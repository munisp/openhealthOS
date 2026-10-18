package main

import (
	"errors"
	"testing"
)

func TestNurseFirstGate(t *testing.T) {
	svc := NewService()
	c := svc.Open("P-1", "NG-KD-0014", "NURSE-7", "fever and headache")

	// No path to the doctor without a nurse assessment.
	if _, err := svc.Escalate(c.ID); !errors.Is(err, ErrNoAssessment) {
		t.Fatalf("escalate without assessment: want ErrNoAssessment, got %v", err)
	}
	if _, err := svc.CloseRoutine(c.ID); !errors.Is(err, ErrNoAssessment) {
		t.Fatalf("close without assessment: want ErrNoAssessment, got %v", err)
	}
	if _, err := svc.Prescribe(c.ID, "NURSE-7", "ACT", "4 tabs bd", 24, "PHARM-1"); !errors.Is(err, ErrNotPrescribable) {
		t.Fatalf("prescribe without assessment: want ErrNotPrescribable, got %v", err)
	}
}

func TestRoutineClosureFlow(t *testing.T) {
	svc := NewService()
	c := svc.Open("P-1", "NG-KD-0014", "NURSE-7", "fever and headache")
	if _, err := svc.Assess(c.ID, "MALARIA-UNCOMPLICATED", "RDT positive, no danger signs"); err != nil {
		t.Fatalf("assess: %v", err)
	}
	closed, err := svc.CloseRoutine(c.ID)
	if err != nil {
		t.Fatalf("close: %v", err)
	}
	if closed.Status != "closed" || closed.Outcome != "routine-closure" {
		t.Fatalf("bad closure: %+v", closed)
	}
	// A closed consult is terminal.
	if _, err := svc.Escalate(c.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("escalate closed consult: want ErrBadTransition, got %v", err)
	}
}

func TestEscalationAndReferral(t *testing.T) {
	svc := NewService()
	c := svc.Open("P-2", "NG-KD-0014", "NURSE-7", "severe abdominal pain")
	svc.Assess(c.ID, "OBSTETRIC-DANGER", "bleeding, 32 weeks pregnant")
	if _, err := svc.Refer(c.ID, "NG-KD-GH-01", "emergency", "antepartum haemorrhage"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("refer before escalation: want ErrBadTransition, got %v", err)
	}
	if _, err := svc.Escalate(c.ID); err != nil {
		t.Fatalf("escalate: %v", err)
	}
	if _, err := svc.Refer(c.ID, "NG-KD-GH-01", "stat", "bad urgency"); !errors.Is(err, ErrBadUrgency) {
		t.Fatalf("bad urgency: want ErrBadUrgency, got %v", err)
	}
	ref, err := svc.Refer(c.ID, "NG-KD-GH-01", "emergency", "antepartum haemorrhage")
	if err != nil {
		t.Fatalf("refer: %v", err)
	}
	if ref.FromFacility != "NG-KD-0014" || ref.Urgency != "emergency" {
		t.Fatalf("bad referral: %+v", ref)
	}
	// Referral loop: accept then complete, no skipping.
	if _, err := svc.CompleteReferral(ref.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("complete before accept: want ErrBadTransition, got %v", err)
	}
	svc.AcceptReferral(ref.ID)
	done, err := svc.CompleteReferral(ref.ID)
	if err != nil || done.Status != "completed" {
		t.Fatalf("complete: %v %+v", err, done)
	}
}

func TestPrescriptionRouting(t *testing.T) {
	svc := NewService()
	c := svc.Open("P-3", "NG-KD-0014", "NURSE-7", "fever")
	svc.Assess(c.ID, "MALARIA-UNCOMPLICATED", "RDT positive")
	p, err := svc.Prescribe(c.ID, "NURSE-7", "artemether-lumefantrine", "4 tabs bd x3d", 24, "PHARM-RETAIL-2")
	if err != nil {
		t.Fatalf("prescribe: %v", err)
	}
	if p.Status != "routed" || p.PharmacyID != "PHARM-RETAIL-2" {
		t.Fatalf("bad prescription: %+v", p)
	}
	if _, err := svc.Prescribe(c.ID, "NURSE-7", "ACT", "bd", 0, "PHARM-1"); err == nil {
		t.Fatal("zero quantity accepted")
	}
	dispensed, err := svc.Dispense(p.ID)
	if err != nil || dispensed.Status != "dispensed" {
		t.Fatalf("dispense: %v %+v", err, dispensed)
	}
	if _, err := svc.Dispense(p.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("double dispense: want ErrBadTransition, got %v", err)
	}
}

func TestEscalatedConsultPrescribableByDoctor(t *testing.T) {
	svc := NewService()
	c := svc.Open("P-4", "NG-KD-0014", "NURSE-7", "persistent cough")
	svc.Assess(c.ID, "PNEUMONIA", "fast breathing, chest indrawing absent")
	svc.Escalate(c.ID)
	p, err := svc.Prescribe(c.ID, "DR-REMOTE-3", "amoxicillin", "500mg tid x5d", 15, "PHARM-1")
	if err != nil || p.Prescriber != "DR-REMOTE-3" {
		t.Fatalf("doctor prescribe: %v %+v", err, p)
	}
}

func TestNotFound(t *testing.T) {
	svc := NewService()
	if _, err := svc.Assess("CONS-XXXX", "X", "Y"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("assess unknown: want ErrNotFound, got %v", err)
	}
	if _, err := svc.Dispense("RX-XXXX"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("dispense unknown: want ErrNotFound, got %v", err)
	}
}

// ADR-0016: the patient reads their own consult thread in the portal —
// oldest-first, and never another patient's consults.
func TestConsultsForPatientThread(t *testing.T) {
	svc := NewService()
	first := svc.Open("PT-1", "NG-KD-0014", "NURSE-7", "fever")
	second := svc.Open("PT-1", "NG-KD-0014", "NURSE-7", "follow-up cough")
	svc.Open("PT-2", "NG-KD-0014", "NURSE-7", "someone else's rash")

	thread := svc.ConsultsForPatient("PT-1")
	if len(thread) != 2 {
		t.Fatalf("thread length: %d", len(thread))
	}
	if thread[0].ID != first.ID || thread[1].ID != second.ID {
		t.Fatalf("thread must be oldest-first: %q then %q", thread[0].ID, thread[1].ID)
	}
	for _, c := range thread {
		if c.PatientID != "PT-1" {
			t.Fatalf("cross-patient leak: %+v", c)
		}
	}
	// Unknown patient: empty thread, never nil-as-error.
	if got := svc.ConsultsForPatient("PT-NOBODY"); len(got) != 0 {
		t.Fatalf("unknown patient: %d consults", len(got))
	}
}
