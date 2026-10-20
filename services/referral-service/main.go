// referral-service (innovation 9): closed-loop referral tracking.
//
// Today a referral in OpenHealth is a message — sent and forgotten. The
// patient who never arrives is invisible; the receiving facility that
// never acknowledges is unaccountable. This service makes every referral
// a state machine with an SLA:
//
//	OPEN → ACCEPTED → COMPLETED   (happy path)
//	  │        └── CANCELLED
//	  └── CANCELLED / BREACHED (SLA sweep)
//
// An SLA sweeper marks referrals whose accept/complete deadlines pass
// and fires a notification-service event per breach (fail-soft, like
// every other integration on the platform — the sweep never dies
// because the notifier is down).
//
// Stdlib-only (ADR-0005), in-memory store behind an interface so
// Postgres follows the same shape as the other services.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

type Status string

const (
	StatusOpen      Status = "OPEN"
	StatusAccepted  Status = "ACCEPTED"
	StatusCompleted Status = "COMPLETED"
	StatusCancelled Status = "CANCELLED"
	StatusBreached  Status = "BREACHED"
)

type Referral struct {
	ID           string `json:"id"`
	PatientID    string `json:"patient_id"`
	FromFacility string `json:"from_facility"`
	ToFacility   string `json:"to_facility"`
	Reason       string `json:"reason"`
	Status       Status `json:"status"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
	SLAHours     int    `json:"sla_hours"`
	BreachNotified bool `json:"breach_notified,omitempty"`
}

type Store interface {
	Put(r Referral)
	Get(id string) (Referral, bool)
	All() []Referral
}

type MemoryStore struct {
	mu   sync.Mutex
	rows map[string]Referral
	seq  int
}

func NewMemoryStore() *MemoryStore { return &MemoryStore{rows: map[string]Referral{}} }

func (m *MemoryStore) Put(r Referral) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.ID == "" {
		m.seq++
		r.ID = fmt.Sprintf("REF-%06d", m.seq)
	}
	m.rows[r.ID] = r
}

func (m *MemoryStore) Get(id string) (Referral, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.rows[id]
	return r, ok
}

func (m *MemoryStore) All() []Referral {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Referral, 0, len(m.rows))
	for _, r := range m.rows {
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.Before(out[j].CreatedAt) })
	return out
}

// Notifier posts breach events; nil-safe (offline edge).
type Notifier interface {
	Notify(event string, payload map[string]any)
}

type HTTPNotifier struct {
	URL   string
	Token string
}

func (n HTTPNotifier) Notify(event string, payload map[string]any) {
	body, _ := json.Marshal(map[string]any{"type": event, "payload": payload})
	req, err := http.NewRequest(http.MethodPost, n.URL+"/events", bytes.NewReader(body))
	if err != nil {
		return
	}
	if n.Token != "" {
		req.Header.Set("Authorization", "Bearer "+n.Token)
	}
	req.Header.Set("Content-Type", "application/json")
	cli := &http.Client{Timeout: 3 * time.Second}
	resp, err := cli.Do(req)
	if err != nil {
		log.Printf("referral: notifier unreachable (fail-soft): %v", err)
		return
	}
	resp.Body.Close()
}

type Service struct {
	Store    Store
	Notifier Notifier // may be nil
	Now      func() time.Time
}

func NewService(s Store, n Notifier) *Service {
	return &Service{Store: s, Notifier: n, Now: func() time.Time { return time.Now().UTC() }}
}

// transition enforces the state machine: only legal edges move.
var legal = map[Status][]Status{
	StatusOpen:     {StatusAccepted, StatusCancelled},
	StatusAccepted: {StatusCompleted, StatusCancelled},
}

func (svc *Service) transition(id string, to Status) (Referral, error) {
	r, ok := svc.Store.Get(id)
	if !ok {
		return r, fmt.Errorf("not found")
	}
	for _, allowed := range legal[r.Status] {
		if allowed == to {
			r.Status = to
			r.UpdatedAt = svc.Now()
			svc.Store.Put(r)
			return r, nil
		}
	}
	return r, fmt.Errorf("illegal transition %s → %s", r.Status, to)
}

// Sweep marks SLA breaches and notifies once each. Returns the count
// marked this pass. Deadline: SLA hours from creation while still
// OPEN (accept deadline) or ACCEPTED (complete deadline, double SLA).
func (svc *Service) Sweep() int {
	marked := 0
	for _, r := range svc.Store.All() {
		if r.Status != StatusOpen && r.Status != StatusAccepted {
			continue
		}
		sla := r.SLAHours
		if sla <= 0 {
			sla = 24
		}
		if r.Status == StatusAccepted {
			sla *= 2
		}
		if svc.Now().Before(r.CreatedAt.Add(time.Duration(sla) * time.Hour)) {
			continue
		}
		r.Status = StatusBreached
		r.UpdatedAt = svc.Now()
		if !r.BreachNotified && svc.Notifier != nil {
			svc.Notifier.Notify("referral_breached", map[string]any{
				"referral_id": r.ID, "patient_id": r.PatientID,
				"from_facility": r.FromFacility, "to_facility": r.ToFacility,
				"status": r.Status, "sla_hours": sla,
			})
			r.BreachNotified = true
		}
		svc.Store.Put(r)
		marked++
	}
	return marked
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (svc *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("/referrals", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			var in Referral
			if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
				writeJSON(w, 400, map[string]string{"error": "bad json"})
				return
			}
			if in.PatientID == "" || in.ToFacility == "" {
				writeJSON(w, 400, map[string]string{"error": "patient_id and to_facility required"})
				return
			}
			in.Status = StatusOpen
			in.CreatedAt = svc.Now()
			in.UpdatedAt = in.CreatedAt
			svc.Store.Put(in)
			stored, _ := svc.Store.All(), 0
			_ = stored
			// Put assigns the ID; fetch back the newest for the response.
			all := svc.Store.All()
			writeJSON(w, 201, all[len(all)-1])
		case http.MethodGet:
			status := r.URL.Query().Get("status")
			out := []Referral{}
			for _, ref := range svc.Store.All() {
				if status == "" || string(ref.Status) == status {
					out = append(out, ref)
				}
			}
			writeJSON(w, 200, map[string]any{"referrals": out})
		default:
			writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		}
	})
	mux.HandleFunc("/referrals/", func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/referrals/"), "/")
		if len(parts) != 2 || r.Method != http.MethodPost {
			writeJSON(w, 404, map[string]string{"error": "not found"})
			return
		}
		id, action := parts[0], parts[1]
		targets := map[string]Status{"accept": StatusAccepted, "complete": StatusCompleted, "cancel": StatusCancelled}
		to, ok := targets[action]
		if !ok {
			writeJSON(w, 404, map[string]string{"error": "unknown action"})
			return
		}
		ref, err := svc.transition(id, to)
		if err != nil {
			writeJSON(w, 409, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, ref)
	})
	mux.HandleFunc("/sweep", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]string{"error": "method not allowed"})
			return
		}
		writeJSON(w, 200, map[string]any{"breached": svc.Sweep()})
	})
	return mux
}

func main() {
	addr := ":8094"
	if v := os.Getenv("ADDR"); v != "" {
		addr = v
	}
	var n Notifier
	if u := os.Getenv("NOTIFY_URL"); u != "" {
		n = HTTPNotifier{URL: u, Token: os.Getenv("NOTIFY_TOKEN")}
	}
	svc := NewService(NewMemoryStore(), n)
	go func() { // SLA sweep every minute.
		for range time.Tick(time.Minute) {
			svc.Sweep()
		}
	}()
	log.Printf("referral-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, svc.Handler()))
}
