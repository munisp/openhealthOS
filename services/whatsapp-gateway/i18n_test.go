package main

import (
	"strings"
	"testing"
)

func TestLanguagePickerFlowToHausa(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "l1", from, "7"), nil)
	if !strings.Contains(cs.last(), "Hausa") {
		t.Fatalf("picker missing languages: %q", cs.last())
	}
	post(t, srv, waBody(t, "l2", from, "3"), nil) // Hausa
	last := cs.last()
	if !strings.Contains(last, "Hausa") || !strings.Contains(last, "Yi rajistar") {
		t.Fatalf("menu not in Hausa: %q", last)
	}
	// Session stays in Hausa for the next action.
	post(t, srv, waBody(t, "l3", from, "3"), nil) // coverage in Hausa
	if !strings.Contains(cs.last(), "Kana da rufe") {
		t.Fatalf("coverage not in Hausa: %q", cs.last())
	}
}

func TestLanguageKeywordFromAnywhere(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "k1", from, "1"), nil) // enter booking
	post(t, srv, waBody(t, "k2", from, "pidgin"), nil)
	if !strings.Contains(cs.last(), "Naija") {
		t.Fatalf("keyword switch failed: %q", cs.last())
	}
	post(t, srv, waBody(t, "k3", from, "0"), nil)
	if !strings.Contains(cs.last(), "Show dis menu") {
		t.Fatalf("menu not in Pidgin: %q", cs.last())
	}
}

func TestBookingInPidgin(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "p1", from, "naija"), nil)
	post(t, srv, waBody(t, "p2", from, "1"), nil) // book
	if !strings.Contains(cs.last(), "Where you wan book") {
		t.Fatalf("booking not in Pidgin: %q", cs.last())
	}
	post(t, srv, waBody(t, "p3", from, "1"), nil)
	post(t, srv, waBody(t, "p4", from, "1"), nil)
	post(t, srv, waBody(t, "p5", from, "1"), nil)
	if !strings.Contains(cs.last(), "Book %s") && !strings.Contains(cs.last(), "Yes, book am") {
		t.Fatalf("confirm not in Pidgin: %q", cs.last())
	}
	post(t, srv, waBody(t, "p6", from, "1"), nil)
	if !strings.Contains(cs.last(), "Booked:") {
		t.Fatalf("booking failed in Pidgin: %q", cs.last())
	}
}

func TestEveryKeyHasEnglish(t *testing.T) {
	for lang, pack := range msgs {
		for key := range pack {
			if _, ok := msgs["en"][key]; !ok {
				t.Errorf("%s has key %s missing from English fallback", lang, key)
			}
		}
	}
	// And the five platform languages are all present.
	for _, l := range []string{"en", "pcm", "ha", "yo", "ig"} {
		if msgs[l]["menu"] == "" {
			t.Errorf("language %s has no menu", l)
		}
	}
}

func TestFallbackCoversMissingTranslation(t *testing.T) {
	ss := &Session{Lang: "yo"}
	delete(msgs["yo"], "ask_sent") // simulate a missing key
	defer func() { msgs["yo"]["ask_sent"] = msgs["en"]["ask_sent"] }()
	if got := ss.tr("ask_sent"); got != msgs["en"]["ask_sent"] {
		t.Fatalf("fallback broken: %q", got)
	}
}
