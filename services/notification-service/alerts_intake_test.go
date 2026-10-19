package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAlertsIntakeDeliversPerTenant(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	mux := http.NewServeMux()
	registerAlertsIntake(mux, svc)
	t.Setenv("ALERT_OPS_CONTACTS", "+2348000000001")
	t.Setenv("ALERT_OPS_CONTACTS_TENANT_LAGOS", "+2348000000002")

	body := `{"status":"firing","alerts":[
		{"status":"firing","labels":{"alertname":"TenantErrorRateHigh","severity":"critical","tenant":"lagos"},
		 "annotations":{"summary":"tenant lagos 5xx rate >5%"}}]}`
	req := httptest.NewRequest("POST", "/alerts/intake", strings.NewReader(body))
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	if !strings.Contains(rec.Body.String(), `"delivered":1`) {
		t.Fatalf("expected delivery to lagos contact: %s", rec.Body)
	}
}

func TestAlertsIntakeFailLoudWithoutContacts(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	mux := http.NewServeMux()
	registerAlertsIntake(mux, svc)

	body := `{"status":"firing","alerts":[
		{"status":"firing","labels":{"alertname":"PostgresConnectionsExhausted","severity":"critical"}}]}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/alerts/intake", strings.NewReader(body)))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("no contacts must 503 so Alertmanager retries, got %d", rec.Code)
	}
}

func TestAlertsIntakeRejectsBadJSON(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	mux := http.NewServeMux()
	registerAlertsIntake(mux, svc)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/alerts/intake", strings.NewReader("{")))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("bad json must 400, got %d", rec.Code)
	}
}
