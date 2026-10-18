// HTTP surface for the notification service.
//
//	POST /subscriptions          — register/update a patient's phone + language
//	GET  /subscriptions          — list (admin/ops)
//	POST /events                 — domain event intake (renders + enqueues)
//	POST /notify                 — direct enqueue (services that compose their own text)
//	POST /drain                  — deliver pending through the provider (cron/loop)
//	GET  /notifications?patient= — patient or ops view of delivery states
//	GET  /stats                  — queued/sent/failed counts (+ provider name)
//	GET  /healthz
//
// The drain loop also runs in-process every DRAIN_INTERVAL_SECONDS
// (default 30) so the rail works without external schedulers; the POST
// endpoint exists for ops forcing and tests.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
)

type server struct {
	svc *Service
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
		return false
	}
	return true
}

func fail(w http.ResponseWriter, err error) {
	switch err {
	case ErrNotFound:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	case ErrNoPhone:
		http.Error(w, `{"error":"patient has no phone on file"}`, http.StatusUnprocessableEntity)
	case ErrBadTransition:
		http.Error(w, `{"error":"bad state transition"}`, http.StatusConflict)
	default:
		http.Error(w, `{"error":"internal"}`, http.StatusInternalServerError)
	}
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("POST /subscriptions", func(w http.ResponseWriter, r *http.Request) {
		var sub Subscription
		if !decode(w, r, &sub) {
			return
		}
		if sub.PatientID == "" || sub.Phone == "" {
			http.Error(w, `{"error":"patient_id and phone required"}`, http.StatusBadRequest)
			return
		}
		sub.Phone = NormalizePhone(sub.Phone)
		if sub.Language == "" {
			sub.Language = "en"
		}
		sub.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
		if err := s.svc.store.PutSubscription(sub); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusCreated, sub)
	})

	mux.HandleFunc("GET /subscriptions", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, s.svc.store.Subscriptions())
	})

	mux.HandleFunc("POST /events", func(w http.ResponseWriter, r *http.Request) {
		var ev DomainEvent
		if !decode(w, r, &ev) {
			return
		}
		n, err := s.svc.HandleEvent(ev)
		switch {
		case err == ErrNoPhone:
			// Not an error for the sender: patient simply has no number.
			writeJSON(w, http.StatusAccepted, map[string]string{"status": "skipped", "reason": "no subscription"})
		case err != nil:
			fail(w, err)
		case n.ID == "":
			writeJSON(w, http.StatusAccepted, map[string]string{"status": "ignored", "reason": "unknown event type"})
		default:
			writeJSON(w, http.StatusCreated, n)
		}
	})

	mux.HandleFunc("POST /notify", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			PatientID string `json:"patient_id"`
			Kind      string `json:"kind"`
			Text      string `json:"text"`
		}
		if !decode(w, r, &body) || body.PatientID == "" || body.Text == "" {
			http.Error(w, `{"error":"patient_id and text required"}`, http.StatusBadRequest)
			return
		}
		sub, err := s.svc.store.Subscription(body.PatientID)
		if err != nil {
			fail(w, ErrNoPhone)
			return
		}
		n := Notification{
			ID: s.svc.nextID(), PatientID: body.PatientID, Phone: sub.Phone,
			Kind: body.Kind, Text: body.Text, State: StateQueued,
			CreatedAt: s.svc.now().UTC().Format(time.RFC3339),
		}
		if err := s.svc.store.Enqueue(n); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusCreated, n)
	})

	mux.HandleFunc("POST /drain", func(w http.ResponseWriter, r *http.Request) {
		limit := 100
		if v := r.URL.Query().Get("limit"); v != "" {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				limit = n
			}
		}
		sent, failed := s.svc.Drain(limit)
		writeJSON(w, http.StatusOK, map[string]int{"sent": sent, "failed": failed})
	})

	mux.HandleFunc("GET /notifications", func(w http.ResponseWriter, r *http.Request) {
		patient := r.URL.Query().Get("patient")
		if patient == "" {
			http.Error(w, `{"error":"patient query param required"}`, http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, s.svc.store.ForPatient(patient))
	})

	// Patient portal feed: the patient PWA authenticates with its
	// patient code + phone number. A mismatch returns 404 — never reveal
	// whether a patient id exists.
	mux.HandleFunc("GET /portal/feed", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		phone := NormalizePhone(r.URL.Query().Get("phone"))
		sub, err := s.svc.store.Subscription(pid)
		if err != nil || sub.Phone != phone || pid == "" || phone == "" {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"patient_id":    pid,
			"language":      sub.Language,
			"notifications": s.svc.store.ForPatient(pid),
		})
	})

	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, _ *http.Request) {
		q, snt, f := s.svc.store.Stats()
		writeJSON(w, http.StatusOK, map[string]any{
			"queued": q, "sent": snt, "failed": f, "provider": s.svc.provider.Name(),
		})
	})

	// Ops view of the failover chain: breaker state + counters per leg.
	mux.HandleFunc("GET /providers", func(w http.ResponseWriter, _ *http.Request) {
		if chain, ok := s.svc.provider.(*ChainProvider); ok {
			writeJSON(w, http.StatusOK, map[string]any{"legs": chain.Status()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"legs": []LegStatus{{Name: s.svc.provider.Name(), State: "closed"}},
		})
	})

	registerFHIRInbox(mux, s.svc)

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok"))
	})
	return mux
}

func main() {
	var store Store = NewMemoryStore()
	if os.Getenv("DATABASE_URL") != "" {
		pg, err := NewPostgresStore(os.Getenv("DATABASE_URL"))
		if err != nil {
			panic(err)
		}
		store = pg
	}

	provider := providerFromEnv()

	svc := NewService(store, provider)

	// in-process drain loop — the rail delivers without external cron
	interval := 30 * time.Second
	if v := os.Getenv("DRAIN_INTERVAL_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 1 {
			interval = time.Duration(n) * time.Second
		}
	}
	go func() {
		for range time.Tick(interval) {
			sent, failed := svc.Drain(200)
			if sent+failed > 0 {
				log.Printf("notification-service: drained sent=%d failed=%d", sent, failed)
			}
		}
	}()

	addr := ":8089"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	var handler http.Handler = server{svc: svc}.routes()
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
	}
	log.Printf("notification-service: listening on %s (provider: %s)", addr, provider.Name())
	if err := servex.ListenAndServe(addr, handler, servex.Options{}); err != nil {
		panic(err)
	}
}
