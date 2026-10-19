package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newTestServer() server {
	return server{store: NewMemoryStore(), apiKey: "devkey", apiSecret: "devsecret"}
}

func post(t *testing.T, s server, path, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	req := httptest.NewRequest("POST", path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rec, req)
	var out map[string]any
	json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func createConsult(t *testing.T, s server) string {
	t.Helper()
	rec, out := post(t, s, "/consults", `{"patient_id":"PAT-1","facility_id":"F1",
		"complaint":"rash on arm, 3 days","consent":true,"actor":"PAT-1"}`)
	if rec.Code != 201 {
		t.Fatalf("create: %d %v", rec.Code, out)
	}
	return out["id"].(string)
}

func TestAsyncConsultLifecycle(t *testing.T) {
	s := newTestServer()
	id := createConsult(t, s)
	if id != "TC-00001" {
		t.Fatalf("seq id: %s", id)
	}
	// triage -> review -> clinician message flips to advice_given
	if rec, out := post(t, s, "/consults/"+id+"/transition", `{"to":"triaged"}`); rec.Code != 200 {
		t.Fatalf("triage: %v", out)
	}
	post(t, s, "/consults/"+id+"/transition", `{"to":"in_review","clinician":"dr-a"}`)
	_, out := post(t, s, "/consults/"+id+"/messages",
		`{"author":"dr-a","role":"clinician","body":"likely contact dermatitis; apply hydrocortisone"}`)
	if out["state"] != StAdviceGiven {
		t.Fatalf("clinician advice flips state: %v", out["state"])
	}
	post(t, s, "/consults/"+id+"/transition", `{"to":"completed"}`)
	// closed threads reject messages
	if rec, _ := post(t, s, "/consults/"+id+"/messages",
		`{"author":"PAT-1","role":"patient","body":"thanks"}`); rec.Code != 409 {
		t.Fatalf("closed thread rejects messages: %d", rec.Code)
	}
}

func TestConsentRequired(t *testing.T) {
	s := newTestServer()
	rec, out := post(t, s, "/consults",
		`{"patient_id":"P1","complaint":"x","consent":false}`)
	if rec.Code != 422 || !strings.Contains(out["error"].(string), "consent") {
		t.Fatalf("consent enforced: %d %v", rec.Code, out)
	}
}

func TestIllegalTransition(t *testing.T) {
	s := newTestServer()
	id := createConsult(t, s)
	rec, out := post(t, s, "/consults/"+id+"/transition", `{"to":"completed"}`)
	if rec.Code != 409 || !strings.Contains(out["error"].(string), "illegal") {
		t.Fatalf("submitted->completed blocked: %d %v", rec.Code, out)
	}
}

func TestEscalationRequiresRoomAndGrantsToken(t *testing.T) {
	s := newTestServer()
	id := createConsult(t, s)
	post(t, s, "/consults/"+id+"/transition", `{"to":"triaged"}`)
	post(t, s, "/consults/"+id+"/transition", `{"to":"in_review"}`)
	// token before escalation is an error
	if rec, _ := post(t, s, "/consults/"+id+"/token", `{"identity":"dr-a"}`); rec.Code != 409 {
		t.Fatalf("no token before a room exists: %d", rec.Code)
	}
	// escalate to voice: room provisioned + state moved atomically
	_, out := post(t, s, "/consults/"+id+"/escalate", `{"tier":"voice","actor":"dr-a"}`)
	if out["state"] != StEscalated || out["room"] == "" || out["tier"] != "voice" {
		t.Fatalf("escalate: %v", out)
	}
	// both parties mint join tokens
	_, tok1 := post(t, s, "/consults/"+id+"/token", `{"identity":"dr-a"}`)
	_, tok2 := post(t, s, "/consults/"+id+"/token", `{"identity":"PAT-1"}`)
	for _, tok := range []string{tok1["token"].(string), tok2["token"].(string)} {
		claims, err := VerifyLiveKitToken("devsecret", tok)
		if err != nil {
			t.Fatalf("token verifies: %v", err)
		}
		vid := claims["video"].(map[string]any)
		if vid["room"] != out["room"] || vid["roomJoin"] != true {
			t.Fatalf("room grant: %v", vid)
		}
	}
	// bad secret rejected
	bad := strings.TrimSuffix(tok1["token"].(string), "a") + "a"
	if _, err := VerifyLiveKitToken("devsecret", bad[:len(bad)-3]+"xxx"); err == nil {
		t.Fatal("tampered token must fail verification")
	}
}

func TestVideoTierMarkedInTokenMetadata(t *testing.T) {
	tok, err := LiveKitToken("k", "s", "consult-x-1", "dr-a", true, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	claims, _ := VerifyLiveKitToken("s", tok)
	if !strings.Contains(claims["metadata"].(string), `"video":true`) {
		t.Fatalf("video flag in metadata: %v", claims["metadata"])
	}
}

func TestAdviceSLAOverdue(t *testing.T) {
	s := newTestServer()
	id := createConsult(t, s) // routine, 4h SLA, unanswered
	old, _ := s.store.Get(id)
	old.CreatedAt = time.Now().Add(-5 * time.Hour)
	s.store.Update(old)
	req := httptest.NewRequest("GET", "/overdue", nil)
	rec := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rec, req)
	var out map[string]any
	json.Unmarshal(rec.Body.Bytes(), &out)
	if len(out["overdue"].([]any)) != 1 {
		t.Fatalf("one overdue consult: %v", out)
	}
}

func TestMediaReferenceStoredNotBytes(t *testing.T) {
	s := newTestServer()
	id := createConsult(t, s)
	_, out := post(t, s, "/consults/"+id+"/messages",
		`{"author":"PAT-1","role":"patient","body":"photo of the rash","media_ref":"blob://media/abc123"}`)
	msgs := out["messages"].([]any)
	last := msgs[len(msgs)-1].(map[string]any)
	if last["media_ref"] != "blob://media/abc123" {
		t.Fatalf("media stored as reference: %v", last)
	}
}
