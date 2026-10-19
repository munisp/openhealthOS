package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// captureSender records outbound replies.
type captureSender struct {
	mu   sync.Mutex
	sent []string
}

func (c *captureSender) Send(to, text string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sent = append(c.sent, to+"|"+text)
	return nil
}

func (c *captureSender) last() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.sent) == 0 {
		return ""
	}
	return c.sent[len(c.sent)-1]
}

// stubPlatform fakes registry + portal + wellness in one server,
// routing by path prefix.
func stubPlatform(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("GET /patients/search", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("q")
		if normPhone(q) == "2348012345678" {
			json.NewEncoder(w).Encode(map[string]any{"results": []map[string]string{
				{"id": "OH-KD-0001", "name": "Amina Bello", "phone": "+2348012345678"},
			}})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"results": []map[string]string{}})
	})
	mux.HandleFunc("GET /portal/facilities", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"facilities": []map[string]string{
			{"id": "FAC-1", "name": "Kawo PHC"}, {"id": "FAC-2", "name": "Tudun Wada PHC"},
		}})
	})
	mux.HandleFunc("GET /portal/slots", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"slots": []map[string]any{
			{"id": "SCH-1:2026-10-20:480", "start": "2026-10-20T08:00:00Z", "capacity": 4, "booked": 1},
			{"id": "SCH-1:2026-10-20:510", "start": "2026-10-20T08:30:00Z", "capacity": 4, "booked": 4},
			{"id": "SCH-1:2026-10-20:540", "start": "2026-10-20T09:00:00Z", "capacity": 4, "booked": 0},
		}})
	})
	mux.HandleFunc("POST /portal/appointments", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"appointment": map[string]string{"id": "APT-9"}})
	})
	mux.HandleFunc("GET /portal/appointments", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"appointments": []map[string]string{
			{"id": "APT-1", "facility_id": "FAC-1", "service": "ANC", "start": "2026-10-21T09:00:00Z", "status": "booked"},
			{"id": "APT-0", "facility_id": "FAC-1", "service": "old", "start": "2026-09-01T09:00:00Z", "status": "completed"},
		}})
	})
	mux.HandleFunc("POST /portal/appointments/cancel", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"status": "cancelled"})
	})
	mux.HandleFunc("GET /portal/coverage", func(w http.ResponseWriter, r *http.Request) {
		// The portal gate, honestly: the pair must match (matrix binding
		// relies on this 404 revealing nothing).
		if normPhone(r.URL.Query().Get("phone")) != "2348012345678" ||
			r.URL.Query().Get("patient_id") != "OH-KD-0001" {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"status": "eligible", "scheme": "NHIA Basic"})
	})
	mux.HandleFunc("GET /portal/feed", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"notifications": []map[string]string{
			{"kind": "result", "body": "Your lab result is ready at Kawo PHC"},
		}})
	})
	mux.HandleFunc("POST /portal/messages", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"status": "queued"})
	})
	mux.HandleFunc("POST /wellness/ingest", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"accepted": 1})
	})
	return httptest.NewServer(mux)
}

func newTestService(t *testing.T) (*service, *captureSender) {
	t.Helper()
	stub := stubPlatform(t)
	t.Cleanup(stub.Close)
	cs := &captureSender{}
	return &service{
		sender: cs, verifyTok: "vtok",
		registry: stub.URL, portal: stub.URL, wellness: stub.URL,
		sessions: map[string]*Session{}, seen: map[string]time.Time{},
	}, cs
}

func waBody(t *testing.T, id, from, text string) string {
	t.Helper()
	msg := map[string]any{"id": id, "from": from, "type": "text", "text": map[string]string{"body": text}}
	value := map[string]any{"messages": []map[string]any{msg}}
	change := map[string]any{"value": value}
	entry := map[string]any{"changes": []map[string]any{change}}
	raw, _ := json.Marshal(map[string]any{"entry": []map[string]any{entry}})
	return string(raw)
}

func post(t *testing.T, srv *httptest.Server, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/webhooks/whatsapp", strings.NewReader(body))
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	srv.Config.Handler.ServeHTTP(rec, req)
	return rec
}

func newTestServer(s *service) *httptest.Server {
	return httptest.NewServer(s.routes())
}

func TestVerifyHandshake(t *testing.T) {
	s, _ := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=vtok&hub.challenge=CH42")
	if err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 16)
	n, _ := resp.Body.Read(buf)
	if string(buf[:n]) != "CH42" {
		t.Fatalf("want challenge echoed, got %q", buf[:n])
	}
	resp2, _ := http.Get(srv.URL + "/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=X")
	if resp2.StatusCode != http.StatusForbidden {
		t.Fatalf("bad token: want 403, got %d", resp2.StatusCode)
	}
}

func TestMenuAndGreeting(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	post(t, srv, waBody(t, "m1", "2348012345678", "hi"), nil)
	if !strings.Contains(cs.last(), "1. Book an appointment") {
		t.Fatalf("menu missing: %q", cs.last())
	}
}

func TestUnknownNumberGetsEnrolmentGuidance(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	post(t, srv, waBody(t, "m1", "2348099999999", "hi"), nil)
	post(t, srv, waBody(t, "m2", "2348099999999", "1"), nil)
	if !strings.Contains(cs.last(), "isn't linked to a health record") {
		t.Fatalf("want enrolment guidance, got %q", cs.last())
	}
	if strings.Contains(cs.last(), "OH-") {
		t.Fatal("patient id leaked to an unknown number")
	}
}

func TestBookingFlowEndToEnd(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "b1", from, "1"), nil)
	if !strings.Contains(cs.last(), "Kawo PHC") {
		t.Fatalf("facilities missing: %q", cs.last())
	}
	post(t, srv, waBody(t, "b2", from, "1"), nil) // facility
	if !strings.Contains(cs.last(), "Which day") {
		t.Fatalf("dates missing: %q", cs.last())
	}
	post(t, srv, waBody(t, "b3", from, "1"), nil) // day
	last := cs.last()
	if !strings.Contains(last, "Free times") || strings.Contains(last, "08:30") {
		t.Fatalf("slots wrong (full slot must be hidden): %q", last)
	}
	post(t, srv, waBody(t, "b4", from, "2"), nil) // second free slot (09:00)
	if !strings.Contains(cs.last(), "Book Kawo PHC") {
		t.Fatalf("confirm missing: %q", cs.last())
	}
	post(t, srv, waBody(t, "b5", from, "1"), nil) // yes
	if !strings.Contains(cs.last(), "Booked:") {
		t.Fatalf("booking failed: %q", cs.last())
	}
}

func TestRetryedWebhookDoesNotDoubleAct(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "r1", from, "1"), nil)
	n := len(cs.sent)
	post(t, srv, waBody(t, "r1", from, "1"), nil) // WhatsApp retry, same id
	if len(cs.sent) != n {
		t.Fatal("duplicate message id produced a second reply")
	}
}

func TestAppointmentsAndCancel(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "a1", from, "2"), nil)
	last := cs.last()
	if !strings.Contains(last, "ANC") || strings.Contains(last, "completed") {
		t.Fatalf("appointments list wrong: %q", last)
	}
	post(t, srv, waBody(t, "a2", from, "1"), nil) // cancel first
	if !strings.Contains(cs.last(), "Cancelled:") {
		t.Fatalf("cancel failed: %q", cs.last())
	}
}

func TestCoverageAndUpdatesAndAsk(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "c1", from, "3"), nil)
	if !strings.Contains(cs.last(), "covered") {
		t.Fatalf("coverage: %q", cs.last())
	}
	post(t, srv, waBody(t, "c2", from, "4"), nil)
	if !strings.Contains(cs.last(), "lab result is ready") {
		t.Fatalf("updates: %q", cs.last())
	}
	post(t, srv, waBody(t, "c3", from, "5"), nil)
	post(t, srv, waBody(t, "c4", from, "my child has a fever since yesterday"), nil)
	if !strings.Contains(cs.last(), "triage queue") {
		t.Fatalf("ask: %q", cs.last())
	}
}

func TestWellnessLogging(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "w1", from, "6"), nil)
	post(t, srv, waBody(t, "w2", from, "3"), nil)        // bp
	post(t, srv, waBody(t, "w3", from, "not-a-bp"), nil) // garbage
	if !strings.Contains(cs.last(), "120/80") {
		t.Fatalf("validation hint missing: %q", cs.last())
	}
	post(t, srv, waBody(t, "w4", from, "128/84"), nil)
	if !strings.Contains(cs.last(), "Logged to your record") {
		t.Fatalf("log failed: %q", cs.last())
	}
}

func TestSignatureEnforcedWhenSecretSet(t *testing.T) {
	s, _ := newTestService(t)
	s.appSecret = "sekrit"
	srv := newTestServer(s)
	defer srv.Close()
	body := waBody(t, "s1", "2348012345678", "hi")
	mac := hmac.New(sha256.New, []byte("sekrit"))
	mac.Write([]byte(body))
	good := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	if rec := post(t, srv, body, map[string]string{"X-Hub-Signature-256": good}); rec.Code != 200 {
		t.Fatalf("good signature rejected: %d", rec.Code)
	}
	if rec := post(t, srv, body, map[string]string{"X-Hub-Signature-256": "sha256=bad"}); rec.Code != 403 {
		t.Fatalf("bad signature accepted: %d", rec.Code)
	}
}

func TestMalformedPayloadStill200(t *testing.T) {
	s, _ := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	if rec := post(t, srv, "{not json", nil); rec.Code != 200 {
		t.Fatalf("malformed payload must ack 200 (no retry loop), got %d", rec.Code)
	}
}

func TestEscapeHatchFromDeepState(t *testing.T) {
	s, cs := newTestService(t)
	srv := newTestServer(s)
	defer srv.Close()
	from := "2348012345678"
	post(t, srv, waBody(t, "e1", from, "1"), nil)
	post(t, srv, waBody(t, "e2", from, "1"), nil)
	post(t, srv, waBody(t, "e3", from, "0"), nil) // back to menu from date pick
	if !strings.Contains(cs.last(), "Reply with a number") {
		t.Fatalf("escape hatch failed: %q", cs.last())
	}
}
