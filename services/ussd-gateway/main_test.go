package main

import (
	"errors"
	"strings"
	"testing"
)

type fakeChecker struct {
	members map[string][3]string // id -> [eligible, scheme, validUntil]
}

func (f fakeChecker) Check(id string) (bool, string, string, error) {
	m, ok := f.members[id]
	if !ok {
		return false, "", "", errors.New("unknown member")
	}
	return m[0] == "yes", m[1], m[2], nil
}

func gateway() *Gateway {
	return NewGateway(fakeChecker{members: map[string][3]string{
		"NG-0001": {"yes", "BHCPF-VUL", "2027-12-31"},
		"NG-0003": {"no", "STATE-BASIC", "2026-01-31"},
	}})
}

func TestMenuShownOnSessionStart(t *testing.T) {
	g := gateway()
	reply := g.Handle("s1", "")
	if !strings.HasPrefix(reply, "CON") || !strings.Contains(reply, "Check my coverage") {
		t.Fatalf("bad menu: %q", reply)
	}
}

func TestEligibilityHappyPath(t *testing.T) {
	g := gateway()
	g.Handle("s2", "")
	reply := g.Handle("s2", "1")
	if !strings.Contains(reply, "member ID") {
		t.Fatalf("expected prompt, got %q", reply)
	}
	reply = g.Handle("s2", "1*NG-0001")
	if !strings.HasPrefix(reply, "END") || !strings.Contains(reply, "COVERED under BHCPF-VUL") {
		t.Fatalf("expected coverage verdict, got %q", reply)
	}
	// session must be terminated after the answer
	if _, ok := g.sessions["s2"]; ok {
		t.Fatal("session should be closed after END")
	}
}

func TestInactiveCoverageSaysSo(t *testing.T) {
	g := gateway()
	g.Handle("s3", "")
	g.Handle("s3", "1")
	reply := g.Handle("s3", "1*NG-0003")
	if !strings.Contains(reply, "INACTIVE") {
		t.Fatalf("expected INACTIVE verdict, got %q", reply)
	}
}

func TestUnknownMemberGuidesToEnrol(t *testing.T) {
	g := gateway()
	g.Handle("s4", "")
	g.Handle("s4", "1")
	reply := g.Handle("s4", "1*XX-9999")
	if !strings.Contains(reply, "could not find") {
		t.Fatalf("expected enrolment guidance, got %q", reply)
	}
}

func TestInvalidChoiceReMenus(t *testing.T) {
	g := gateway()
	g.Handle("s5", "")
	reply := g.Handle("s5", "9")
	if !strings.Contains(reply, "Invalid choice") {
		t.Fatalf("expected invalid-choice reply, got %q", reply)
	}
}

func TestConcurrentSessionsIsolated(t *testing.T) {
	g := gateway()
	g.Handle("a", "")
	g.Handle("b", "")
	g.Handle("a", "1")
	replyB := g.Handle("b", "2")
	if !strings.Contains(replyB, "enrolled with") {
		t.Fatalf("sessions must be isolated, got %q", replyB)
	}
}
