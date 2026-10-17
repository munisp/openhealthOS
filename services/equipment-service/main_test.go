package main

import (
	"errors"
	"testing"
)

func seeded(t *testing.T) (*Service, Equipment) {
	t.Helper()
	s := NewService()
	s.AddTechnician(Technician{ID: "BMET-1", Name: "Emeka Obi", Vetted: true, Regions: []string{"kaduna"}})
	s.AddTechnician(Technician{ID: "BMET-2", Name: "Unvetted Uche", Vetted: false})
	e, err := s.Register(Equipment{Facility: "PHC-007", Type: "vaccine-fridge", Model: "SureChill"})
	if err != nil {
		t.Fatal(err)
	}
	return s, e
}

func TestTicketLifecycle(t *testing.T) {
	s, e := seeded(t)
	tk, err := s.OpenTicket(e.ID, "compressor failure", "P1")
	if err != nil {
		t.Fatal(err)
	}
	// equipment goes down when the ticket opens
	got, _ := s.GetEquipment(e.ID)
	if got.Status != "down" {
		t.Fatalf("equipment status = %s, want down", got.Status)
	}
	// open ticket cannot be resolved before assignment
	if err := s.Resolve(tk.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatal("resolve before assign must be rejected")
	}
	if err := s.Assign(tk.ID, "BMET-1"); err != nil {
		t.Fatal(err)
	}
	if err := s.Resolve(tk.ID); err != nil {
		t.Fatal(err)
	}
	got, _ = s.GetEquipment(e.ID)
	if got.Status != "working" {
		t.Fatalf("equipment status = %s, want working after resolve", got.Status)
	}
}

func TestMarketplaceGate(t *testing.T) {
	s, e := seeded(t)
	tk, _ := s.OpenTicket(e.ID, "broken seal", "P2")
	if err := s.Assign(tk.ID, "BMET-2"); !errors.Is(err, ErrUnvettedTech) {
		t.Fatal("unvetted technician must not receive tickets")
	}
}

func TestDonationScreening(t *testing.T) {
	good := Equipment{Facility: "PHC-007", Type: "autoclave", Status: "working", Donated: true}
	if err := DonationScreen(good, true, true); err != nil {
		t.Fatal(err)
	}
	if err := DonationScreen(good, false, true); !errors.Is(err, ErrDonationReject) {
		t.Fatal("no local consumables → reject at intake")
	}
	if err := DonationScreen(good, true, false); !errors.Is(err, ErrDonationReject) {
		t.Fatal("incompatible voltage → reject at intake")
	}
	dead := good
	dead.Status = "condemned"
	if err := DonationScreen(dead, true, true); !errors.Is(err, ErrDonationReject) {
		t.Fatal("condemned-at-source → reject")
	}
}

func TestDoubleAssignRejected(t *testing.T) {
	s, e := seeded(t)
	tk, _ := s.OpenTicket(e.ID, "x", "P3")
	s.Assign(tk.ID, "BMET-1")
	if err := s.Assign(tk.ID, "BMET-1"); !errors.Is(err, ErrBadTransition) {
		t.Fatal("double assignment must be rejected")
	}
}

func TestListTicketsByStatus(t *testing.T) {
	s, e := seeded(t)
	s.OpenTicket(e.ID, "a", "P2")
	tk2, _ := s.OpenTicket(e.ID, "b", "P1")
	s.Assign(tk2.ID, "BMET-1")
	if len(s.ListTickets("open")) != 1 || len(s.ListTickets("assigned")) != 1 || len(s.ListTickets("")) != 2 {
		t.Fatal("status filtering wrong")
	}
}
