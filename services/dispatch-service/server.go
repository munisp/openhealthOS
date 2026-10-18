package main

// HTTP surface + integrations for the dispatcher: SOS intake, the
// WhatsApp Cloud API webhook, facility assignment over the registry,
// lifecycle transitions, and the domain-event rail that keeps caller
// and facility informed.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// DedupeWindow: repeat contact from the same caller inside this window
// updates the open incident instead of cloning it.
const DedupeWindow = 10 * time.Minute

// FacilityMatch is the registry's nearest-facility answer.
type FacilityMatch struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	Level      string   `json:"level"`
	Hours      string   `json:"hours"`
	Services   []string `json:"services"`
	DistanceKm float64  `json:"distance_km"`
}

type Integrations struct {
	registryURL string
	notifyURL   string
	client      *http.Client
	protocol    []triageRule
	now         func() time.Time
}

func newIntegrations(getenv func(string) string) Integrations {
	reg := getenv("REGISTRY_URL")
	if reg == "" {
		reg = "http://localhost:8081"
	}
	return Integrations{
		registryURL: reg,
		notifyURL:   getenv("NOTIFY_URL"),
		client:      &http.Client{Timeout: 4 * time.Second},
		protocol:    loadTriageProtocol(getenv),
		now:         time.Now,
	}
}

// fireEvent posts to the notification rail; failures are logged via the
// incident timeline, never fatal — the incident exists whether or not
// the SMS leaves today.
func (ix Integrations) fireEvent(evType string, payload map[string]any) {
	if ix.notifyURL == "" {
		return
	}
	body, _ := json.Marshal(map[string]any{
		"id": payload["event_id"], "facility": payload["facility_id"],
		"type": evType, "ts": ix.now().UTC().Format(time.RFC3339), "payload": payload,
	})
	req, err := http.NewRequest(http.MethodPost, ix.notifyURL+"/events", bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := ix.client.Do(req)
	if err == nil {
		resp.Body.Close()
	}
}

// nearestFacility asks the registry for candidates, preferring the
// emergency amenity, then hospital level, then anything — the first
// answer of the first non-empty tier wins, and the tier used is
// recorded so a downgrade is visible, not silent.
func (ix Integrations) nearestFacility(ctx context.Context, lat, lng float64) (FacilityMatch, string, error) {
	tiers := []struct{ query, label string }{
		{"radius_km=100&amenity=emergency&limit=1", "emergency-amenity"},
		{"radius_km=150&level=hospital&limit=1", "hospital"},
		{"radius_km=200&limit=1", "any"},
	}
	for _, t := range tiers {
		url := fmt.Sprintf("%s/facilities/near?lat=%f&lng=%f&%s", ix.registryURL, lat, lng, t.query)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			continue
		}
		resp, err := ix.client.Do(req)
		if err != nil {
			continue
		}
		var out struct {
			Results []FacilityMatch `json:"results"`
		}
		json.NewDecoder(resp.Body).Decode(&out)
		resp.Body.Close()
		if len(out.Results) > 0 {
			return out.Results[0], t.label, nil
		}
	}
	return FacilityMatch{}, "", fmt.Errorf("no facility within reach of (%.4f, %.4f)", lat, lng)
}

type server struct {
	store CaseStore
	ix    Integrations
	fleet *FleetStore
	mci   *MCIStore
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	case errors.Is(err, ErrBadTransition), errors.Is(err, ErrValidation):
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
	default:
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
	}
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /sos", s.sos)
	mux.HandleFunc("GET /webhooks/whatsapp", s.whatsappVerify)
	mux.HandleFunc("POST /webhooks/whatsapp", s.whatsappInbound)
	mux.HandleFunc("GET /incidents", s.listIncidents)
	mux.HandleFunc("GET /incidents/{id}", s.getIncident)
	mux.HandleFunc("POST /incidents/{id}/assign", s.assign)
	mux.HandleFunc("POST /incidents/{id}/transition", s.transition)
	s.fleetRoutes(mux)
	s.mciRoutes(mux)
	mux.HandleFunc("GET /contacts", s.listContacts)
	mux.HandleFunc("GET /contacts/{phone}", s.getContact)
	mux.HandleFunc("POST /contacts/{phone}/consent", s.setConsent)
	mux.HandleFunc("POST /contacts/{phone}/interactions", s.addInteraction)
	mux.HandleFunc("GET /cases", s.listCases)
	mux.HandleFunc("GET /cases/{id}", s.getCase)
	mux.HandleFunc("POST /cases/{id}/transition", s.caseTransition)
	mux.HandleFunc("POST /cases/{id}/tasks", s.addTask)
	mux.HandleFunc("POST /tasks/{id}/done", s.taskDone)
	mux.HandleFunc("GET /tasks/overdue", s.overdueTasks)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

// intake is the shared core: dedupe, triage, assign, fire events.
func (s server) intake(channel, phone, patientID string, lat, lng float64, locSource, complaint string) (Incident, bool, error) {
	if phone == "" && patientID == "" {
		return Incident{}, false, fmt.Errorf("%w: caller phone or patient_id required", ErrValidation)
	}
	if strings.TrimSpace(complaint) == "" {
		return Incident{}, false, fmt.Errorf("%w: complaint required", ErrValidation)
	}
	now := s.ix.now().UTC()
	// Repeat-SOS dedupe: same caller, open incident, inside the window.
	if phone != "" {
		if open, err := s.store.OpenForCaller(phone, now.Add(-DedupeWindow)); err == nil {
			open.Complaint = complaint // latest description wins
			if lat != 0 || lng != 0 {
				open.Lat, open.Lng, open.LocSource = lat, lng, locSource
			}
			open.UpdatedAt = now.Format(time.RFC3339)
			open.Timeline = append(open.Timeline, TimelineEntry{
				TS: open.UpdatedAt, Actor: "service", Action: "repeat-contact",
				Detail: "caller contacted again inside dedupe window",
			})
			// A location that arrives after the text (the WhatsApp
			// pattern) completes the incident: assign and dispatch now.
			if open.FacilityID == "" && (open.Lat != 0 || open.Lng != 0) {
				if f, tier, err := s.ix.nearestFacility(context.Background(), open.Lat, open.Lng); err == nil {
					open.FacilityID, open.FacilityName, open.FacilityKm = f.ID, f.Name, f.DistanceKm
					if open.Status == StReceived || open.Status == StTriaged {
						open.Status = StDispatched
					}
					open.Timeline = append(open.Timeline, TimelineEntry{
						TS: open.UpdatedAt, Actor: "service", Action: "dispatched",
						Detail: fmt.Sprintf("%s (%s, %.1fkm, tier %s)", f.Name, f.ID, f.DistanceKm, tier),
					})
				}
			}
			_ = s.store.Put(open)
			return open, false, nil
		}
	}
	cat, reason := triage(s.ix.protocol, complaint)
	inc := Incident{
		ID: s.store.NextID(), Channel: channel, CallerPhone: phone,
		PatientID: patientID, Lat: lat, Lng: lng, LocSource: locSource,
		Complaint: complaint, Triage: cat, TriageReason: reason,
		Status: StReceived, CreatedAt: now.Format(time.RFC3339), UpdatedAt: now.Format(time.RFC3339),
	}
	inc.Timeline = append(inc.Timeline,
		TimelineEntry{TS: inc.CreatedAt, Actor: "service", Action: "received", Detail: channel},
		TimelineEntry{TS: inc.CreatedAt, Actor: "service", Action: "triaged",
			Detail: cat + " — " + reason})
	inc.Status = StTriaged

	// Assign the nearest appropriate facility when we have coordinates.
	if lat != 0 || lng != 0 {
		if f, tier, err := s.ix.nearestFacility(context.Background(), lat, lng); err == nil {
			inc.FacilityID, inc.FacilityName, inc.FacilityKm = f.ID, f.Name, f.DistanceKm
			inc.Status = StDispatched
			inc.Timeline = append(inc.Timeline, TimelineEntry{
				TS: inc.UpdatedAt, Actor: "service", Action: "dispatched",
				Detail: fmt.Sprintf("%s (%s, %.1fkm, tier %s)", f.Name, f.ID, f.DistanceKm, tier),
			})
		} else {
			inc.Timeline = append(inc.Timeline, TimelineEntry{
				TS: inc.UpdatedAt, Actor: "service", Action: "awaiting-dispatch",
				Detail: err.Error(),
			})
		}
	} else {
		inc.Timeline = append(inc.Timeline, TimelineEntry{
			TS: inc.UpdatedAt, Actor: "service", Action: "awaiting-location",
			Detail: "no coordinates — dispatcher must call back or assign manually",
		})
	}
	// A dispatched incident with coordinates gets the nearest available
	// unit automatically — the uber-like default; dispatchers override.
	if inc.Status == StDispatched && s.fleet != nil {
		if u, km, err := s.fleet.NearestAvailable(inc.Lat, inc.Lng); err == nil {
			_, _ = s.fleet.SetStatus(u.ID, UnitEnRoute, inc.ID, now)
			inc.UnitID = u.ID
			inc.Timeline = append(inc.Timeline, TimelineEntry{
				TS: inc.UpdatedAt, Actor: "service", Action: "unit-dispatched",
				Detail: fmt.Sprintf("%s (%s, %.1fkm)", u.Callsign, u.ID, km),
			})
		}
	}
	if err := s.store.Put(inc); err != nil {
		return Incident{}, false, err
	}
	s.openCaseForIntake(inc)
	go s.ix.fireEvent("emergency_dispatch", map[string]any{
		"event_id": "dispatch-" + inc.ID, "facility_id": inc.FacilityID,
		"patient_id": inc.PatientID, "caller_phone": inc.CallerPhone,
		"facility_name": inc.FacilityName, "triage": inc.Triage,
		"when_text": inc.CreatedAt, "incident_id": inc.ID,
	})
	return inc, true, nil
}

func (s server) sos(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Channel   string  `json:"channel"`
		Phone     string  `json:"phone"`
		PatientID string  `json:"patient_id"`
		Lat       float64 `json:"lat"`
		Lng       float64 `json:"lng"`
		Complaint string  `json:"complaint"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	if req.Channel == "" {
		req.Channel = "app"
	}
	locSource := "app-gps"
	if req.Channel != "app" {
		locSource = "cell-tower"
	}
	inc, created, err := s.intake(req.Channel, req.Phone, req.PatientID, req.Lat, req.Lng, locSource, req.Complaint)
	if err != nil {
		fail(w, err)
		return
	}
	if created {
		writeJSON(w, http.StatusCreated, inc)
	} else {
		writeJSON(w, 200, inc)
	}
}

// WhatsApp Cloud API verification handshake: GET with hub.verify_token.
func (s server) whatsappVerify(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	want := os.Getenv("WHATSAPP_VERIFY_TOKEN")
	if want == "" {
		http.Error(w, `{"error":"WHATSAPP_VERIFY_TOKEN unset"}`, http.StatusServiceUnavailable)
		return
	}
	if q.Get("hub.mode") == "subscribe" && q.Get("hub.verify_token") == want {
		w.Write([]byte(q.Get("hub.challenge")))
		return
	}
	http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
}

// whatsappInbound parses the Cloud API webhook shape: text messages and
// location pins become SOS intakes. The "one number" a whole state can
// be taught to message.
func (s server) whatsappInbound(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		Entry []struct {
			Changes []struct {
				Value struct {
					Messages []struct {
						From string `json:"from"`
						Type string `json:"type"`
						Text *struct {
							Body string `json:"body"`
						} `json:"text"`
						Location *struct {
							Lat float64 `json:"latitude"`
							Lng float64 `json:"longitude"`
						} `json:"location"`
					} `json:"messages"`
				} `json:"value"`
			} `json:"changes"`
		} `json:"entry"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		// WhatsApp retries on non-200; a malformed body must still 200
		// or the channel loops forever. Ack and drop.
		writeJSON(w, 200, map[string]any{"received": false})
		return
	}
	created := 0
	for _, e := range payload.Entry {
		for _, c := range e.Changes {
			for _, m := range c.Value.Messages {
				complaint, lat, lng, loc := "", 0.0, 0.0, ""
				switch m.Type {
				case "text":
					if m.Text != nil {
						complaint = m.Text.Body
					}
				case "location":
					if m.Location != nil {
						lat, lng = m.Location.Lat, m.Location.Lng
						complaint = "shared their location — emergency (no text)"
						loc = "whatsapp-pin"
					}
				default:
					continue // images, stickers, statuses: not SOS intake
				}
				if complaint == "" {
					continue
				}
				// A bare location follows a text: attach to the open
				// incident via dedupe rather than inventing a second one.
				if _, isNew, err := s.intake("whatsapp", m.From, "", lat, lng, loc, complaint); err == nil && isNew {
					created++
				}
			}
		}
	}
	writeJSON(w, 200, map[string]any{"received": true, "incidents_created": created})
}

func (s server) listIncidents(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	writeJSON(w, 200, map[string]any{"incidents": s.store.List(q.Get("status"), q.Get("facility_id"))})
}

func (s server) getIncident(w http.ResponseWriter, r *http.Request) {
	inc, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, inc)
}

// assign is the human dispatcher's override — manual beats algorithm,
// and the timeline says who.
func (s server) assign(w http.ResponseWriter, r *http.Request) {
	var req struct {
		FacilityID   string  `json:"facility_id"`
		FacilityName string  `json:"facility_name"`
		DistanceKm   float64 `json:"distance_km"`
		Actor        string  `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.FacilityID == "" || req.Actor == "" {
		fail(w, fmt.Errorf("%w: facility_id and actor required", ErrValidation))
		return
	}
	inc, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	if inc.Status == StClosed || inc.Status == StCancelled {
		fail(w, ErrBadTransition)
		return
	}
	inc.FacilityID, inc.FacilityName, inc.FacilityKm = req.FacilityID, req.FacilityName, req.DistanceKm
	if inc.Status == StReceived || inc.Status == StTriaged {
		inc.Status = StDispatched
	}
	inc.UpdatedAt = s.ix.now().UTC().Format(time.RFC3339)
	inc.Timeline = append(inc.Timeline, TimelineEntry{
		TS: inc.UpdatedAt, Actor: req.Actor, Action: "assigned",
		Detail: "manual dispatch to " + req.FacilityID,
	})
	_ = s.store.Put(inc)
	go s.ix.fireEvent("emergency_dispatch", map[string]any{
		"event_id": "dispatch-assign-" + inc.ID, "facility_id": inc.FacilityID,
		"caller_phone": inc.CallerPhone, "facility_name": inc.FacilityName,
		"triage": inc.Triage, "incident_id": inc.ID,
	})
	writeJSON(w, 200, inc)
}

func (s server) transition(w http.ResponseWriter, r *http.Request) {
	var req struct {
		To    string `json:"to"`
		Actor string `json:"actor"`
		Note  string `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.To == "" || req.Actor == "" {
		fail(w, fmt.Errorf("%w: to and actor required", ErrValidation))
		return
	}
	inc, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	legal := false
	for _, t := range transitions[inc.Status] {
		if t == req.To {
			legal = true
			break
		}
	}
	if !legal {
		fail(w, fmt.Errorf("%w: %s -> %s", ErrBadTransition, inc.Status, req.To))
		return
	}
	inc.Status = req.To
	inc.UpdatedAt = s.ix.now().UTC().Format(time.RFC3339)
	inc.Timeline = append(inc.Timeline, TimelineEntry{
		TS: inc.UpdatedAt, Actor: req.Actor, Action: req.To, Detail: req.Note,
	})
	_ = s.store.Put(inc)
	if inc.UnitID != "" && s.fleet != nil {
		switch req.To {
		case StOnScene:
			_, _ = s.fleet.SetStatus(inc.UnitID, UnitOnScene, inc.ID, s.ix.now())
		case StTransported:
			_, _ = s.fleet.SetStatus(inc.UnitID, UnitTransporting, inc.ID, s.ix.now())
		case StClosed, StCancelled:
			_, _ = s.fleet.SetStatus(inc.UnitID, UnitAvailable, "", s.ix.now())
		}
	}
	if req.To == StClosed {
		s.scheduleFollowUp(inc)
	}
	go s.ix.fireEvent("dispatch_update", map[string]any{
		"event_id": "dispatch-" + req.To + "-" + inc.ID, "facility_id": inc.FacilityID,
		"caller_phone": inc.CallerPhone, "facility_name": inc.FacilityName,
		"incident_id": inc.ID, "status": req.To,
	})
	writeJSON(w, 200, inc)
}
