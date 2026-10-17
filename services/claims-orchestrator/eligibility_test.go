package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeChecker struct {
	res map[string]any
	err error
}

func (f fakeChecker) CheckEligibility(memberID, serviceDate string) (map[string]any, error) {
	return f.res, f.err
}

func TestEligibilityEndpoint(t *testing.T) {
	h := eligibilityHandler{Checker: fakeChecker{res: map[string]any{
		"member_id": "NG-0001", "eligible": true, "scheme": "BHCPF-VUL", "source": "live",
	}}}
	req := httptest.NewRequest(http.MethodPost, "/eligibility",
		strings.NewReader(`{"member_id":"NG-0001","service_date":"2026-10-17"}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"eligible":true`) {
		t.Fatalf("bad eligibility response: %d %s", rec.Code, rec.Body)
	}
}

func TestEligibilityRequiresMemberID(t *testing.T) {
	h := eligibilityHandler{Checker: fakeChecker{}}
	req := httptest.NewRequest(http.MethodPost, "/eligibility", strings.NewReader(`{}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 400 {
		t.Fatalf("missing member_id must 400, got %d", rec.Code)
	}
}
