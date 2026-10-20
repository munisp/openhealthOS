// Package main — OpenHealth OS equipment registry & maintenance tickets
// (module 9, infrastructure layer).
//
// 50–80% of medical equipment in developing countries is non-functional —
// a maintenance crisis, not a procurement one. This service tracks the
// equipment registry per facility, routes maintenance tickets to vetted
// BMET (biomedical technician) marketplace members, and screens donations
// at intake.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Equipment is one registered asset at a facility.
type Equipment struct {
	ID       string `json:"id"`
	Facility string `json:"facility"`
	Type     string `json:"type"` // e.g. autoclave, oxygen-concentrator, fridge
	Model    string `json:"model"`
	Status   string `json:"status"` // working | down | in-maintenance | condemned
	Donated  bool   `json:"donated"`
	IntakeAt string `json:"intake_at"`
	Location string `json:"location,omitempty"` // ward/room — set by tag scans (ADR-0052)
}

// Ticket is one maintenance work order.
type Ticket struct {
	ID          string `json:"id"`
	EquipmentID string `json:"equipment_id"`
	Issue       string `json:"issue"`
	Priority    string `json:"priority"` // P1 safety-critical | P2 care-blocking | P3 routine
	Status      string `json:"status"`   // open | assigned | resolved
	Technician  string `json:"technician,omitempty"`
	OpenedAt    string `json:"opened_at"`
	ResolvedAt  string `json:"resolved_at,omitempty"`
}

// Technician is a vetted BMET marketplace member.
type Technician struct {
	ID      string   `json:"id"`
	Name    string   `json:"name"`
	Vetted  bool     `json:"vetted"` // marketplace gate: only vetted techs get tickets
	Regions []string `json:"regions"`
}

var (
	ErrNotFound       = errors.New("not found")
	ErrBadTransition  = errors.New("invalid ticket state transition")
	ErrUnvettedTech   = errors.New("technician is not vetted — marketplace gate")
	ErrDonationReject = errors.New("donation failed intake screening")
)

// Service holds the registry, tickets and marketplace directory.
type Service struct {
	mu      sync.Mutex
	equip   map[string]*Equipment
	tickets map[string]*Ticket
	techs   map[string]*Technician
	custody []CustodyEvent // scan audit spine (ADR-0052)
	seq     int
	now     func() time.Time
}

func NewService() *Service {
	return &Service{
		equip: map[string]*Equipment{}, tickets: map[string]*Ticket{},
		techs: map[string]*Technician{}, now: time.Now,
	}
}

// DonationScreen applies intake rules: donations must be working or
// economically repairable, have consumables available locally, and be
// electrically compatible (220V/50Hz or dual-voltage) — the screening that
// keeps dead-on-arrival equipment out of the registry.
func DonationScreen(e Equipment, consumablesLocal, voltageOK bool) error {
	if e.Status == "condemned" {
		return fmt.Errorf("%w: condemned at source", ErrDonationReject)
	}
	if !consumablesLocal {
		return fmt.Errorf("%w: consumables unavailable locally", ErrDonationReject)
	}
	if !voltageOK {
		return fmt.Errorf("%w: incompatible power supply", ErrDonationReject)
	}
	return nil
}

func (s *Service) Register(e Equipment) (Equipment, error) {
	if e.Facility == "" || e.Type == "" {
		return Equipment{}, errors.New("facility and type are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	if e.ID == "" {
		e.ID = fmt.Sprintf("EQ-%06d", s.seq)
	}
	if e.Status == "" {
		e.Status = "working"
	}
	e.IntakeAt = s.now().UTC().Format(time.RFC3339)
	s.equip[e.ID] = &e
	return e, nil
}

func (s *Service) AddTechnician(t Technician) (Technician, error) {
	if t.ID == "" || t.Name == "" {
		return Technician{}, errors.New("id and name are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.techs[t.ID] = &t
	return t, nil
}

// OpenTicket creates a work order and marks the equipment down.
func (s *Service) OpenTicket(equipmentID, issue, priority string) (Ticket, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.equip[equipmentID]
	if !ok {
		return Ticket{}, ErrNotFound
	}
	s.seq++
	t := &Ticket{
		ID: fmt.Sprintf("MT-%06d", s.seq), EquipmentID: equipmentID,
		Issue: issue, Priority: priority, Status: "open",
		OpenedAt: s.now().UTC().Format(time.RFC3339),
	}
	s.tickets[t.ID] = t
	e.Status = "down"
	return *t, nil
}

// Assign routes a ticket to a vetted technician.
func (s *Service) Assign(ticketID, techID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tickets[ticketID]
	if !ok {
		return ErrNotFound
	}
	if t.Status != "open" {
		return ErrBadTransition
	}
	tech, ok := s.techs[techID]
	if !ok {
		return ErrNotFound
	}
	if !tech.Vetted {
		return ErrUnvettedTech
	}
	t.Status = "assigned"
	t.Technician = techID
	return nil
}

// Resolve closes a ticket and returns the equipment to service.
func (s *Service) Resolve(ticketID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tickets[ticketID]
	if !ok {
		return ErrNotFound
	}
	if t.Status != "assigned" {
		return ErrBadTransition
	}
	t.Status = "resolved"
	t.ResolvedAt = s.now().UTC().Format(time.RFC3339)
	if e, ok := s.equip[t.EquipmentID]; ok {
		e.Status = "working"
	}
	return nil
}

// AutoResolve closes an open telemetry-raised (AUTO:) ticket on sensor
// recovery — the recovery reading IS the closure evidence. Human tickets
// still require assignment first (marketplace accountability).
func (s *Service) AutoResolve(ticketID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tickets[ticketID]
	if !ok {
		return ErrNotFound
	}
	if t.Status != "open" || !strings.HasPrefix(t.Issue, "AUTO:") {
		return ErrBadTransition
	}
	t.Status = "resolved"
	t.Technician = "auto:telemetry"
	t.ResolvedAt = s.now().UTC().Format(time.RFC3339)
	if e, ok := s.equip[t.EquipmentID]; ok {
		e.Status = "working"
	}
	return nil
}

func (s *Service) GetEquipment(id string) (Equipment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.equip[id]
	if !ok {
		return Equipment{}, ErrNotFound
	}
	return *e, nil
}

func (s *Service) ListTickets(status string) []Ticket {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []Ticket{}
	for _, t := range s.tickets {
		if status == "" || t.Status == status {
			out = append(out, *t)
		}
	}
	return out
}

// EquipmentService is the asset/ticket lifecycle seam: the in-memory
// Service (edge, tests) and PostgresService (state plane) both satisfy it.
type EquipmentService interface {
	Register(e Equipment) (Equipment, error)
	AddTechnician(t Technician) (Technician, error)
	OpenTicket(equipmentID, issue, priority string) (Ticket, error)
	Assign(ticketID, techID string) error
	Resolve(ticketID string) error
	AutoResolve(ticketID string) error
	GetEquipment(id string) (Equipment, error)
	ListTickets(status string) []Ticket
	// ADR-0052: signed tag scans + custody spine.
	Scan(tag, action, actor, location, notes string) (CustodyEvent, error)
	CustodyLog(assetID string) []CustodyEvent
	Now() time.Time
}

// Now exposes the service clock (tests pin it).
func (s *Service) Now() time.Time { return s.now() }

var _ EquipmentService = (*Service)(nil)

// ---- HTTP surface ----

type server struct{ svc EquipmentService }

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/equipment", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var e Equipment
		if err := json.NewDecoder(r.Body).Decode(&e); err != nil {
			http.Error(w, "bad equipment", http.StatusBadRequest)
			return
		}
		e, err := s.svc.Register(e)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, e)
	})
	mux.HandleFunc("/technicians", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var t Technician
		if err := json.NewDecoder(r.Body).Decode(&t); err != nil {
			http.Error(w, "bad technician", http.StatusBadRequest)
			return
		}
		t, err := s.svc.AddTechnician(t)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, t)
	})
	mux.HandleFunc("/tickets", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, 200, s.svc.ListTickets(r.URL.Query().Get("status")))
		case http.MethodPost:
			var req struct {
				EquipmentID string `json:"equipment_id"`
				Issue       string `json:"issue"`
				Priority    string `json:"priority"`
			}
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				http.Error(w, "bad ticket", http.StatusBadRequest)
				return
			}
			t, err := s.svc.OpenTicket(req.EquipmentID, req.Issue, req.Priority)
			if err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, 201, t)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/tickets/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		path := strings.TrimPrefix(r.URL.Path, "/tickets/")
		var err error
		if strings.HasSuffix(path, "/assign") {
			var body struct {
				TechnicianID string `json:"technician_id"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			err = s.svc.Assign(strings.TrimSuffix(path, "/assign"), body.TechnicianID)
		} else if strings.HasSuffix(path, "/resolve") {
			err = s.svc.Resolve(strings.TrimSuffix(path, "/resolve"))
		} else {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		switch {
		case errors.Is(err, ErrNotFound):
			http.Error(w, "not found", http.StatusNotFound)
		case err != nil:
			http.Error(w, err.Error(), http.StatusConflict)
		default:
			writeJSON(w, 200, map[string]string{"status": "ok"})
		}
	})
	readingRoute(mux, s.svc)
	// Asset tags + scans (ADR-0052): print a signed QR for an asset,
	// then scan it to locate / transfer / flag maintenance. The tag is
	// HMAC-verified offline; every scan lands on the custody spine.
	mux.HandleFunc("POST /equipment/{id}/tag", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		eq, err := s.svc.GetEquipment(id)
		if err != nil {
			http.Error(w, `{"error":"unknown equipment"}`, http.StatusNotFound)
			return
		}
		tag, err := MintTag(eq.Facility, eq.ID, s.svc.Now())
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusServiceUnavailable)
			return
		}
		writeJSON(w, 201, map[string]string{"equipment_id": id, "tag": tag})
	})
	mux.HandleFunc("POST /scan", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Tag      string `json:"tag"`
			Action   string `json:"action"`
			Actor    string `json:"actor"`
			Location string `json:"location"`
			Notes    string `json:"notes"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
			return
		}
		ev, err := s.svc.Scan(body.Tag, body.Action, body.Actor, body.Location, body.Notes)
		if err != nil {
			code := http.StatusBadRequest
			if errors.Is(err, ErrTagDisabled) {
				code = http.StatusServiceUnavailable
			} else if errors.Is(err, ErrNotFound) {
				code = http.StatusNotFound
			}
			http.Error(w, `{"error":"`+err.Error()+`"}`, code)
			return
		}
		writeJSON(w, 200, ev)
	})
	mux.HandleFunc("GET /custody", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"events": s.svc.CustodyLog(r.URL.Query().Get("asset"))})
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
