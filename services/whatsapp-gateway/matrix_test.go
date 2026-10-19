package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeHomeserver serves /sync with a scripted event queue and captures
// outbound sends.
type fakeHomeserver struct {
	mu     sync.Mutex
	events []matrixEvent
	sent   []string // "roomID|body"
	sends  int
}

func (f *fakeHomeserver) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /_matrix/client/v3/account/whoami", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{"user_id": "@oh-bot:hs"})
	})
	mux.HandleFunc("GET /_matrix/client/v3/sync", func(w http.ResponseWriter, _ *http.Request) {
		f.mu.Lock()
		evs := f.events
		f.events = nil
		f.mu.Unlock()
		rooms := map[string]any{}
		if len(evs) > 0 {
			rooms["!room1:hs"] = map[string]any{"timeline": map[string]any{"events": evs}}
		}
		json.NewEncoder(w).Encode(map[string]any{
			"next_batch": "n" + time.Now().Format("150405.000000"),
			"rooms":      map[string]any{"join": rooms},
		})
	})
	mux.HandleFunc("PUT /_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Body string `json:"body"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		f.sent = append(f.sent, r.PathValue("room")+"|"+body.Body)
		f.mu.Unlock()
		json.NewEncoder(w).Encode(map[string]string{"event_id": "$ok"})
	})
	return mux
}

func (f *fakeHomeserver) inject(id, sender, body string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.events = append(f.events, matrixEvent{EventID: id, Sender: sender, Type: "m.room.message",
		Content: struct {
			MsgType string `json:"msgtype"`
			Body    string `json:"body"`
		}{MsgType: "m.text", Body: body}})
}

func (f *fakeHomeserver) last() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.sent) == 0 {
		return ""
	}
	return f.sent[len(f.sent)-1]
}

func newMatrixTestRig(t *testing.T) (*matrixListener, *fakeHomeserver) {
	t.Helper()
	stub := stubPlatform(t)
	t.Cleanup(stub.Close)
	hs := &fakeHomeserver{}
	hsSrv := httptest.NewServer(hs.handler())
	t.Cleanup(hsSrv.Close)
	svc := &service{
		sender: logSender{}, verifyTok: "vtok",
		registry: stub.URL, portal: stub.URL, wellness: stub.URL,
		sessions: map[string]*Session{}, seen: map[string]time.Time{},
	}
	l := newMatrixListener(svc, hsSrv.URL, "tok")
	return l, hs
}

func mxMsg(t *testing.T, l *matrixListener, hs *fakeHomeserver, id, body string) {
	t.Helper()
	hs.inject(id, "@amina:hs", body)
	if err := l.syncOnce(); err != nil {
		t.Fatal(err)
	}
}

func TestMatrixBindingThenMenu(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	mxMsg(t, l, hs, "e1", "hi")
	if !strings.Contains(hs.last(), "patient ID") {
		t.Fatalf("want ID prompt, got %q", hs.last())
	}
	mxMsg(t, l, hs, "e2", "OH-KD-0001")
	if !strings.Contains(hs.last(), "phone number") {
		t.Fatalf("want phone prompt, got %q", hs.last())
	}
	mxMsg(t, l, hs, "e3", "08012345678")
	if !strings.Contains(hs.last(), "Book an appointment") {
		t.Fatalf("want menu after binding, got %q", hs.last())
	}
	// Bound: coverage works.
	mxMsg(t, l, hs, "e4", "3")
	if !strings.Contains(hs.last(), "covered") {
		t.Fatalf("coverage over matrix failed: %q", hs.last())
	}
}

func TestMatrixBindingOneMessage(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	mxMsg(t, l, hs, "e1", "hi")
	mxMsg(t, l, hs, "e2", "OH-KD-0001 08012345678")
	if !strings.Contains(hs.last(), "Book an appointment") {
		t.Fatalf("one-message binding failed: %q", hs.last())
	}
}

func TestMatrixBindingRejectsWrongPair(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	mxMsg(t, l, hs, "e1", "hi")
	mxMsg(t, l, hs, "e2", "OH-KD-0001")
	mxMsg(t, l, hs, "e3", "08099999999")
	last := hs.last()
	if !strings.Contains(last, "don't match") {
		t.Fatalf("wrong pair must fail closed: %q", last)
	}
	if strings.Contains(last, "eligible") || strings.Contains(last, "NHIA") {
		t.Fatal("wrong pair leaked coverage data")
	}
}

func TestMatrixEventDedupe(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	hs.inject("same-id", "@amina:hs", "hi")
	hs.inject("same-id", "@amina:hs", "hi")
	if err := l.syncOnce(); err != nil {
		t.Fatal(err)
	}
	hs.mu.Lock()
	n := len(hs.sent)
	hs.mu.Unlock()
	if n != 1 {
		t.Fatalf("duplicate event id produced %d replies", n)
	}
}

func TestMatrixBookingFlow(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	mxMsg(t, l, hs, "b1", "hi")
	mxMsg(t, l, hs, "b2", "OH-KD-0001 08012345678")
	mxMsg(t, l, hs, "b3", "1")
	if !strings.Contains(hs.last(), "Kawo PHC") {
		t.Fatalf("facilities: %q", hs.last())
	}
	mxMsg(t, l, hs, "b4", "1")
	mxMsg(t, l, hs, "b5", "1")
	mxMsg(t, l, hs, "b6", "1")
	if !strings.Contains(hs.last(), "Book Kawo PHC") {
		t.Fatalf("confirm: %q", hs.last())
	}
	mxMsg(t, l, hs, "b7", "1")
	if !strings.Contains(hs.last(), "Booked:") {
		t.Fatalf("booking over matrix failed: %q", hs.last())
	}
}

func TestMatrixIgnoresOwnMessages(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	hs.inject("self1", "@oh-bot:hs", "hi")
	if err := l.syncOnce(); err != nil {
		t.Fatal(err)
	}
	if hs.last() != "" {
		t.Fatal("bot replied to itself")
	}
}

func TestMatrixLanguageOnSovereignChannel(t *testing.T) {
	l, hs := newMatrixTestRig(t)
	mxMsg(t, l, hs, "z1", "harshe")
	if !strings.Contains(hs.last(), "Hausa") {
		t.Fatalf("picker must work pre-binding: %q", hs.last())
	}
	mxMsg(t, l, hs, "z2", "3") // Hausa
	if !strings.Contains(hs.last(), "Yi rajistar") {
		t.Fatalf("menu in Hausa pre-binding: %q", hs.last())
	}
}
