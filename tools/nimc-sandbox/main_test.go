package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func verify(t *testing.T, h http.Handler, token, body string) (int, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/verify", strings.NewReader(body))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	var out map[string]any
	json.NewDecoder(rr.Body).Decode(&out)
	return rr.Code, out
}

func TestVerifyContract(t *testing.T) {
	h := newSandboxHandler(defaultFixtures, "sekrit")

	// Happy path: known NIN + matching DOB.
	code, out := verify(t, h, "sekrit", `{"nin":"10000000001","dob":"1990-04-12"}`)
	if code != 200 || out["match"] != true || out["name"] != "Adaeze Ngozi Okafor" {
		t.Fatalf("match: %d %v", code, out)
	}
	// DOB omitted: identity match alone suffices (progressive enrolment).
	code, out = verify(t, h, "sekrit", `{"nin":"10000000002"}`)
	if code != 200 || out["match"] != true {
		t.Fatalf("no-dob: %d %v", code, out)
	}
	// DOB mismatch.
	code, out = verify(t, h, "sekrit", `{"nin":"10000000001","dob":"1970-01-01"}`)
	if code != 200 || out["match"] != false || out["name"] != "Adaeze Ngozi Okafor" {
		t.Fatalf("dob mismatch: %d %v", code, out)
	}
	// Unknown NIN.
	code, out = verify(t, h, "sekrit", `{"nin":"99999999999"}`)
	if code != 200 || out["match"] != false {
		t.Fatalf("unknown: %d %v", code, out)
	}
	// Bad NIN format → 400.
	code, _ = verify(t, h, "sekrit", `{"nin":"123"}`)
	if code != http.StatusBadRequest {
		t.Fatalf("bad nin: %d", code)
	}
	// Wrong token → 401; right token works.
	code, _ = verify(t, h, "wrong", `{"nin":"10000000001"}`)
	if code != http.StatusUnauthorized {
		t.Fatalf("wrong token: %d", code)
	}
	// Malformed JSON → 400.
	code, _ = verify(t, h, "sekrit", `{`)
	if code != http.StatusBadRequest {
		t.Fatalf("malformed: %d", code)
	}
	// GET → 405.
	req := httptest.NewRequest(http.MethodGet, "/verify", nil)
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("get: %d", rr.Code)
	}
}

func TestHealthz(t *testing.T) {
	h := newSandboxHandler(defaultFixtures, "")
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 || !strings.Contains(rr.Body.String(), `"identities":5`) {
		t.Fatalf("healthz: %d %s", rr.Code, rr.Body.String())
	}
}

func TestLoadFixturesValidates(t *testing.T) {
	if _, err := loadFixtures("/nonexistent"); err == nil {
		t.Fatal("missing file should fail")
	}
}
