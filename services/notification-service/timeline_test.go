package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func timelineRig(t *testing.T, teleHandler http.HandlerFunc) (*Service, *http.ServeMux) {
	t.Helper()
	tele := httptest.NewServer(teleHandler)
	t.Cleanup(tele.Close)
	t.Setenv("TELEHEALTH_URL", tele.URL)

	svc := NewService(NewMemoryStore(), &LogProvider{})
	svc.stepup.randomPct = 0
	if err := svc.store.PutSubscription(Subscription{
		PatientID: "PT-1", Phone: "+2348030000000", Language: "en",
	}); err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	registerTimeline(mux, server{svc: svc})
	return svc, mux
}

func getTimeline(t *testing.T, mux *http.ServeMux, token string) (*httptest.ResponseRecorder, PortalTimeline) {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/portal/timeline?patient_id=PT-1&phone=%2B2348030000000", nil)
	if token != "" {
		req.Header.Set("X-StepUp-Token", token)
	}
	mux.ServeHTTP(rec, req)
	var tl PortalTimeline
	if rec.Code == http.StatusOK {
		if err := json.NewDecoder(rec.Body).Decode(&tl); err != nil {
			t.Fatalf("decode: %v", err)
		}
	}
	return rec, tl
}

func TestTimelineMergesNotificationsAndConsultsNewestFirst(t *testing.T) {
	svc, mux := timelineRig(t, func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`[{"created_at":"2026-10-19T10:00:00Z","status":"closed","reason":"fever"}]`))
	})
	for _, n := range []Notification{
		{ID: "1", PatientID: "PT-1", Kind: "lab_result", Text: "Lab result ready", State: "sent", CreatedAt: "2026-10-20T08:00:00Z"},
		{ID: "2", PatientID: "PT-1", Kind: "appointment", Text: "Appointment booked", State: "sent", CreatedAt: "2026-10-18T09:00:00Z"},
	} {
		if err := svc.store.Enqueue(n); err != nil {
			t.Fatal(err)
		}
	}

	rec, tl := getTimeline(t, mux, elevate(t, svc, "PT-1"))
	if rec.Code != 200 {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	if !tl.Complete || len(tl.Items) != 3 {
		t.Fatalf("expected 3 complete items, got %+v", tl)
	}
	if tl.Items[0].Kind != "lab_result" || tl.Items[1].Kind != "consult" || tl.Items[2].Kind != "appointment" {
		t.Fatalf("wrong order: %+v", tl.Items)
	}
	if tl.Items[1].Summary != "consult: fever" {
		t.Fatalf("consult summary: %+v", tl.Items[1])
	}
}

func TestTimelineRequiresElevation(t *testing.T) {
	_, mux := timelineRig(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`[]`)) })
	rec, _ := getTimeline(t, mux, "")
	if rec.Code != 428 {
		t.Fatalf("PHI timeline without step-up must 428, got %d", rec.Code)
	}
}

func TestTimelineFailSoftWhenTelehealthDark(t *testing.T) {
	svc, mux := timelineRig(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	})
	rec, tl := getTimeline(t, mux, elevate(t, svc, "PT-1"))
	if rec.Code != 200 || tl.Complete {
		t.Fatalf("dark leg must degrade to complete=false, got %d %+v", rec.Code, tl)
	}
}
