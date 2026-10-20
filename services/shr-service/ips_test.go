package main

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/munisp/openhealthOS/shared/shlx"
)

const ipsTestPatient = "OH-NG-KD-0014-000123"

func seedClinical(t *testing.T, s *Store) {
	t.Helper()
	for _, r := range []Resource{
		{ResourceType: "Patient", ID: ipsTestPatient, PatientID: ipsTestPatient, SourceFacility: "NG-KD-0014",
			Body: []byte(`{"resourceType":"Patient","name":[{"text":"Amina Yusuf"}],"birthDate":"1990-04-02","gender":"female"}`)},
		{ResourceType: "Immunization", ID: "imm-1", PatientID: ipsTestPatient, SourceFacility: "NG-KD-0014",
			Body: []byte(`{"resourceType":"Immunization","status":"completed","vaccineCode":{"text":"BCG"}}`)},
		{ResourceType: "Condition", ID: "cond-1", PatientID: ipsTestPatient, SourceFacility: "NG-KD-0014",
			Body: []byte(`{"resourceType":"Condition","code":{"text":"Malaria"}}`)},
		{ResourceType: "Observation", ID: "obs-1", PatientID: ipsTestPatient, SourceFacility: "NG-KD-0014",
			Body: []byte(`{"resourceType":"Observation","code":{"text":"Heart rate"},"valueString":"72 /min"}`)},
	} {
		if err := s.Put(r); err != nil {
			t.Fatalf("seed: %v", err)
		}
	}
}

func ipsTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	s := newStore()
	seedClinical(t, s)
	srv := server{store: s}
	return httptest.NewServer(srv.routes())
}

func TestIPSDocument(t *testing.T) {
	ts := ipsTestServer(t)
	defer ts.Close()
	req, _ := http.NewRequest("GET", ts.URL+"/ips/"+ipsTestPatient+"/document", nil)
	req.Header.Set("X-Patient-Self", ipsTestPatient)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status %d", resp.StatusCode)
	}
	var bundle map[string]any
	json.NewDecoder(resp.Body).Decode(&bundle)
	if bundle["resourceType"] != "Bundle" || bundle["type"] != "document" {
		t.Fatalf("not an IPS document: %v", bundle["resourceType"])
	}
	entries := bundle["entry"].([]any)
	var comp map[string]any
	for _, e := range entries {
		r := e.(map[string]any)["resource"].(map[string]any)
		if r["resourceType"] == "Composition" {
			comp = r
		}
	}
	if comp == nil {
		t.Fatal("no Composition")
	}
	sections := map[string]bool{}
	for _, s := range comp["section"].([]any) {
		sections[s.(map[string]any)["title"].(string)] = true
	}
	for _, req := range []string{"problems", "allergies", "medications", "immunizations", "results"} {
		if !sections[req] {
			t.Fatalf("missing IPS section %s", req)
		}
	}
}

func TestIPSDocumentUnauthorized(t *testing.T) {
	ts := ipsTestServer(t)
	defer ts.Close()
	// stranger facility without consent grant
	req, _ := http.NewRequest("GET", ts.URL+"/ips/"+ipsTestPatient+"/document", nil)
	req.Header.Set("X-Facility-ID", "NG-KD-0001")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 404 {
		t.Fatalf("unconsented access must fail, got %d", resp.StatusCode)
	}
}

func TestSHLRoundtripEndToEnd(t *testing.T) {
	ts := ipsTestServer(t)
	defer ts.Close()
	req, _ := http.NewRequest("GET", ts.URL+"/ips/"+ipsTestPatient+"/shl", nil)
	req.Header.Set("X-Patient-Self", ipsTestPatient)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 201 {
		t.Fatalf("shl mint: %d", resp.StatusCode)
	}
	var out struct {
		Payload map[string]any `json:"payload"`
		Link    string         `json:"link"`
	}
	json.NewDecoder(resp.Body).Decode(&out)
	if !strings.HasPrefix(out.Link, "shlink:/") {
		t.Fatalf("bad link %s", out.Link[:20])
	}
	// the link itself must decode to the same payload (server sent JSON+b64)
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimPrefix(out.Link, "shlink:/"))
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	json.Unmarshal(raw, &payload)
	if payload["key"] != out.Payload["key"] {
		t.Fatal("link payload != response payload")
	}
	// fetch the encrypted file and decrypt with the key from the link
	fresp, err := http.Get(ts.URL + payload["url"].(string))
	if err != nil {
		t.Fatal(err)
	}
	defer fresp.Body.Close()
	var envelope strings.Builder
	buf := make([]byte, 4096)
	for {
		n, err := fresp.Body.Read(buf)
		envelope.Write(buf[:n])
		if err != nil {
			break
		}
	}
	doc, err := shlx.Decrypt(envelope.String(), payload["key"].(string))
	if err != nil {
		t.Fatalf("decrypt: %v", err)
	}
	var bundle map[string]any
	if err := json.Unmarshal(doc, &bundle); err != nil || bundle["resourceType"] != "Bundle" {
		t.Fatalf("decrypted payload is not the IPS: %v", err)
	}
	// wrong key fails closed
	if _, err := shlx.Decrypt(envelope.String(),
		base64.RawURLEncoding.EncodeToString([]byte("01234567890123456789012345678901"))); err == nil {
		t.Fatal("wrong key must fail")
	}
}

func TestSHLFileExpiryHonest(t *testing.T) {
	s := newStore()
	seedClinical(t, s)
	h := newIPSHandler(s, "")
	h.now = func() time.Time { return time.Date(2026, 10, 20, 0, 0, 0, 0, time.UTC) }
	h.files["shl-old"] = shlFile{Envelope: "OHSHL1.x.y", Expires: h.now().Add(-time.Hour)}
	req := httptest.NewRequest("GET", "/shl-file/shl-old", nil)
	rec := httptest.NewRecorder()
	h.shlFileServe(rec, req)
	if rec.Code != 410 {
		t.Fatalf("expired link must be 410, got %d", rec.Code)
	}
}
