package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/munisp/openhealthOS/shared/crdt"
)

func mergeDocsViaHTTP(t *testing.T, srv *httptest.Server, b *crdt.Batch) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(b)
	resp, err := http.Post(srv.URL+"/sync/merge", "application/json", bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("merge: %s", resp.Status)
	}
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	return out
}

func TestCRDTMergeEndpointConverges(t *testing.T) {
	docs := NewMemoryDocStore()
	s := server{store: NewMemoryStore(), docs: docs}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()

	// edge-1 registers the patient offline
	e1 := crdt.NewReplica("edge-1")
	e1.Set("p1", "Patient", "name", "Aisha")
	e1.Add("p1", "Patient", "immunizations", "BCG")
	res := mergeDocsViaHTTP(t, srv, e1.Export())
	if res["merged"].(float64) != 1 {
		t.Fatalf("merged=%v", res)
	}

	// edge-2 (same patient, other facility, months later) adds a fact
	e2 := crdt.NewReplica("edge-2")
	e2.Add("p1", "Patient", "immunizations", "Penta1")
	e2.Set("p1", "Patient", "phone", "+234...")
	mergeDocsViaHTTP(t, srv, e2.Export())

	got := docs.docs["p1"]
	if n := got.SetMembers("immunizations"); len(n) != 2 {
		t.Fatalf("add-only union lost a fact: %v", n)
	}
	if name, _ := got.GetString("name"); name != "Aisha" {
		t.Fatalf("name lost: %q", name)
	}
	if phone, _ := got.GetString("phone"); phone != "+234..." {
		t.Fatalf("phone lost: %q", phone)
	}
}

func TestCRDTMergeIdempotentRedelivery(t *testing.T) {
	docs := NewMemoryDocStore()
	s := server{store: NewMemoryStore(), docs: docs}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()

	e := crdt.NewReplica("edge")
	e.Set("p9", "Patient", "name", "Musa")
	b := e.Export()
	mergeDocsViaHTTP(t, srv, b)
	mergeDocsViaHTTP(t, srv, b) // crash-retry redelivery
	mergeDocsViaHTTP(t, srv, b)

	resp, err := http.Get(srv.URL + "/sync/docs")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var list []map[string]any
	json.NewDecoder(resp.Body).Decode(&list)
	if len(list) != 1 || list[0]["name"] != "Musa" {
		t.Fatalf("redelivery duplicated or lost state: %v", list)
	}
}

func TestCRDTMergeRejectsGarbage(t *testing.T) {
	s := server{store: NewMemoryStore(), docs: NewMemoryDocStore()}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()
	resp, err := http.Post(srv.URL+"/sync/merge", "application/json", bytes.NewReader([]byte("{")))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("want 400, got %s", resp.Status)
	}
}
