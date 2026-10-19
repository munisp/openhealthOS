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
	MediaURL    string // media-service base (blob bytes); empty = artifacts fail
	HTTP        *http.Client
}

func NewMatrixProvider(bindingsURL, homeserver, token string) *MatrixProvider {
	return &MatrixProvider{
		BindingsURL: strings.TrimRight(bindingsURL, "/"),
		Homeserver:  strings.TrimRight(homeserver, "/"),
		Token:       token,
		MediaURL:    strings.TrimRight(os.Getenv("MEDIA_URL"), "/"),
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

// SendArtifact delivers a media blob inline: blob://media/{id} bytes are
// fetched from media-service (SHA-256-verified store, ADR-0027), uploaded
// to the homeserver's media repo (mxc://), and sent as m.image — so an
// imaging result arrives as a viewable artifact in howUdeyChat, not a
// "come back tomorrow" slip. Any failure errors the leg: the chain then
// degrades to the text caption.
func (p *MatrixProvider) SendArtifact(phone, caption, blobRef, mime string) error {
	if p.MediaURL == "" {
		return fmt.Errorf("artifact delivery needs MEDIA_URL")
	}
	id := strings.TrimPrefix(blobRef, "blob://media/")
	if mime == "" {
		mime = "application/octet-stream"
	}
	// 1. fetch the blob
	resp, err := p.HTTP.Get(p.MediaURL + "/blobs/" + url.PathEscape(id))
	if err != nil {
		return err
	}
	blob, err := io.ReadAll(io.LimitReader(resp.Body, MaxBlobBytesForMatrix))
	resp.Body.Close()
	if err != nil {
		return err
	}
	if resp.StatusCode != 200 || len(blob) == 0 {
		return fmt.Errorf("blob %s: %s", id, resp.Status)
	}
	// 2. upload to the homeserver media repo -> mxc://
	up, err := http.NewRequest(http.MethodPost,
		p.Homeserver+"/_matrix/media/v3/upload?filename="+url.QueryEscape(id),
		bytes.NewReader(blob))
	if err != nil {
		return err
	}
	up.Header.Set("Content-Type", mime)
	up.Header.Set("Authorization", "Bearer "+p.Token)
	ures, err := p.HTTP.Do(up)
	if err != nil {
		return err
	}
	defer ures.Body.Close()
	if ures.StatusCode >= 300 {
		io.Copy(io.Discard, io.LimitReader(ures.Body, 1<<16))
		return fmt.Errorf("media upload: %s", ures.Status)
	}
	var uploaded struct {
		ContentURI string `json:"content_uri"`
	}
	if err := json.NewDecoder(io.LimitReader(ures.Body, 1<<16)).Decode(&uploaded); err != nil || uploaded.ContentURI == "" {
		return fmt.Errorf("media upload: bad response")
	}
	// 3. send the artifact into the patient's room
	room, err := p.roomFor(phone)
	if err != nil {
		return err
	}
	msgtype := "m.image"
	if strings.HasPrefix(mime, "video/") {
		msgtype = "m.video"
	} else if strings.HasPrefix(mime, "audio/") {
		msgtype = "m.audio"
	}
	body, _ := json.Marshal(map[string]string{
		"msgtype": msgtype, "body": caption, "url": uploaded.ContentURI,
	})
	txn := fmt.Sprintf("ntf-art-%x", fnv(blobRef))
	u := fmt.Sprintf("%s/_matrix/client/v3/rooms/%s/send/m.room.message/%s",
		p.Homeserver, url.PathEscape(room), txn)
	req, err := http.NewRequest(http.MethodPut, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.Token)
	sres, err := p.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer sres.Body.Close()
	io.Copy(io.Discard, io.LimitReader(sres.Body, 1<<16))
	if sres.StatusCode >= 300 {
		return fmt.Errorf("matrix send: %s", sres.Status)
	}
	return nil
}

// MaxBlobBytesForMatrix mirrors media-service's 25 MiB ceiling.
const MaxBlobBytesForMatrix = 25 << 20

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
