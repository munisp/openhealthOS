package main

// Step-up / random re-auth gate tests: challenge delivery over the provider
// chain, fail-closed verify, burn-on-use, attempt ceiling, token TTL, and
// the random-reauth roll that keeps stolen sessions unreliable.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func stepUpRig(t *testing.T, pct int) (*Service, *LogProvider, *http.ServeMux) {
	t.Helper()
	lp := &LogProvider{}
	svc := NewService(NewMemoryStore(), lp)
	svc.stepup.randomPct = pct
	if err := svc.store.PutSubscription(Subscription{
		PatientID: "PT-1", Phone: "+2348030000000", Language: "en",
	}); err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	server{svc: svc}.registerStepUp(mux)
	return svc, lp, mux
}

func post(t *testing.T, mux *http.ServeMux, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, path, strings.NewReader(body)))
	return rec
}

func TestStepUpChallengeDeliversOverProviderChain(t *testing.T) {
	svc, lp, mux := stepUpRig(t, 0)

	rec := post(t, mux, "/portal/stepup/challenge", `{"patient_id":"PT-1","phone":"08030000000"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("challenge: %d %s", rec.Code, rec.Body)
	}
	if len(lp.Sent) != 1 || !strings.Contains(lp.Sent[0][1], "security code") {
		t.Fatalf("code must ride the provider chain: %+v", lp.Sent)
	}
	// the code on the wire is never the code at rest
	code := strings.Split(strings.Split(lp.Sent[0][1], "code: ")[1], " ")[0]
	if svc.stepup.challenges["PT-1"].hash == hashStep("000000") {
		t.Fatal("sanity")
	}
	if hashStep(code) != svc.stepup.challenges["PT-1"].hash {
		t.Fatal("stored value must be the code hash, not the code")
	}

	// wrong code → 404, indistinguishable
	rec = post(t, mux, "/portal/stepup/verify", `{"patient_id":"PT-1","phone":"08030000000","code":"999999"}`)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("wrong code must 404, got %d", rec.Code)
	}

	// right code → elevated token
	rec = post(t, mux, "/portal/stepup/verify",
		`{"patient_id":"PT-1","phone":"08030000000","code":"`+code+`"}`)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "stepup_token") {
		t.Fatalf("verify: %d %s", rec.Code, rec.Body)
	}

	// the challenge burned — same code cannot verify twice
	rec = post(t, mux, "/portal/stepup/verify",
		`{"patient_id":"PT-1","phone":"08030000000","code":"`+code+`"}`)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("replayed code must 404, got %d", rec.Code)
	}
}

func TestStepUpChallengeUnknownPatientFailsClosed(t *testing.T) {
	_, lp, mux := stepUpRig(t, 0)
	rec := post(t, mux, "/portal/stepup/challenge", `{"patient_id":"GHOST","phone":"08030000000"}`)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown patient must 404, got %d", rec.Code)
	}
	if len(lp.Sent) != 0 {
		t.Fatal("nothing must be sent to an unbound pair")
	}
}

func TestStepUpAttemptCeilingKillsChallenge(t *testing.T) {
	svc, _, mux := stepUpRig(t, 0)
	post(t, mux, "/portal/stepup/challenge", `{"patient_id":"PT-1","phone":"08030000000"}`)
	for i := 0; i < stepUpMaxTries; i++ {
		post(t, mux, "/portal/stepup/verify", `{"patient_id":"PT-1","phone":"08030000000","code":"000000"}`)
	}
	if _, ok := svc.stepup.challenges["PT-1"]; ok {
		t.Fatal("challenge must be destroyed after max attempts")
	}
}

func TestRandomReauthBouncesValidTokens(t *testing.T) {
	svc, _, _ := stepUpRig(t, 100) // always bounce
	tok := elevate(t, svc, "PT-1")
	if ok, why := svc.stepup.elevated("PT-1", tok); ok || why != "random_reauth" {
		t.Fatalf("100%% random re-auth must bounce: %v %s", ok, why)
	}
	// and at 0% a valid token sails through
	svc.stepup.randomPct = 0
	if ok, _ := svc.stepup.elevated("PT-1", tok); !ok {
		t.Fatal("valid token with 0% random must pass")
	}
}
