package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func ev(id, typ string, payload map[string]any) Event {
	return Event{ID: id, Facility: "F-1", Type: typ, TS: "2026-10-18T09:00:00Z", Payload: payload}
}

func TestClassify(t *testing.T) {
	cases := []struct {
		ev    Event
		class string
	}{
		{ev("1", "referral", nil), Critical},
		{ev("2", "encounter", map[string]any{"danger": true}), Critical},
		{ev("3", "danger_sign_referral", nil), Critical},
		{ev("4", "encounter", nil), Standard},
		{ev("5", "claim_submitted", nil), Standard},
		{ev("6", "prescription", nil), Standard},
		{ev("7", "telemetry", nil), Bulk},
		{ev("8", "equipment_ping", nil), Bulk},
	}
	for _, c := range cases {
		if got := Classify(c.ev); got != c.class {
			t.Errorf("%s: got %s want %s", c.ev.Type, got, c.class)
		}
	}
}

func TestDedupe(t *testing.T) {
	s := NewMemoryStore()
	fresh, _ := s.Add(ev("a", "encounter", nil))
	if !fresh {
		t.Fatal("first add should be fresh")
	}
	fresh, _ = s.Add(ev("a", "encounter", nil))
	if fresh {
		t.Fatal("duplicate id must not re-buffer")
	}
	c, st, b := s.PendingCounts()
	if c+st+b != 1 {
		t.Fatalf("pending=%d want 1", c+st+b)
	}
}

func TestPriorityForwardingOrder(t *testing.T) {
	var got []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, line := range strings.Split(strings.TrimSpace(readAll(r)), "\n") {
			var e Event
			_ = json.Unmarshal([]byte(line), &e)
			got = append(got, e.ID)
		}
		w.Write([]byte(`{"received":1}`))
	}))
	defer up.Close()

	s := NewMemoryStore()
	// insert bulk first, critical last — order in must not matter
	_ = mustAdd(s, ev("bulk-1", "telemetry", nil))
	_ = mustAdd(s, ev("std-1", "encounter", nil))
	_ = mustAdd(s, ev("crit-1", "referral", nil))

	h := &hub{store: s, upstream: up.URL, hubID: "test"}
	n := h.forwardOnce(500)
	if n != 3 {
		t.Fatalf("forwarded %d want 3", n)
	}
	if got[0] != "crit-1" {
		t.Fatalf("critical must forward first, got order %v", got)
	}
	if got[len(got)-1] != "bulk-1" {
		t.Fatalf("bulk must forward last, got order %v", got)
	}
	if c, st, b := s.PendingCounts(); c+st+b != 0 {
		t.Fatalf("pending after forward: %d/%d/%d", c, st, b)
	}
}

func TestUpstreamDownBuffersEverything(t *testing.T) {
	s := NewMemoryStore()
	_ = mustAdd(s, ev("c1", "referral", nil))
	h := &hub{store: s, upstream: "http://127.0.0.1:1", hubID: "test"} // dead
	if n := h.forwardOnce(500); n != 0 {
		t.Fatalf("forwarded to dead upstream: %d", n)
	}
	c, _, _ := s.PendingCounts()
	if c != 1 {
		t.Fatalf("critical event lost on upstream failure: %d", c)
	}
}

func TestFileStoreSurvivesRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "hub.jsonl")
	s1, err := NewFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	_ = mustAdd(s1, ev("f1", "encounter", nil))
	_ = mustAdd(s1, ev("f2", "referral", nil))
	_ = s1.MarkForwarded([]string{"f1"})
	s1.Close()

	s2, err := NewFileStore(path) // power cut + reboot
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	fresh, _ := s2.Add(ev("f1", "encounter", nil))
	if fresh {
		t.Fatal("replayed store must remember seen ids")
	}
	c, st, b := s2.PendingCounts()
	if c != 1 || st != 0 || b != 0 {
		t.Fatalf("pending after restart: %d/%d/%d want 1/0/0", c, st, b)
	}
}

func TestIngestEndpointIdempotent(t *testing.T) {
	s := NewMemoryStore()
	h := &hub{store: s, hubID: "test"}
	ts := httptest.NewServer(h.routes())
	defer ts.Close()

	body := `{"id":"e1","facility":"F-1","type":"encounter","ts":"2026-10-18T09:00:00Z"}` + "\n" +
		`{"id":"e2","facility":"F-1","type":"telemetry","ts":"2026-10-18T09:00:01Z","payload":{"metric":"fridge_temp","value":4.2}}` + "\n"
	for i := 0; i < 2; i++ { // send twice — agents retry freely
		resp, err := ts.Client().Post(ts.URL+"/ingest/raw", "application/x-ndjson", strings.NewReader(body))
		if err != nil || resp.StatusCode != 200 {
			t.Fatalf("ingest %d: %v %d", i, err, resp.StatusCode)
		}
		var ack struct {
			Received   int `json:"received"`
			Duplicates int `json:"duplicates"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&ack)
		resp.Body.Close()
		if i == 0 && ack.Received != 2 {
			t.Fatalf("first batch received=%d want 2", ack.Received)
		}
		if i == 1 && ack.Duplicates != 2 {
			t.Fatalf("second batch duplicates=%d want 2", ack.Duplicates)
		}
	}
	_, st, b := s.PendingCounts()
	if st != 1 || b != 1 {
		t.Fatalf("pending standard=%d bulk=%d want 1/1", st, b)
	}
}

func mustAdd(s Store, e Event) error {
	if _, err := s.Add(e); err != nil {
		return err
	}
	return nil
}

func readAll(r *http.Request) string {
	var buf bytes.Buffer
	_, _ = buf.ReadFrom(r.Body)
	return buf.String()
}

var _ = fmt.Sprintf // keep fmt import stable across test edits
var _ = os.Getenv

func TestFileStoreSealedAtRest(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/spill.jsonl"
	t.Setenv("SEALX_KEY", "0011223344556677889900112233445566778899001122334455667788990011")

	s, err := NewFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	ok, _ := s.Add(Event{ID: "ev-secret", Facility: "F1", Type: "encounter",
		Payload: map[string]any{"diagnosis": "measles"}})
	if !ok {
		t.Fatal("add")
	}
	s.Close()

	// Disk holds no plaintext PHI.
	raw, _ := os.ReadFile(path)
	if string(raw) == "" || !strings.HasPrefix(strings.TrimSpace(string(raw)), "SX1:") {
		t.Fatalf("spill should be sealed, got: %.40s", raw)
	}
	if strings.Contains(string(raw), "measles") {
		t.Fatal("PHI leaked to disk in plaintext")
	}

	// Restart with the same key: event replays fine.
	s2, err := NewFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	if n := len(s2.Pending(Critical, 10)) + len(s2.Pending(Standard, 10)) + len(s2.Pending(Bulk, 10)); n != 1 {
		t.Fatalf("sealed replay: want 1 pending, got %d", n)
	}
	s2.Close()

	// Wrong key: boot must not crash (line skipped), and no plaintext served.
	t.Setenv("SEALX_KEY", "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")
	s3, err := NewFileStore(path)
	if err != nil {
		t.Fatalf("wrong key must not crash boot: %v", err)
	}
	if n := len(s3.Pending(Standard, 10)); n != 0 {
		t.Fatalf("wrong key must yield no plaintext, got %d pending", n)
	}
	s3.Close()
}
