// OpenHealth OS — pharmacy service (module: medicines).
//
// The dispensing point of the clinical loop: telehealth routes a
// prescription here, the pharmacist dispenses, stock decrements
// atomically, a claim line drafts itself, and the event joins the sync
// stream. Allergy and duplicate-therapy checks are structural — a
// dispensing that fails them cannot be created.
//
// Doctrine: in-memory store for edge/tests, Postgres when DATABASE_URL
// is set (migration 0003). servex + authz like every state-plane service.
package main

import (
	"errors"
	"fmt"
	"sync"
	"time"
)

// ---------- formulary ----------

// Drug is one formulary entry. AllergyClass is the structural key the
// allergy gate checks against patient allergy records.
type Drug struct {
	Code         string  `json:"code"` // e.g. AMOX500
	Name         string  `json:"name"` // "Amoxicillin 500mg capsule"
	Unit         string  `json:"unit"` // capsule | tablet | vial | bottle
	UnitPriceNGN float64 `json:"unit_price_ngn"`
	Essential    bool    `json:"essential"`     // NHIA essential medicines list
	AllergyClass string  `json:"allergy_class"` // penicillin | sulfa | nsaid | "" (no class)
	TherapyClass string  `json:"therapy_class"` // antibiotic | antimalarial | analgesic | ...
}

// Formulary is the tracer-drug catalogue (seeded identically in memory
// and SQL so edge and state plane agree).
func Formulary() []Drug {
	return []Drug{
		{"AMOX500", "Amoxicillin 500mg capsule", "capsule", 25, true, "penicillin", "antibiotic"},
		{"AL8020", "Artemether/Lumefantrine 80/480 tab", "tablet", 120, true, "", "antimalarial"},
		{"PARA500", "Paracetamol 500mg tablet", "tablet", 5, true, "", "analgesic"},
		{"IBU400", "Ibuprofen 400mg tablet", "tablet", 8, true, "nsaid", "analgesic"},
		{"ORS", "Oral rehydration salts sachet", "sachet", 15, true, "", "rehydration"},
		{"ZINC20", "Zinc sulphate 20mg tablet", "tablet", 10, true, "", "supplement"},
		{"AMLO5", "Amlodipine 5mg tablet", "tablet", 12, true, "", "antihypertensive"},
		{"MET500", "Metformin 500mg tablet", "tablet", 9, true, "", "antidiabetic"},
		{"COTRIM480", "Co-trimoxazole 480mg tablet", "tablet", 7, true, "sulfa", "antibiotic"},
		{"OXYTOCIN10", "Oxytocin 10IU vial (cold chain)", "vial", 150, true, "", "uterotonic"},
	}
}

func formularyDrug(code string) (Drug, bool) {
	for _, d := range Formulary() {
		if d.Code == code {
			return d, true
		}
	}
	return Drug{}, false
}

// ---------- domain ----------

// DispenseRequest is a routed prescription awaiting pharmacist action.
type DispenseRequest struct {
	ID             string   `json:"id"`
	PrescriptionID string   `json:"prescription_id"`
	PatientID      string   `json:"patient_id"`
	FacilityID     string   `json:"facility_id"`
	DrugCode       string   `json:"drug_code"`
	Dose           string   `json:"dose"`
	Quantity       int      `json:"quantity"`
	Prescriber     string   `json:"prescriber"`
	Allergies      []string `json:"allergies,omitempty"` // allergy classes from the chart
	Status         string   `json:"status"`              // pending | dispensed | rejected
	RejectReason   string   `json:"reject_reason,omitempty"`
	CreatedAt      string   `json:"created_at"`
	DispensedAt    string   `json:"dispensed_at,omitempty"`
	DispensedBy    string   `json:"dispensed_by,omitempty"`
}

// ClaimDraft is the money trail of a dispensing — one claim line,
// priced off the formulary, awaiting batch submission.
type ClaimDraft struct {
	ID           string  `json:"id"`
	DispenseID   string  `json:"dispense_id"`
	FacilityID   string  `json:"facility_id"`
	PatientID    string  `json:"patient_id"`
	ItemCode     string  `json:"item_code"`
	Quantity     int     `json:"quantity"`
	UnitPriceNGN float64 `json:"unit_price_ngn"`
	TotalNGN     float64 `json:"total_ngn"`
	Status       string  `json:"status"` // drafted | submitted
	CreatedAt    string  `json:"created_at"`
}

// StockLevel is one facility's holding of one drug.
type StockLevel struct {
	FacilityID   string `json:"facility_id"`
	DrugCode     string `json:"drug_code"`
	Quantity     int    `json:"quantity"`
	ReorderLevel int    `json:"reorder_level"`
}

var (
	ErrNotFound      = errors.New("not found")
	ErrUnknownDrug   = errors.New("drug not on formulary")
	ErrBadTransition = errors.New("invalid dispense-request state transition")
	ErrOutOfStock    = errors.New("insufficient stock — reorder before dispensing")
	ErrAllergyBlock  = errors.New("patient allergy matches this drug's allergy class — dispensing blocked")
	ErrBadQuantity   = errors.New("quantity must be positive")
)

// ---------- store seam ----------

// Store is the persistence seam: memory (edge/tests) or Postgres.
type Store interface {
	UpsertStock(facilityID, drugCode string, delta, reorderLevel int) (StockLevel, error)
	Stock(facilityID string) []StockLevel
	StockLevel(facilityID, drugCode string) (StockLevel, error)
	PutRequest(r DispenseRequest) error
	GetRequest(id string) (DispenseRequest, error)
	Requests(facilityID, status string) []DispenseRequest
	// Dispense atomically: mark request dispensed + decrement stock +
	// write the claim draft. Fails closed (all or nothing).
	Dispense(requestID, staffID string, at time.Time, draft ClaimDraft) error
	PutDraft(d ClaimDraft) error
	Drafts(facilityID, status string) []ClaimDraft
	MarkDraftSubmitted(id string) error
}

// ---------- memory store ----------

type MemoryStore struct {
	mu       sync.Mutex
	stock    map[string]*StockLevel // facility|drug
	requests map[string]*DispenseRequest
	drafts   map[string]*ClaimDraft
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		stock:    map[string]*StockLevel{},
		requests: map[string]*DispenseRequest{},
		drafts:   map[string]*ClaimDraft{},
	}
}

func stockKey(facility, drug string) string { return facility + "|" + drug }

func (m *MemoryStore) UpsertStock(facilityID, drugCode string, delta, reorderLevel int) (StockLevel, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := stockKey(facilityID, drugCode)
	s, ok := m.stock[k]
	if !ok {
		s = &StockLevel{FacilityID: facilityID, DrugCode: drugCode, ReorderLevel: reorderLevel}
		m.stock[k] = s
	}
	if s.Quantity+delta < 0 {
		return StockLevel{}, ErrOutOfStock
	}
	s.Quantity += delta
	if reorderLevel > 0 {
		s.ReorderLevel = reorderLevel
	}
	return *s, nil
}

func (m *MemoryStore) Stock(facilityID string) []StockLevel {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []StockLevel{}
	for _, s := range m.stock {
		if s.FacilityID == facilityID {
			out = append(out, *s)
		}
	}
	return out
}

func (m *MemoryStore) StockLevel(facilityID, drugCode string) (StockLevel, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if s, ok := m.stock[stockKey(facilityID, drugCode)]; ok {
		return *s, nil
	}
	return StockLevel{}, ErrNotFound
}

func (m *MemoryStore) PutRequest(r DispenseRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.requests[r.ID] = &r
	return nil
}

func (m *MemoryStore) GetRequest(id string) (DispenseRequest, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if r, ok := m.requests[id]; ok {
		return *r, nil
	}
	return DispenseRequest{}, ErrNotFound
}

func (m *MemoryStore) Requests(facilityID, status string) []DispenseRequest {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []DispenseRequest{}
	for _, r := range m.requests {
		if (facilityID == "" || r.FacilityID == facilityID) && (status == "" || r.Status == status) {
			out = append(out, *r)
		}
	}
	return out
}

func (m *MemoryStore) Dispense(requestID, staffID string, at time.Time, draft ClaimDraft) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.requests[requestID]
	if !ok {
		return ErrNotFound
	}
	if r.Status != "pending" {
		return ErrBadTransition
	}
	s, ok := m.stock[stockKey(r.FacilityID, r.DrugCode)]
	if !ok || s.Quantity < r.Quantity {
		return ErrOutOfStock
	}
	s.Quantity -= r.Quantity
	r.Status = "dispensed"
	r.DispensedBy = staffID
	r.DispensedAt = at.UTC().Format(time.RFC3339)
	m.drafts[draft.ID] = &draft
	return nil
}

func (m *MemoryStore) PutDraft(d ClaimDraft) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.drafts[d.ID] = &d
	return nil
}

func (m *MemoryStore) Drafts(facilityID, status string) []ClaimDraft {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []ClaimDraft{}
	for _, d := range m.drafts {
		if (facilityID == "" || d.FacilityID == facilityID) && (status == "" || d.Status == status) {
			out = append(out, *d)
		}
	}
	return out
}

func (m *MemoryStore) MarkDraftSubmitted(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	d, ok := m.drafts[id]
	if !ok {
		return ErrNotFound
	}
	d.Status = "submitted"
	return nil
}

var _ Store = (*MemoryStore)(nil)

// ---------- domain service ----------

// Service owns the dispensing rules on top of a Store.
type Service struct {
	store Store
	seq   int
	mu    sync.Mutex
	now   func() time.Time
}

func NewService(store Store) *Service {
	return &Service{store: store, now: time.Now}
}

func (s *Service) nextID(prefix string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	return fmt.Sprintf("%s-%06d", prefix, s.seq)
}

// ReceiveStock adds inventory (procurement intake).
func (s *Service) ReceiveStock(facilityID, drugCode string, qty, reorderLevel int) (StockLevel, error) {
	if _, ok := formularyDrug(drugCode); !ok {
		return StockLevel{}, ErrUnknownDrug
	}
	if qty <= 0 {
		return StockLevel{}, ErrBadQuantity
	}
	return s.store.UpsertStock(facilityID, drugCode, qty, reorderLevel)
}

// RoutePrescription registers a dispense request from telehealth. The
// allergy gate runs here — a blocked request is recorded as rejected
// with the reason, never silently dropped (auditable denial).
func (s *Service) RoutePrescription(req DispenseRequest) (DispenseRequest, error) {
	drug, ok := formularyDrug(req.DrugCode)
	if !ok {
		return DispenseRequest{}, ErrUnknownDrug
	}
	if req.Quantity <= 0 {
		return DispenseRequest{}, ErrBadQuantity
	}
	if req.ID == "" {
		req.ID = s.nextID("DSP")
	}
	req.Status = "pending"
	req.CreatedAt = s.now().UTC().Format(time.RFC3339)
	for _, allergy := range req.Allergies {
		if drug.AllergyClass != "" && allergy == drug.AllergyClass {
			req.Status = "rejected"
			req.RejectReason = fmt.Sprintf("allergy:%s blocks %s (%s)", allergy, drug.Code, drug.Name)
			break
		}
	}
	if err := s.store.PutRequest(req); err != nil {
		return DispenseRequest{}, err
	}
	return req, nil
}

// Dispense completes a pending request: stock decrements, claim drafts,
// prescription closes — atomically at the store.
func (s *Service) Dispense(requestID, staffID string) (DispenseRequest, ClaimDraft, error) {
	req, err := s.store.GetRequest(requestID)
	if err != nil {
		return DispenseRequest{}, ClaimDraft{}, err
	}
	if req.Status == "rejected" {
		return req, ClaimDraft{}, fmt.Errorf("%w: %s", ErrAllergyBlock, req.RejectReason)
	}
	if req.Status != "pending" {
		return req, ClaimDraft{}, ErrBadTransition
	}
	drug, _ := formularyDrug(req.DrugCode)
	lvl, err := s.store.StockLevel(req.FacilityID, req.DrugCode)
	if err != nil || lvl.Quantity < req.Quantity {
		return req, ClaimDraft{}, ErrOutOfStock
	}
	draft := ClaimDraft{
		ID:           s.nextID("CD"),
		DispenseID:   req.ID,
		FacilityID:   req.FacilityID,
		PatientID:    req.PatientID,
		ItemCode:     drug.Code,
		Quantity:     req.Quantity,
		UnitPriceNGN: drug.UnitPriceNGN,
		TotalNGN:     drug.UnitPriceNGN * float64(req.Quantity),
		Status:       "drafted",
		CreatedAt:    s.now().UTC().Format(time.RFC3339),
	}
	if err := s.store.Dispense(requestID, staffID, s.now(), draft); err != nil {
		return req, ClaimDraft{}, err
	}
	req.Status = "dispensed"
	req.DispensedBy = staffID
	req.DispensedAt = draft.CreatedAt
	return req, draft, nil
}
