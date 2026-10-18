package wafx

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func okHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte(`{"ok":true}`))
	})
}

func exercise(t *testing.T, p Policy, req *http.Request) *httptest.ResponseRecorder {
	t.Helper()
	rr := httptest.NewRecorder()
	Middleware(okHandler(), p).ServeHTTP(rr, req)
	return rr
}

func TestMethodAllowlist(t *testing.T) {
	req := httptest.NewRequest(http.MethodTrace, "/api/claims", nil)
	rr := exercise(t, Policy{}, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("TRACE: want 405, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "method_not_allowed") {
		t.Fatalf("body: %s", rr.Body.String())
	}
	if rr.Header().Get("Allow") == "" {
		t.Fatal("405 must set Allow")
	}
}

func TestContentTypeEnforcement(t *testing.T) {
	// Body without content type → 415
	req := httptest.NewRequest(http.MethodPost, "/api/claims", strings.NewReader(`{"a":1}`))
	rr := exercise(t, Policy{}, req)
	if rr.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("no content type: want 415, got %d", rr.Code)
	}
	// JSON passes
	req = httptest.NewRequest(http.MethodPost, "/api/claims", strings.NewReader(`{"a":1}`))
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	rr = exercise(t, Policy{}, req)
	if rr.Code != 200 {
		t.Fatalf("json: want 200, got %d (%s)", rr.Code, rr.Body.String())
	}
	// FHIR content type passes
	req = httptest.NewRequest(http.MethodPost, "/api/fhir/Patient", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/fhir+json")
	rr = exercise(t, Policy{}, req)
	if rr.Code != 200 {
		t.Fatalf("fhir+json: want 200, got %d", rr.Code)
	}
	// Form post passes (USSD webhooks)
	req = httptest.NewRequest(http.MethodPost, "/api/ussd/callback", strings.NewReader("sessionId=1&text=2"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	rr = exercise(t, Policy{}, req)
	if rr.Code != 200 {
		t.Fatalf("form: want 200, got %d", rr.Code)
	}
	// GET with query string is not content-type checked
	req = httptest.NewRequest(http.MethodGet, "/api/registry/facilities/search?q=general", nil)
	rr = exercise(t, Policy{}, req)
	if rr.Code != 200 {
		t.Fatalf("get: want 200, got %d", rr.Code)
	}
}

func TestBodySizeCap(t *testing.T) {
	// Declared too large → 413 before reading a byte
	big := Policy{MaxBodyBytes: 16}
	req := httptest.NewRequest(http.MethodPost, "/api/claims", strings.NewReader(`{"a":12345678901234567890}`))
	req.Header.Set("Content-Type", "application/json")
	rr := exercise(t, big, req)
	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("declared oversize: want 413, got %d", rr.Code)
	}
	// Chunked liar (ContentLength = -1): reader cap cuts the stream and
	// the handler observes the truncation.
	handler := Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, err := io.ReadAll(r.Body)
		if err == nil {
			t.Error("oversize chunked body read without error")
		}
		w.WriteHeader(http.StatusRequestEntityTooLarge)
	}), big)
	rr = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/api/claims", strings.NewReader(`{"a":12345678901234567890}`))
	req.Header.Set("Content-Type", "application/json")
	req.ContentLength = -1
	handler.ServeHTTP(rr, req)
	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("chunked oversize: want 413, got %d", rr.Code)
	}
}

func TestProbeScreening(t *testing.T) {
	blocked := []string{
		"/api/registry/facilities/search?q=x%27%20OR%201%3D1", // ' OR 1=1
		"/api/registry/facilities/search?q=x+UNION+SELECT+password",
		"/api/registry/facilities/search?q=%27%3B+DROP+TABLE+patients",
		"/api/registry/facilities/search?q=sleep(10)",
		"/api/claims/../../etc/passwd",
		"/api/claims/%2e%2e%2fetc%2fpasswd",
		"/api/registry/facilities/search?q=%3Cscript%3Ealert(1)%3C/script%3E",
		"/api/registry/facilities/search?q=%00",
	}
	for _, path := range blocked {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		rr := exercise(t, Policy{}, req)
		if rr.Code != http.StatusBadRequest {
			t.Errorf("%s: want 400, got %d", path, rr.Code)
		}
		if !strings.Contains(rr.Body.String(), "probe_blocked") {
			t.Errorf("%s: missing code in body %s", path, rr.Body.String())
		}
	}
}

// Clinical reality: apostrophes in names, "selection" in prose, dashes
// in codes — none of these may trip the screen.
func TestLegitimateQueriesPass(t *testing.T) {
	legit := []string{
		"/api/registry/facilities/search?q=O'Brien+Memorial",
		"/api/registry/facilities/search?q=selection+of+services",
		"/api/registry/facilities/search?q=St.+Mary%27s+PHC+Lagos",
		"/api/fhir/Patient?name=Adaeze+Okafor--Eze",
		"/api/registry/facilities/near?lat=6.45&lon=3.39&radius=25",
	}
	for _, path := range legit {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		rr := exercise(t, Policy{}, req)
		if rr.Code != 200 {
			t.Errorf("%s: legit query blocked with %d", path, rr.Code)
		}
	}
}

func TestOnRejectSeam(t *testing.T) {
	var reasons []string
	p := Policy{OnReject: func(reason string, _ *http.Request) { reasons = append(reasons, reason) }}
	req := httptest.NewRequest(http.MethodDelete, "/x", nil)
	req.Method = http.MethodTrace
	exercise(t, p, req)
	if len(reasons) != 1 || reasons[0] != "method not allowed" {
		t.Fatalf("OnReject: %v", reasons)
	}
}
