// server_main.go — HTTP surface, notification rail wiring, advisory
// coverage snapshots (ADR-0017).
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"time"
)

type server struct {
	store Store
	now   func() time.Time
	ix    Integrations
}

// Integrations: the rail and the coverage check. Both optional —
// an edge node books appointments fine without either; the rail and
// the coverage snapshot catch up when the uplink does.
type Integrations struct {
	notifyURL      string // e.g. http://notification-service:8089
	eligibilityURL string // e.g. http://claims-orchestrator:8080
	client         *http.Client
}

func IntegrationsFromEnv(getenv func(string) string) Integrations {
	return Integrations{
		notifyURL:      getenv("NOTIFY_URL"),
		eligibilityURL: getenv("ELIGIBILITY_URL"),
		client:         &http.Client{Timeout: 3 * time.Second},
	}
}

// fireEvent posts to the notification rail; failures are logged, never
// fatal — the appointment exists whether or not the SMS leaves today.
func (ix Integrations) fireEvent(evType string, payload map[string]any) {
	if ix.notifyURL == "" {
		return
	}
	body, _ := json.Marshal(map[string]any{
		"id":       payload["event_id"],
		"facility": payload["facility_id"],
		"type":     evType,
		"payload":  payload,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		ix.notifyURL+"/events", bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := ix.client.Do(req)
	if err != nil {
		log.Printf("notify: %v", err)
		return
	}
	resp.Body.Close()
}

// coverageSnapshot takes an advisory eligibility reading at booking
// time. Advisory means: the answer decorates the appointment, it never
// decides whether the appointment exists.
func (ix Integrations) coverageSnapshot(memberID string) map[string]any {
	if ix.eligibilityURL == "" || memberID == "" {
		return nil
	}
	body, _ := json.Marshal(map[string]string{
		"member_id":    memberID,
		"service_date": time.Now().UTC().Format("2006-01-02"),
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		ix.eligibilityURL+"/eligibility", bytes.NewReader(body))
	if err != nil {
		return nil
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := ix.client.Do(req)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil
	}
	var m map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&m); err != nil {
		return nil
	}
	return m
}

// ---------------------------------------------------------------- http

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	case errors.Is(err, ErrSlotFull):
		writeJSON(w, http.StatusConflict, map[string]string{"error": ErrSlotFull.Error()})
	case errors.Is(err, ErrSlotPast), errors.Is(err, ErrBadTransition), errors.Is(err, ErrBadSchedule):
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
	default:
		http.Error(w, `{"error":"internal"}`, http.StatusInternalServerError)
	}
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
		return false
	}
	return true
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /schedules", s.putSchedule)
	mux.HandleFunc("GET /schedules", s.listSchedules)
	mux.HandleFunc("POST /slots/generate", s.generate)
	mux.HandleFunc("GET /slots", s.listSlots)
	mux.HandleFunc("GET /slots/recommend", s.recommendSlots) // smart slot recommender (innovation 4)
	mux.HandleFunc("POST /appointments", s.book)
	mux.HandleFunc("GET /appointments", s.listAppointments)
	mux.HandleFunc("GET /appointments/{id}", s.getAppointment)
	mux.HandleFunc("POST /appointments/{id}/cancel", s.cancel)
	mux.HandleFunc("POST /appointments/{id}/reschedule", s.reschedule)
	mux.HandleFunc("POST /appointments/{id}/checkin", s.transition("checkin"))
	mux.HandleFunc("POST /appointments/{id}/complete", s.transition("complete"))
	mux.HandleFunc("POST /appointments/{id}/noshow", s.transition("noshow"))
	mux.HandleFunc("POST /waitlist", s.joinWaitlist)
	mux.HandleFunc("GET /waitlist", s.listWaitlist)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func (s server) putSchedule(w http.ResponseWriter, r *http.Request) {
	var sch Schedule
	if !decode(w, r, &sch) {
		return
	}
	out, err := s.store.PutSchedule(sch)
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, out)
}

func (s server) listSchedules(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"schedules": s.store.Schedules(r.URL.Query().Get("facility_id")),
	})
}

func (s server) generate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ScheduleID string `json:"schedule_id"`
		Date       string `json:"date"`
	}
	if !decode(w, r, &req) {
		return
	}
	slots, err := s.store.Generate(req.ScheduleID, req.Date, s.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{"slots": slots})
}

func (s server) listSlots(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	writeJSON(w, 200, map[string]any{
		"slots": s.store.Slots(q.Get("facility_id"), q.Get("service"), q.Get("from"), q.Get("to")),
	})
}

func (s server) book(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SlotID    string `json:"slot_id"`
		PatientID string `json:"patient_id"`
		MemberID  string `json:"member_id,omitempty"` // for the advisory coverage snapshot
		Reason    string `json:"reason,omitempty"`
	}
	if !decode(w, r, &req) || req.SlotID == "" || req.PatientID == "" {
		http.Error(w, `{"error":"slot_id and patient_id required"}`, http.StatusBadRequest)
		return
	}
	a, err := s.store.Book(req.SlotID, req.PatientID, req.Reason,
		s.ix.coverageSnapshot(req.MemberID), s.now())
	if err != nil {
		fail(w, err)
		return
	}
	go s.ix.fireEvent("appointment", map[string]any{
		"event_id": "sched-" + a.ID, "facility_id": a.FacilityID,
		"patient_id": a.PatientID, "facility_name": a.FacilityID,
		"when_text": a.Start, "service": a.Service,
	})
	writeJSON(w, 201, a)
}

func (s server) listAppointments(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	switch {
	case q.Get("patient_id") != "":
		writeJSON(w, 200, map[string]any{"appointments": s.store.PatientAppointments(q.Get("patient_id"))})
	case q.Get("facility_id") != "" && q.Get("date") != "":
		writeJSON(w, 200, map[string]any{"appointments": s.store.DayList(q.Get("facility_id"), q.Get("date"))})
	default:
		http.Error(w, `{"error":"patient_id or facility_id+date required"}`, http.StatusBadRequest)
	}
}

func (s server) getAppointment(w http.ResponseWriter, r *http.Request) {
	a, err := s.store.Appointment(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, a)
}

func (s server) cancel(w http.ResponseWriter, r *http.Request) {
	cancelled, promoted, err := s.store.Cancel(r.PathValue("id"), s.now())
	if err != nil {
		fail(w, err)
		return
	}
	go s.ix.fireEvent("appointment_cancelled", map[string]any{
		"event_id": "sched-cancel-" + cancelled.ID, "facility_id": cancelled.FacilityID,
		"patient_id": cancelled.PatientID, "facility_name": cancelled.FacilityID,
		"when_text": cancelled.Start,
	})
	if promoted != nil {
		go s.ix.fireEvent("appointment", map[string]any{
			"event_id": "sched-" + promoted.ID, "facility_id": promoted.FacilityID,
			"patient_id": promoted.PatientID, "facility_name": promoted.FacilityID,
			"when_text": promoted.Start, "service": promoted.Service,
		})
	}
	writeJSON(w, 200, map[string]any{"cancelled": cancelled, "promoted": promoted})
}

func (s server) reschedule(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SlotID string `json:"slot_id"`
	}
	if !decode(w, r, &req) {
		return
	}
	a, err := s.store.Reschedule(r.PathValue("id"), req.SlotID, s.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, a)
}

func (s server) transition(action string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		a, err := s.store.Transition(r.PathValue("id"), action)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 200, a)
	}
}

func (s server) joinWaitlist(w http.ResponseWriter, r *http.Request) {
	var req struct {
		PatientID  string `json:"patient_id"`
		FacilityID string `json:"facility_id"`
		Service    string `json:"service"`
	}
	if !decode(w, r, &req) {
		return
	}
	e, err := s.store.Waitlist(req.PatientID, req.FacilityID, req.Service, s.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, e)
}

func (s server) listWaitlist(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	writeJSON(w, 200, map[string]any{
		"waitlist": s.store.WaitlistFor(q.Get("facility_id"), q.Get("service")),
	})
}

func main() {
	addr := ":8092"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	var store Store = NewMemoryStore()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresStore(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		store = pg
		log.Print("scheduling storage: postgres (state plane)")
	} else {
		log.Print("scheduling storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{store: store, now: time.Now, ix: IntegrationsFromEnv(os.Getenv)}
	log.Printf("OpenHealth OS scheduling-service on %s", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
