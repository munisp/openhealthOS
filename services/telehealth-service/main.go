// Package main — OpenHealth OS telehealth service (module 8).
//
// Nurse-first telemedicine: every consult starts with a nurse assessment
// against a protocol. The nurse closes routine cases; anything beyond
// protocol escalates to a remote doctor and may produce a referral into
// the physical network. Prescriptions route to the facility pharmacy or
// a designated retail pharmacy.
//
// The nurse-first gate is structural, not advisory: there is no code path
// that reaches the doctor queue without a recorded nurse assessment —
// specialist time is the scarcest resource in the system and the gate is
// what keeps it spendable on patients who need it.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"time"
)

// Consult is one telehealth episode, nurse-first by construction.
type Consult struct {
	ID         string `json:"id"`
	PatientID  string `json:"patient_id"`
	FacilityID string `json:"facility_id"`
	NurseID    string `json:"nurse_id"`
	Complaint  string `json:"complaint"`
	Protocol   string `json:"protocol,omitempty"`   // triage protocol applied by the nurse
	Assessment string `json:"assessment,omitempty"` // nurse's recorded findings
	Status     string `json:"status"`               // open | assessed | escalated | closed
	Outcome    string `json:"outcome,omitempty"`    // routine-closure | doctor-consult | referred
	CreatedAt  string `json:"created_at"`
	ClosedAt   string `json:"closed_at,omitempty"`
}

// Referral moves a patient into the physical network.
type Referral struct {
	ID           string `json:"id"`
	ConsultID    string `json:"consult_id"`
	FromFacility string `json:"from_facility"`
	ToFacility   string `json:"to_facility"`
	Urgency      string `json:"urgency"` // routine | urgent | emergency
	Reason       string `json:"reason"`
	Status       string `json:"status"` // open | accepted | completed
	CreatedAt    string `json:"created_at"`
}

// Prescription routes a medication order to a pharmacy.
type Prescription struct {
	ID         string `json:"id"`
	ConsultID  string `json:"consult_id"`
	Prescriber string `json:"prescriber"` // doctor id, or nurse under protocol
	Medication string `json:"medication"`
	Dose       string `json:"dose"`
	Quantity   int    `json:"quantity"`
	PharmacyID string `json:"pharmacy_id"`
	Status     string `json:"status"` // routed | dispensed
}

var (
	ErrNotFound        = errors.New("not found")
	ErrBadTransition   = errors.New("invalid consult state transition")
	ErrNoAssessment    = errors.New("nurse assessment required first — the nurse-first gate")
	ErrBadUrgency      = errors.New("urgency must be routine | urgent | emergency")
	ErrNotPrescribable = errors.New("consult not in a prescribable state")
)

// Service holds consults, referrals and prescriptions.
type Service struct {
	mu            sync.Mutex
	consults      map[string]*Consult
	referrals     map[string]*Referral
	prescriptions map[string]*Prescription
	seq           int
}

func NewService() *Service {
	return &Service{
		consults:      map[string]*Consult{},
		referrals:     map[string]*Referral{},
		prescriptions: map[string]*Prescription{},
	}
}

func (s *Service) nextID(prefix string) string {
	s.seq++
	return fmt.Sprintf("%s-%04d", prefix, s.seq)
}

// Open starts a consult — always with a nurse, never directly with a doctor.
func (s *Service) Open(patientID, facilityID, nurseID, complaint string) Consult {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := &Consult{
		ID: s.nextID("CONS"), PatientID: patientID, FacilityID: facilityID,
		NurseID: nurseID, Complaint: complaint, Status: "open",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
	s.consults[c.ID] = c
	return *c
}

// Assess records the nurse's protocol assessment. Everything downstream —
// closure, escalation, prescription — requires this step.
func (s *Service) Assess(consultID, protocol, assessment string) (Consult, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.consults[consultID]
	if !ok {
		return Consult{}, ErrNotFound
	}
	if c.Status != "open" {
		return Consult{}, ErrBadTransition
	}
	if protocol == "" || assessment == "" {
		return Consult{}, errors.New("protocol and assessment are required")
	}
	c.Protocol, c.Assessment = protocol, assessment
	c.Status = "assessed"
	return *c, nil
}

// CloseRoutine closes a consult the nurse can handle under protocol.
func (s *Service) CloseRoutine(consultID string) (Consult, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.consults[consultID]
	if !ok {
		return Consult{}, ErrNotFound
	}
	if c.Status == "open" {
		return Consult{}, ErrNoAssessment
	}
	if c.Status != "assessed" {
		return Consult{}, ErrBadTransition
	}
	c.Status, c.Outcome = "closed", "routine-closure"
	c.ClosedAt = time.Now().UTC().Format(time.RFC3339)
	return *c, nil
}

// Escalate hands the consult to the remote doctor queue and, when the
// doctor judges the case needs hands on, opens a referral.
func (s *Service) Escalate(consultID string) (Consult, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.consults[consultID]
	if !ok {
		return Consult{}, ErrNotFound
	}
	if c.Status == "open" {
		return Consult{}, ErrNoAssessment
	}
	if c.Status != "assessed" {
		return Consult{}, ErrBadTransition
	}
	c.Status, c.Outcome = "escalated", "doctor-consult"
	return *c, nil
}

// Refer turns an escalated consult into a physical referral.
func (s *Service) Refer(consultID, toFacility, urgency, reason string) (Referral, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.consults[consultID]
	if !ok {
		return Referral{}, ErrNotFound
	}
	if c.Status != "escalated" {
		return Referral{}, ErrBadTransition
	}
	switch urgency {
	case "routine", "urgent", "emergency":
	default:
		return Referral{}, ErrBadUrgency
	}
	r := &Referral{
		ID: s.nextID("REF"), ConsultID: consultID, FromFacility: c.FacilityID,
		ToFacility: toFacility, Urgency: urgency, Reason: reason, Status: "open",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
	s.referrals[r.ID] = r
	c.Outcome = "referred"
	return *r, nil
}

// AcceptReferral: the receiving facility takes the patient.
func (s *Service) AcceptReferral(referralID string) (Referral, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.referrals[referralID]
	if !ok {
		return Referral{}, ErrNotFound
	}
	if r.Status != "open" {
		return Referral{}, ErrBadTransition
	}
	r.Status = "accepted"
	return *r, nil
}

// CompleteReferral: the patient was actually seen — the loop is closed.
func (s *Service) CompleteReferral(referralID string) (Referral, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.referrals[referralID]
	if !ok {
		return Referral{}, ErrNotFound
	}
	if r.Status != "accepted" {
		return Referral{}, ErrBadTransition
	}
	r.Status = "completed"
	return *r, nil
}

// Prescribe routes a prescription to a pharmacy. Prescribable states are
// "assessed" (nurse acting under protocol) and "escalated" (doctor).
func (s *Service) Prescribe(consultID, prescriber, medication, dose string, quantity int, pharmacyID string) (Prescription, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.consults[consultID]
	if !ok {
		return Prescription{}, ErrNotFound
	}
	if c.Status != "assessed" && c.Status != "escalated" {
		return Prescription{}, ErrNotPrescribable
	}
	if quantity <= 0 {
		return Prescription{}, errors.New("quantity must be positive")
	}
	p := &Prescription{
		ID: s.nextID("RX"), ConsultID: consultID, Prescriber: prescriber,
		Medication: medication, Dose: dose, Quantity: quantity,
		PharmacyID: pharmacyID, Status: "routed",
	}
	s.prescriptions[p.ID] = p
	return *p, nil
}

// Dispense marks a routed prescription filled at the pharmacy.
// ListPrescriptions supports the pharmacy pull fallback.
func (s *Service) ListPrescriptions(status string) []Prescription {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []Prescription{}
	for _, p := range s.prescriptions {
		if status == "" || p.Status == status {
			out = append(out, *p)
		}
	}
	return out
}

func (s *Service) Dispense(prescriptionID string) (Prescription, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.prescriptions[prescriptionID]
	if !ok {
		return Prescription{}, ErrNotFound
	}
	if p.Status != "routed" {
		return Prescription{}, ErrBadTransition
	}
	p.Status = "dispensed"
	return *p, nil
}

// TelehealthService is the consult lifecycle seam: the in-memory Service
// (edge, tests) and PostgresService (state plane) both satisfy it.
type TelehealthService interface {
	Open(patientID, facilityID, nurseID, complaint string) Consult
	Assess(consultID, protocol, assessment string) (Consult, error)
	CloseRoutine(consultID string) (Consult, error)
	Escalate(consultID string) (Consult, error)
	Refer(consultID, toFacility, urgency, reason string) (Referral, error)
	AcceptReferral(referralID string) (Referral, error)
	CompleteReferral(referralID string) (Referral, error)
	Prescribe(consultID, prescriber, medication, dose string, quantity int, pharmacyID string) (Prescription, error)
	Dispense(prescriptionID string) (Prescription, error)
}

var _ TelehealthService = (*Service)(nil)

// ---------------------------------------------------------------- HTTP

type server struct {
	svc TelehealthService
	ix  Integrations
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /consults", s.openConsult)
	mux.HandleFunc("POST /consults/{id}/assess", s.assess)
	mux.HandleFunc("POST /consults/{id}/close", s.closeRoutine)
	mux.HandleFunc("POST /consults/{id}/escalate", s.escalate)
	mux.HandleFunc("POST /consults/{id}/refer", s.refer)
	mux.HandleFunc("POST /referrals/{id}/accept", s.acceptReferral)
	mux.HandleFunc("POST /referrals/{id}/complete", s.completeReferral)
	mux.HandleFunc("POST /prescriptions", s.prescribe)
	mux.HandleFunc("POST /prescriptions/{id}/dispense", s.dispense)
	// Pull fallback: a pharmacy that was down when the prescription was
	// routed catches up by polling this.
	mux.HandleFunc("GET /prescriptions", s.listPrescriptions)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok"))
	})
	return mux
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return false
	}
	return true
}

func reply(w http.ResponseWriter, v any, err error) {
	switch {
	case err == nil:
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(v)
	case errors.Is(err, ErrNotFound):
		http.Error(w, err.Error(), http.StatusNotFound)
	case errors.Is(err, ErrNoAssessment), errors.Is(err, ErrNotPrescribable):
		http.Error(w, err.Error(), http.StatusForbidden)
	default:
		http.Error(w, err.Error(), http.StatusConflict)
	}
}

func (s server) openConsult(w http.ResponseWriter, r *http.Request) {
	var req struct {
		PatientID  string `json:"patient_id"`
		FacilityID string `json:"facility_id"`
		NurseID    string `json:"nurse_id"`
		Complaint  string `json:"complaint"`
	}
	if !decode(w, r, &req) {
		return
	}
	if req.PatientID == "" || req.NurseID == "" || req.Complaint == "" {
		http.Error(w, "patient_id, nurse_id and complaint are required", http.StatusBadRequest)
		return
	}
	reply(w, s.svc.Open(req.PatientID, req.FacilityID, req.NurseID, req.Complaint), nil)
}

func (s server) assess(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Protocol   string `json:"protocol"`
		Assessment string `json:"assessment"`
	}
	if !decode(w, r, &req) {
		return
	}
	v, err := s.svc.Assess(r.PathValue("id"), req.Protocol, req.Assessment)
	reply(w, v, err)
}

func (s server) closeRoutine(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.CloseRoutine(r.PathValue("id"))
	if err == nil {
		s.ix.onConsultFinished(v)
	}
	reply(w, v, err)
}

func (s server) escalate(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.Escalate(r.PathValue("id"))
	if err == nil {
		s.ix.onConsultFinished(v)
	}
	reply(w, v, err)
}

func (s server) refer(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ToFacility string `json:"to_facility"`
		Urgency    string `json:"urgency"`
		Reason     string `json:"reason"`
	}
	if !decode(w, r, &req) {
		return
	}
	v, err := s.svc.Refer(r.PathValue("id"), req.ToFacility, req.Urgency, req.Reason)
	reply(w, v, err)
}

func (s server) acceptReferral(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.AcceptReferral(r.PathValue("id"))
	reply(w, v, err)
}

func (s server) completeReferral(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.CompleteReferral(r.PathValue("id"))
	reply(w, v, err)
}

func (s server) prescribe(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ConsultID  string   `json:"consult_id"`
		Prescriber string   `json:"prescriber"`
		Medication string   `json:"medication"` // formulary code (e.g. AMOX500)
		Dose       string   `json:"dose"`
		Quantity   int      `json:"quantity"`
		PharmacyID string   `json:"pharmacy_id"`
		PatientID  string   `json:"patient_id"`  // for the FHIR + pharmacy provenance write
		FacilityID string   `json:"facility_id"` // prescriber's facility
		Allergies  []string `json:"allergies"`   // allergy classes from the chart
	}
	if !decode(w, r, &req) {
		return
	}
	if req.ConsultID == "" || req.Medication == "" || req.PharmacyID == "" {
		http.Error(w, "consult_id, medication and pharmacy_id are required", http.StatusBadRequest)
		return
	}
	v, err := s.svc.Prescribe(req.ConsultID, req.Prescriber, req.Medication, req.Dose, req.Quantity, req.PharmacyID)
	if err != nil {
		reply(w, v, err)
		return
	}
	routed := s.ix.onPrescribed(v, req.PatientID, req.FacilityID, req.Allergies)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"prescription":    v,
		"pharmacy_routed": routed, // false → pharmacy pulls via GET /prescriptions?status=routed
	})
}

// listPrescriptions is the pharmacy's pull fallback.
func (s server) listPrescriptions(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	if l, ok := s.svc.(interface{ ListPrescriptions(string) []Prescription }); ok {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(l.ListPrescriptions(status))
		return
	}
	http.Error(w, "listing unsupported by this store", http.StatusNotImplemented)
}

func (s server) dispense(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.Dispense(r.PathValue("id"))
	reply(w, v, err)
}
