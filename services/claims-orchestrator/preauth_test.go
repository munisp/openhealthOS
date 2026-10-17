package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeAuthorizer struct {
	res map[string]any
	err error
	got PreauthRequest
}

func (f *fakeAuthorizer) Preauth(req PreauthRequest) (map[string]any, error) {
	f.got = req
	return f.res, f.err
}

func TestPreauthEndpoint(t *testing.T) {
	fake := &fakeAuthorizer{res: map[string]any{
		"preauth_id": "PA-AB12CD34EF56", "status": "APPROVED", "estimated_ngn": 4500,
	}}
	h := preauthHandler{Authorizer: fake}
	req := httptest.NewRequest(http.MethodPost, "/preauth",
		strings.NewReader(`{"member_id":"NG-0001","provider_id":"PHC-007","items":[{"code":"ANC-001","qty":1}]}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"status":"APPROVED"`) {
		t.Fatalf("bad preauth response: %d %s", rec.Code, rec.Body)
	}
	if fake.got.MemberID != "NG-0001" || len(fake.got.Items) != 1 || fake.got.Items[0].Code != "ANC-001" {
		t.Fatalf("request not forwarded intact: %+v", fake.got)
	}
}

func TestPreauthValidation(t *testing.T) {
	h := preauthHandler{Authorizer: &fakeAuthorizer{}}
	for _, body := range []string{
		`{"items":[{"code":"ANC-001","qty":1}]}`, // no member
		`{"member_id":"NG-0001"}`,                // no items
	} {
		req := httptest.NewRequest(http.MethodPost, "/preauth", strings.NewReader(body))
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("want 400 for %s, got %d", body, rec.Code)
		}
	}
}

func TestPreauthUpstreamError(t *testing.T) {
	h := preauthHandler{Authorizer: &fakeAuthorizer{err: errors.New("boom")}}
	req := httptest.NewRequest(http.MethodPost, "/preauth",
		strings.NewReader(`{"member_id":"NG-0001","items":[{"code":"ANC-001","qty":1}]}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("want 500, got %d", rec.Code)
	}
}
