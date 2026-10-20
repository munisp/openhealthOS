package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestDetectDangerAcrossLanguages(t *testing.T) {
	cases := map[string]string{
		"I am bleeding heavily since morning":  "heavy_bleeding",
		"Blood dey rush come out":              "heavy_bleeding",
		"Ina zubar jini sosai":                 "heavy_bleeding",
		"Ẹjẹ ń sàn gan-an":                     "heavy_bleeding",
		"Ọbara na-asị ọtụtụ":                   "heavy_bleeding",
		"the baby is not moving since yesterday": "baby_not_moving",
		"she dey convulse":                     "convulsion",
		"my water don break":                   "labour_before_time",
	}
	for text, want := range cases {
		got, ok := detectDanger(text)
		if !ok || got != want {
			t.Errorf("detectDanger(%q) = %q,%v want %q", text, got, ok, want)
		}
	}
}

func TestDetectDangerStaysQuietOnNormalChat(t *testing.T) {
	for _, text := range []string{
		"I want to book an appointment",
		"2", "menu", "sannu",
		"when is my next visit",
		"I feel fine today",
	} {
		if sign, ok := detectDanger(text); ok {
			t.Errorf("false positive on %q: %s", text, sign)
		}
	}
}

func TestDangerShortCircuitsAnyStateAndEscalates(t *testing.T) {
	var gotEscalation map[string]string
	admin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/escalate" {
			t.Errorf("wrong path %s", r.URL.Path)
		}
		json.NewDecoder(r.Body).Decode(&gotEscalation)
		w.WriteHeader(200)
	}))
	defer admin.Close()
	t.Setenv("HUC_ADMIN_URL", admin.URL)
	t.Setenv("HUC_ADMIN_TOKEN", "mgmt-test")

	s := &service{sessions: map[string]*Session{}}
	ss := &Session{Channel: "whatsapp", Phone: "+2348011112222", State: "booking_slot", Data: map[string]string{}}
	reply := s.Handle(ss, "my water don break, baby coming too early")

	if reply != ss.tr("danger_reply") {
		t.Fatalf("expected urgent guidance, got %q", reply)
	}
	if ss.State != StateMenu {
		t.Fatalf("session must reset to menu, got %s", ss.State)
	}
	if gotEscalation["reason"] != "danger_sign:labour_before_time" {
		t.Fatalf("escalation not fired: %+v", gotEscalation)
	}
	if gotEscalation["username"] != "+2348011112222" {
		t.Fatalf("escalation must identify the patient: %+v", gotEscalation)
	}
	if r := gotEscalation; r == nil {
		t.Fatal("no escalation received")
	}
}

func TestDangerEscalationFailSoftWhenAdminDark(t *testing.T) {
	t.Setenv("HUC_ADMIN_URL", "http://127.0.0.1:1") // nothing there
	s := &service{sessions: map[string]*Session{}}
	ss := &Session{Channel: "whatsapp", Phone: "+2348011112222", State: StateMenu, Data: map[string]string{}}
	reply := s.Handle(ss, "I am bleeding heavily")
	if reply == "" {
		t.Fatal("patient must still get urgent guidance when the admin service is dark")
	}
}
