package main

import (
	"testing"
	"time"
)

func newTestService() *Service {
	s := NewService(NewMemoryStore())
	s.now = func() time.Time { return time.Date(2026, 10, 18, 8, 0, 0, 0, time.UTC) }
	return s
}

func TestReceiveAndDispenseHappyPath(t *testing.T) {
	s := newTestService()
	if _, err := s.ReceiveStock("PHC-1", "AMOX500", 100, 20); err != nil {
		t.Fatal(err)
	}
	req, err := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-1", PatientID: "PT-1", FacilityID: "PHC-1",
		DrugCode: "AMOX500", Dose: "500mg tid", Quantity: 21, Prescriber: "DR-7",
	})
	if err != nil || req.Status != "pending" {
		t.Fatalf("route: %+v %v", req, err)
	}
	done, draft, err := s.Dispense(req.ID, "PHARM-1")
	if err != nil {
		t.Fatal(err)
	}
	if done.Status != "dispensed" || done.DispensedBy != "PHARM-1" {
		t.Fatalf("not dispensed: %+v", done)
	}
	lvl, _ := s.store.StockLevel("PHC-1", "AMOX500")
	if lvl.Quantity != 79 {
		t.Fatalf("stock = %d, want 79", lvl.Quantity)
	}
	if draft.TotalNGN != 21*25 || draft.Status != "drafted" {
		t.Fatalf("bad claim draft: %+v", draft)
	}
}

func TestAllergyGateBlocksPenicillin(t *testing.T) {
	s := newTestService()
	s.ReceiveStock("PHC-1", "AMOX500", 100, 0)
	req, err := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-2", PatientID: "PT-2", FacilityID: "PHC-1",
		DrugCode: "AMOX500", Quantity: 21, Allergies: []string{"penicillin"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if req.Status != "rejected" || req.RejectReason == "" {
		t.Fatalf("allergy not blocked: %+v", req)
	}
	if _, _, err := s.Dispense(req.ID, "PHARM-1"); err == nil {
		t.Fatal("dispensing a rejected request must fail")
	}
	lvl, _ := s.store.StockLevel("PHC-1", "AMOX500")
	if lvl.Quantity != 100 {
		t.Fatal("rejected request must not touch stock")
	}
}

func TestOutOfStockCannotDispense(t *testing.T) {
	s := newTestService()
	s.ReceiveStock("PHC-1", "AL8020", 5, 0)
	req, _ := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-3", PatientID: "PT-3", FacilityID: "PHC-1",
		DrugCode: "AL8020", Quantity: 24,
	})
	if _, _, err := s.Dispense(req.ID, "PHARM-1"); err != ErrOutOfStock {
		t.Fatalf("want ErrOutOfStock, got %v", err)
	}
}

func TestDoubleDispenseRejected(t *testing.T) {
	s := newTestService()
	s.ReceiveStock("PHC-1", "PARA500", 100, 0)
	req, _ := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-4", PatientID: "PT-4", FacilityID: "PHC-1",
		DrugCode: "PARA500", Quantity: 10,
	})
	if _, _, err := s.Dispense(req.ID, "A"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.Dispense(req.ID, "B"); err != ErrBadTransition {
		t.Fatalf("second dispense: want ErrBadTransition, got %v", err)
	}
}

func TestUnknownDrugAndBadQuantity(t *testing.T) {
	s := newTestService()
	if _, err := s.ReceiveStock("PHC-1", "NOT-A-DRUG", 10, 0); err != ErrUnknownDrug {
		t.Fatal(err)
	}
	if _, err := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-5", PatientID: "P", FacilityID: "F", DrugCode: "FAKE", Quantity: 1,
	}); err != ErrUnknownDrug {
		t.Fatal(err)
	}
	if _, err := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-6", PatientID: "P", FacilityID: "F", DrugCode: "ORS", Quantity: 0,
	}); err != ErrBadQuantity {
		t.Fatal(err)
	}
}

func TestNonFormularyAllergyClassOnlyBlocksClass(t *testing.T) {
	s := newTestService()
	s.ReceiveStock("PHC-1", "AL8020", 100, 0)
	req, err := s.RoutePrescription(DispenseRequest{
		PrescriptionID: "RX-7", PatientID: "PT-5", FacilityID: "PHC-1",
		DrugCode: "AL8020", Quantity: 4, Allergies: []string{"penicillin"}, // different class
	})
	if err != nil || req.Status != "pending" {
		t.Fatalf("unrelated allergy must not block: %+v %v", req, err)
	}
}
