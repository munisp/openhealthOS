// matrix.go — the sovereign channel (ADR-0038): a Matrix transport for
// the same dialogue engine, for deployments that want WhatsApp-class
// interaction without Meta in the middle. The homeserver is ours
// (Tuwunel/Synapse/any compliant server), retention and federation are
// policy, and NDPA data residency is a config value, not a ToS.
//
// Transport doctrine follows the zooid pull pattern: the bot is a
// Matrix *user* that long-polls /sync — nothing to expose, runs behind
// any firewall, catches up after downtime. No SDK, no embedded server:
// the whole binding is two HTTPS calls (sync, send).
//
// Identity differs from the WhatsApp channel by necessity: a Matrix
// user id carries no phone number, so the channel binds the way the
// PWA does — patient ID (on the card) + registered phone, validated
// against the portal gate, which 404s any mismatch indistinguishably.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// matrixSender delivers replies through the Client-Server API. The
// "to" of a Matrix reply is a room id, captured when the message
// arrived — the service maps user → current room.
type matrixSender struct {
	hs    string // homeserver base URL
	token string
	http  *http.Client
}

func (m matrixSender) Send(roomID, text string) error {
	body, _ := json.Marshal(map[string]string{"msgtype": "m.text", "body": text})
	txn := fmt.Sprintf("oh-%d", time.Now().UnixNano())
	u := fmt.Sprintf("%s/_matrix/client/v3/rooms/%s/send/m.room.message/%s",
		m.hs, url.PathEscape(roomID), txn)
	req, err := http.NewRequest(http.MethodPut, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.token)
	c := m.http
	if c == nil {
		c = &http.Client{Timeout: 10 * time.Second}
	}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("matrix send: %s", resp.Status)
	}
	return nil
}

// matrixEvent is the slice of a /sync timeline this channel reads.
type matrixEvent struct {
	EventID string `json:"event_id"`
	Sender  string `json:"sender"`
	Type    string `json:"type"`
	Content struct {
		MsgType string `json:"msgtype"`
		Body    string `json:"body"`
	} `json:"content"`
}

// matrixListener is the pull loop: long-poll /sync, hand each inbound
// text event to the dialogue engine, reply into the room it came from.
type matrixListener struct {
	svc     *service
	sender  matrixSender
	botUser string
	since   string
	rooms   map[string]string // user id → last room they spoke in
	seen    map[string]bool   // event id dedupe
}

func newMatrixListener(svc *service, hs, token string) *matrixListener {
	return &matrixListener{
		svc:    svc,
		sender: matrixSender{hs: strings.TrimRight(hs, "/"), token: token},
		rooms:  map[string]string{},
		seen:   map[string]bool{},
	}
}

// whoami resolves the bot's own user id so it never answers itself.
func (l *matrixListener) whoami() string {
	if l.botUser != "" {
		return l.botUser
	}
	req, err := http.NewRequest(http.MethodGet,
		l.sender.hs+"/_matrix/client/v3/account/whoami", nil)
	if err != nil {
		return ""
	}
	req.Header.Set("Authorization", "Bearer "+l.sender.token)
	resp, err := l.client().Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	var out struct {
		UserID string `json:"user_id"`
	}
	json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&out)
	l.botUser = out.UserID
	return l.botUser
}

func (l *matrixListener) client() *http.Client {
	if l.sender.http != nil {
		return l.sender.http
	}
	return &http.Client{Timeout: 40 * time.Second}
}

// syncOnce performs one /sync round and processes new messages.
// Exposed for tests; the production loop is run().
func (l *matrixListener) syncOnce() error {
	u := l.sender.hs + "/_matrix/client/v3/sync?timeout=5000"
	if l.since != "" {
		u += "&since=" + url.QueryEscape(l.since)
	}
	req, err := http.NewRequest(http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+l.sender.token)
	resp, err := l.client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		return fmt.Errorf("sync: %s", resp.Status)
	}
	var batch struct {
		NextBatch string `json:"next_batch"`
		Rooms     struct {
			Join map[string]struct {
				Timeline struct {
					Events []matrixEvent `json:"events"`
				} `json:"timeline"`
			} `json:"join"`
		} `json:"rooms"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(&batch); err != nil {
		return err
	}
	bot := l.whoami()
	for roomID, room := range batch.Rooms.Join {
		for _, ev := range room.Timeline.Events {
			if ev.Type != "m.room.message" || ev.Content.MsgType != "m.text" {
				continue
			}
			if ev.Sender == bot || l.seen[ev.EventID] {
				continue
			}
			l.seen[ev.EventID] = true
			l.rooms[ev.Sender] = roomID
			ss := l.svc.matrixSession(ev.Sender)
			reply := l.svc.Handle(ss, strings.TrimSpace(ev.Content.Body))
			if reply == "" {
				continue
			}
			if err := l.sender.Send(roomID, reply); err != nil {
				log.Printf("matrix reply to %s: %v", roomID, err)
			}
		}
	}
	l.since = batch.NextBatch
	return nil
}

// run is the production loop. Sync errors back off and retry — a
// homeserver restart must never kill the channel.
func (l *matrixListener) run() {
	log.Printf("matrix channel: syncing against %s", l.sender.hs)
	for {
		if err := l.syncOnce(); err != nil {
			log.Printf("matrix sync: %v (retrying in 5s)", err)
			time.Sleep(5 * time.Second)
		}
	}
}
