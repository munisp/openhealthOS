// matrix_provider.go — sovereign-first delivery leg (ADR-0038).
//
// A notification whose patient has a bound howUdeyChat/Matrix session is
// delivered straight into their Matrix room on our own homeserver — no SMS
// aggregator, no Meta, no per-message cost. Routing is dynamic: the leg
// resolves phone → room through the whatsapp-gateway's binding table
// (/internal/matrix-room) at send time, so a patient who bound yesterday
// starts receiving Matrix delivery today without any config change.
//
// Failure doctrine matches the chain: an unbound patient (404) or a dead
// homeserver both return an error, the circuit breaker opens, and the next
// leg (SMS webhook/log) takes over — sovereignty preferred, never required.
//
// Message doctrine is unchanged: renderMessage keeps texts clinical-detail-
// free ("your result is ready"), so this leg carries no new privacy risk.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// MatrixProvider delivers notifications into the patient's Matrix room.
type MatrixProvider struct {
	BindingsURL string // whatsapp-gateway internal base, e.g. http://gateway:8099
	Homeserver  string // homeserver base URL
	Token       string // bot access token on that homeserver
	HTTP        *http.Client
}

func NewMatrixProvider(bindingsURL, homeserver, token string) *MatrixProvider {
	return &MatrixProvider{
		BindingsURL: strings.TrimRight(bindingsURL, "/"),
		Homeserver:  strings.TrimRight(homeserver, "/"),
		Token:       token,
		HTTP:        &http.Client{Timeout: 8 * time.Second},
	}
}

func (p *MatrixProvider) Name() string { return "matrix" }

// roomFor resolves the patient's phone to a bound Matrix room.
func (p *MatrixProvider) roomFor(phone string) (string, error) {
	u := p.BindingsURL + "/internal/matrix-room?phone=" + url.QueryEscape(phone)
	req, err := http.NewRequest(http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	resp, err := p.HTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		return "", fmt.Errorf("no matrix binding for phone (%s)", resp.Status)
	}
	var out struct {
		RoomID string `json:"room_id"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&out); err != nil {
		return "", err
	}
	if out.RoomID == "" {
		return "", fmt.Errorf("no matrix binding for phone")
	}
	return out.RoomID, nil
}

// Send resolves the binding and posts the message into the room. The txn id
// is content-derived, so a chain retry after an ambiguous failure does not
// double-deliver (homeservers dedupe PUT txn ids).
func (p *MatrixProvider) Send(phone, text string) error {
	room, err := p.roomFor(phone)
	if err != nil {
		return err
	}
	body, _ := json.Marshal(map[string]string{"msgtype": "m.text", "body": text})
	txn := fmt.Sprintf("ntf-%x", fnv(text))
	u := fmt.Sprintf("%s/_matrix/client/v3/rooms/%s/send/m.room.message/%s",
		p.Homeserver, url.PathEscape(room), txn)
	req, err := http.NewRequest(http.MethodPut, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.Token)
	resp, err := p.HTTP.Do(req)
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

func fnv(s string) uint64 {
	var h uint64 = 1469598103934665603
	for i := 0; i < len(s); i++ {
		h ^= uint64(s[i])
		h *= 1099511628211
	}
	return h
}

// matrixProviderFromEnv builds the leg when its envs are set; nil otherwise.
//   matrix:https://gateway:8099   in SMS_PROVIDERS
//   MATRIX_HOMESERVER, MATRIX_ACCESS_TOKEN
func matrixProviderFromEnv(bindingsURL string) SMSProvider {
	hs := os.Getenv("MATRIX_HOMESERVER")
	tok := os.Getenv("MATRIX_ACCESS_TOKEN")
	if hs == "" || tok == "" {
		return &brokenProvider{name: "matrix(missing MATRIX_HOMESERVER/MATRIX_ACCESS_TOKEN)"}
	}
	return NewMatrixProvider(bindingsURL, hs, tok)
}
