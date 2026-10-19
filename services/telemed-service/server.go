// HTTP surface for the telemedicine service (ADR-0026).
package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"time"
)

type server struct {
	store     Store
	apiKey    string
	apiSecret string
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("POST /consults", s.createConsult)
	mux.HandleFunc("GET /consults", s.listConsults)
	mux.HandleFunc("GET /consults/{id}", s.getConsult)
	mux.HandleFunc("POST /consults/{id}/transition", s.transition)
	mux.HandleFunc("POST /consults/{id}/messages", s.addMessage)
	mux.HandleFunc("POST /consults/{id}/escalate", s.escalate)
	mux.HandleFunc("POST /consults/{id}/token", s.joinToken)
	mux.HandleFunc("GET /overdue", s.overdue)
}

func (s server) createConsult(w http.ResponseWriter, r *http.Request) {
	var req struct {
		PatientID  string `json:"patient_id"`
		FacilityID string `json:"facility_id"`
		Priority   string `json:"priority"`
		Complaint  string `json:"complaint"`
		Consent    bool   `json:"consent"`
		Actor      string `json:"actor"`
		Role       string `json:"role"`
		MediaRef   string `json:"media_ref"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if req.PatientID == "" || req.Complaint == "" {
		writeJSON(w, 400, map[string]string{"error": "patient_id and complaint required"})
		return
	}
	c, err := s.store.Create(Consult{
		PatientID: req.PatientID, FacilityID: req.FacilityID,
		Priority: req.Priority, Complaint: req.Complaint, Consent: req.Consent,
	})
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	if req.Actor != "" {
		role := req.Role
		if role == "" {
			role = "patient"
		}
		c.Messages = append(c.Messages, Message{
			At: time.Now(), Author: req.Actor, Role: role,
			Body: req.Complaint, MediaRef: req.MediaRef,
		})
		s.store.Update(c)
	}
	writeJSON(w, 201, c)
}

func (s server) listConsults(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"consults": s.store.List(r.URL.Query().Get("state"))})
}

func (s server) getConsult(w http.ResponseWriter, r *http.Request) {
	c, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, c)
}

func (s server) transition(w http.ResponseWriter, r *http.Request) {
	var req struct {
		To        string `json:"to"`
		Actor     string `json:"actor"`
		Clinician string `json:"clinician"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	c, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	c, err = Transition(c, req.To)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": err.Error()})
		return
	}
	if req.Clinician != "" {
		c.Clinician = req.Clinician
	}
	s.store.Update(c)
	writeJSON(w, 200, c)
}

func (s server) addMessage(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Author   string `json:"author"`
		Role     string `json:"role"`
		Body     string `json:"body"`
		MediaRef string `json:"media_ref"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Author == "" || req.Body == "" {
		writeJSON(w, 400, map[string]string{"error": "author and body required"})
		return
	}
	c, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	if c.State == StCompleted || c.State == StCancelled {
		writeJSON(w, 409, map[string]string{"error": "consult is closed"})
		return
	}
	c.Messages = append(c.Messages, Message{
		At: time.Now(), Author: req.Author, Role: req.Role,
		Body: req.Body, MediaRef: req.MediaRef,
	})
	if req.Role == "clinician" && c.State == StInReview {
		c.State = StAdviceGiven
	}
	s.store.Update(c)
	writeJSON(w, 200, c)
}

// escalate provisions a LiveKit room and moves the consult to
// escalated_to_call in one step — a call never exists without its room.
func (s server) escalate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Tier  string `json:"tier"` // voice | video
		Actor string `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if req.Tier != TierVoice && req.Tier != TierVideo {
		writeJSON(w, 400, map[string]string{"error": "tier must be voice or video"})
		return
	}
	c, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	c.Room = ProvisionRoom(c.ID)
	c.Tier = req.Tier
	c, err = Transition(c, StEscalated)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": err.Error()})
		return
	}
	c.Messages = append(c.Messages, Message{At: time.Now(), Author: req.Actor,
		Role: "system", Body: "escalated to " + req.Tier + " call"})
	s.store.Update(c)
	writeJSON(w, 200, c)
}

// joinToken mints a LiveKit JWT for a participant of an escalated consult.
func (s server) joinToken(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Identity string `json:"identity"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Identity == "" {
		writeJSON(w, 400, map[string]string{"error": "identity required"})
		return
	}
	c, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	if c.Room == "" {
		writeJSON(w, 409, map[string]string{"error": "consult has no room — escalate first"})
		return
	}
	tok, err := LiveKitToken(s.apiKey, s.apiSecret, c.Room, req.Identity,
		c.Tier == TierVideo, 2*time.Hour)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"token": tok, "room": c.Room, "tier": c.Tier})
}

func (s server) overdue(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"overdue": AdviceOverdue(s.store.List(""), time.Now())})
}

func main() {
	listen := os.Getenv("LISTEN")
	if listen == "" {
		listen = ":8094"
	}
	s := server{store: NewMemoryStore(),
		apiKey: os.Getenv("LK_API_KEY"), apiSecret: os.Getenv("LK_API_SECRET")}
	mux := http.NewServeMux()
	s.routes(mux)
	if err := http.ListenAndServe(listen, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		panic(err)
	}
}
