// Package main — OpenHealth OS patient WhatsApp channel (ADR-0037).
//
// WhatsApp is the app Nigerians already have; the patient PWA and USSD
// cover smartphones and feature phones, but the largest reachable
// channel for patient↔provider interaction is the chat thread they
// open every day. This service is that channel: one verified number
// where a patient books appointments, checks coverage, reads clinic
// updates, asks the clinic a question, and logs a health reading —
// without installing anything.
//
// Doctrine (same as every rail in this platform):
//   - identity is WhatsApp-authenticated MSISDN → registry phone match
//     → the portal's patient_id+phone gate. A number that matches no
//     patient gets enrolment guidance, never a hint about who exists.
//   - the bot is a thin client of the portal rail (ADR-0017) and the
//     wellness rail (ADR-0035): every action it can take is an action
//     those services already expose, with their own gates. No domain
//     logic lives here.
//   - malformed webhook events still answer 200 — a webhook that
//     retries poison payloads forever is a self-inflicted outage
//     (the dispatch voice/webhook doctrine, ADR-0022).
//   - nothing proprietary is embedded: the Cloud API is one HTTPS
//     POST behind a Sender seam; the default sender is a local log
//     or JSONL outbox so the whole channel runs offline in dev.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// ---------- WhatsApp Cloud API payload shapes ----------

type waPayload struct {
	Entry []struct {
		Changes []struct {
			Value struct {
				Messages []struct {
					ID   string `json:"id"`
					From string `json:"from"`
					Type string `json:"type"`
					Text struct {
						Body string `json:"body"`
					} `json:"text"`
				} `json:"messages"`
			} `json:"value"`
		} `json:"changes"`
	} `json:"entry"`
}

// ---------- Sender seam ----------

// Sender delivers one outbound text message. The WhatsApp Cloud API is
// the production binding; the file and log senders keep development and
// demos fully offline and inspectable.
type Sender interface {
	Send(to, text string) error
}

type logSender struct{}

func (logSender) Send(to, text string) error {
	log.Printf("wa→%s: %s", to, strings.ReplaceAll(text, "\n", " | "))
	return nil
}

// fileSender appends one JSON line per message — the demo/e2e seam.
type fileSender struct{ path string }

func (f fileSender) Send(to, text string) error {
	raw, _ := json.Marshal(map[string]string{"to": to, "text": text,
		"ts": time.Now().UTC().Format(time.RFC3339)})
	fh, err := os.OpenFile(f.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer fh.Close()
	_, err = fh.Write(append(raw, '\n'))
	return err
}

// graphSender is the WhatsApp Cloud API binding: one HTTPS POST,
// bearer token, no SDK.
type graphSender struct {
	phoneID string
	token   string
	base    string // overridable in tests
	client  *http.Client
}

func (g graphSender) Send(to, text string) error {
	body := map[string]any{
		"messaging_product": "whatsapp",
		"to":                to,
		"type":              "text",
		"text":              map[string]string{"body": text},
	}
	raw, _ := json.Marshal(body)
	base := g.base
	if base == "" {
		base = "https://graph.facebook.com/v21.0"
	}
	req, err := http.NewRequest(http.MethodPost, base+"/"+g.phoneID+"/messages",
		strings.NewReader(string(raw)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+g.token)
	c := g.client
	if c == nil {
		c = &http.Client{Timeout: 8 * time.Second}
	}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("graph api: %s", resp.Status)
	}
	return nil
}

// ---------- service ----------

type service struct {
	sender    Sender
	verifyTok string
	appSecret string // empty → signature check off (dev only)
	registry  string // registry-service base
	portal    string // notification-service base (portal rail)
	wellness  string // wellness-service base

	mu       sync.Mutex
	sessions map[string]*Session  // keyed by sender MSISDN
	seen     map[string]time.Time // inbound message-id dedupe
}

func (s *service) session(phone string) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	ss, ok := s.sessions[phone]
	if !ok || time.Since(ss.UpdatedAt) > 30*time.Minute {
		ss = &Session{Phone: phone, State: StateMenu, Data: map[string]string{}}
		s.sessions[phone] = ss
	}
	ss.UpdatedAt = time.Now()
	return ss
}

// duplicate reports whether this message id was already handled —
// WhatsApp retries webhook delivery, and a retried "1" must not book
// twice.
func (s *service) duplicate(id string) bool {
	if id == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.seen[id]; ok {
		return true
	}
	s.seen[id] = time.Now()
	for k, t := range s.seen { // prune older than a day
		if time.Since(t) > 24*time.Hour {
			delete(s.seen, k)
		}
	}
	return false
}

// verifySignature enforces X-Hub-Signature-256 when an app secret is
// configured. Without a secret the check is off (dev default), which
// the README states plainly.
func (s *service) verifySignature(body []byte, sigHeader string) bool {
	if s.appSecret == "" {
		return true
	}
	mac := hmac.New(sha256.New, []byte(s.appSecret))
	mac.Write(body)
	want := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	return hmac.Equal([]byte(want), []byte(sigHeader))
}

func (s *service) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /webhooks/whatsapp", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if q.Get("hub.mode") == "subscribe" && q.Get("hub.verify_token") == s.verifyTok {
			w.Write([]byte(q.Get("hub.challenge")))
			return
		}
		http.Error(w, "forbidden", http.StatusForbidden)
	})
	mux.HandleFunc("POST /webhooks/whatsapp", func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		if err != nil {
			w.WriteHeader(http.StatusOK) // never start a retry loop
			return
		}
		if !s.verifySignature(body, r.Header.Get("X-Hub-Signature-256")) {
			http.Error(w, "bad signature", http.StatusForbidden)
			return
		}
		w.WriteHeader(http.StatusOK) // ack first; processing is ours
		var p waPayload
		if json.Unmarshal(body, &p) != nil {
			return // malformed: logged nowhere, retried never
		}
		for _, e := range p.Entry {
			for _, ch := range e.Changes {
				for _, m := range ch.Value.Messages {
					if m.Type != "text" || strings.TrimSpace(m.Text.Body) == "" {
						continue
					}
					if s.duplicate(m.ID) {
						continue
					}
					reply := s.Handle(s.session(m.From), strings.TrimSpace(m.Text.Body))
					if reply == "" {
						continue
					}
					if err := s.sender.Send(m.From, reply); err != nil {
						log.Printf("send to %s: %v", m.From, err)
					}
				}
			}
		}
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func main() {
	sender := Sender(logSender{})
	if out := os.Getenv("WA_OUTBOX"); out != "" {
		sender = fileSender{path: out}
	}
	if tok := os.Getenv("WHATSAPP_TOKEN"); tok != "" {
		sender = graphSender{phoneID: os.Getenv("WHATSAPP_PHONE_ID"), token: tok}
	}
	s := &service{
		sender:    sender,
		verifyTok: env("WHATSAPP_VERIFY_TOKEN", "dev-verify-token"),
		appSecret: os.Getenv("WHATSAPP_APP_SECRET"),
		registry:  env("REGISTRY_URL", "http://127.0.0.1:8081"),
		portal:    env("PORTAL_URL", "http://127.0.0.1:8089"),
		wellness:  env("WELLNESS_URL", "http://127.0.0.1:8090"),
		sessions:  map[string]*Session{},
		seen:      map[string]time.Time{},
	}
	addr := env("LISTEN", ":8099")
	log.Printf("whatsapp-gateway listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
