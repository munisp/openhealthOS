package main

import (
	"errors"
	"strings"
	"testing"
)

type stubAppts struct{ slot string; err error }
func (s stubAppts) BookNext(m, c string) (string, error) { return s.slot, s.err }

type stubRefills struct{ label string; days int; ok bool }
func (s stubRefills) SetReminder(m, l string, d int) error { return nil }
func (s stubRefills) NextRefill(m string) (string, int, bool) { return s.label, s.days, s.ok }

type stubResults struct{ ready, pending int; err error }
func (s stubResults) Status(m string) (int, int, error) { return s.ready, s.pending, s.err }

func TestMenuHidesSelfServiceWhenNil(t *testing.T) {
	g := NewGateway(fakeChecker{})
	out := g.Handle("s1", "")
	if strings.Contains(out, "Book an appointment") {
		t.Fatal("self-service must be hidden when providers are nil")
	}
}

func TestAppointmentBookingFlow(t *testing.T) {
	g := NewGateway(fakeChecker{}).WithAppointments(stubAppts{slot: "Tue 21 Oct 09:00"})
	g.Handle("s2", "")
	out := g.Handle("s2", "6")
	if !strings.Contains(out, "member ID") {
		t.Fatalf("got %q", out)
	}
	g.Handle("s2", "NG-0001")
	out = g.Handle("s2", "2")
	if !strings.Contains(out, "ANC") || !strings.Contains(out, "Tue 21 Oct 09:00") {
		t.Fatalf("got %q", out)
	}
}

func TestAppointmentFailSoft(t *testing.T) {
	g := NewGateway(fakeChecker{}).WithAppointments(stubAppts{err: errors.New("down")})
	g.Handle("s3", "")
	g.Handle("s3", "6")
	g.Handle("s3", "NG-0001")
	out := g.Handle("s3", "1")
	if !strings.HasPrefix(out, "END We could not book") {
		t.Fatalf("fail-soft expected, got %q", out)
	}
}

func TestRefillAndResults(t *testing.T) {
	g := NewGateway(fakeChecker{}).
		WithRefills(stubRefills{label: "Amlodipine 5mg", days: 6, ok: true}).
		WithResults(stubResults{ready: 2, pending: 1})
	g.Handle("s4", "")
	g.Handle("s4", "7")
	out := g.Handle("s4", "NG-0001")
	if !strings.Contains(out, "Amlodipine") {
		t.Fatalf("got %q", out)
	}
	g.Handle("s5", "")
	g.Handle("s5", "8")
	out = g.Handle("s5", "NG-0001")
	if !strings.Contains(out, "2 result(s) READY") {
		t.Fatalf("got %q", out)
	}
}
