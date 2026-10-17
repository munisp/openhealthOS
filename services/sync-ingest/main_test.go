package main

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func gzLines(t *testing.T, lines ...string) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	for _, l := range lines {
		fmt.Fprintln(w, l)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return &buf
}

const ev1 = `{"id":"E-1","facility":"PHC-007","type":"encounter","ts":"2026-10-17T09:00:00Z"}`
const ev2 = `{"id":"E-2","facility":"PHC-007","type":"dispensing","ts":"2026-10-17T09:05:00Z"}`

func TestIngestAppliesOnce(t *testing.T) {
	store := NewMemoryStore()
	res, err := IngestBatch(gzLines(t, ev1, ev2), store)
	if err != nil || res.Applied != 2 {
		t.Fatalf("first batch: %+v err=%v", res, err)
	}
	// edge retries the same batch after a connectivity flap (at-least-once)
	res, err = IngestBatch(gzLines(t, ev1, ev2), store)
	if err != nil {
		t.Fatal(err)
	}
	if res.Applied != 0 || res.Duplicates != 2 {
		t.Fatalf("replay must apply nothing twice: %+v", res)
	}
	if len(store.Events()) != 2 {
		t.Fatal("exactly-once violated")
	}
}

func TestRejectsEventsWithoutID(t *testing.T) {
	_, err := IngestBatch(gzLines(t, `{"facility":"PHC-007","type":"encounter","ts":"x"}`), NewMemoryStore())
	if err == nil {
		t.Fatal("events without id cannot be idempotent — must be rejected")
	}
}

func TestRejectsBadGzip(t *testing.T) {
	_, err := IngestBatch(strings.NewReader("not gzip"), NewMemoryStore())
	if err == nil {
		t.Fatal("bad gzip must fail loudly")
	}
}

func TestHTTPEndpoints(t *testing.T) {
	s := server{store: NewMemoryStore()}
	req := httptest.NewRequest(http.MethodPost, "/ingest", gzLines(t, ev1))
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("ingest: %d %s", rec.Code, rec.Body)
	}
	var res IngestResult
	json.Unmarshal(rec.Body.Bytes(), &res)
	if res.Applied != 1 {
		t.Fatalf("want 1 applied, got %+v", res)
	}
	req = httptest.NewRequest(http.MethodGet, "/metrics", nil)
	rec = httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if !strings.Contains(rec.Body.String(), `"events_applied":1`) {
		t.Fatalf("metrics wrong: %s", rec.Body)
	}
}
