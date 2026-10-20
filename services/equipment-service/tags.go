package main

// tags.go — signed QR/barcode identity + scan actions (ADR-0052).
//
// Go mirror of packages/assettag (same payload, same HMAC): the tag is
// verified HERE, offline-capable, before any custody/location/maintenance
// action commits. Every scan appends to an in-memory custody log — who
// moved what, where, when — which is the audit spine for assets (the
// MRI that walks between wards leaves a trail).
//
// Payload: OHAST1.<facility>.<asset>.<issued_unix>.<hmac8-hex>
// Secret:  OH_ASSET_SECRET env (>=16 chars). Unset → tagging disabled,
// scans rejected 503 — fail-closed, never guess.

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

const tagPrefix = "OHAST1"
const tagMaxAgeSec = 5 * 365 * 86400

var ErrTagDisabled = errors.New("asset tagging disabled: OH_ASSET_SECRET unset")
var ErrTagBadSig = errors.New("bad tag signature — rejected")

type AssetTag struct {
	Facility string `json:"facility"`
	Asset    string `json:"asset"`
	Issued   int64  `json:"issued"`
	Stale    bool   `json:"stale"`
	Tag      string `json:"tag"`
}

func tagSecret() ([]byte, error) {
	s := os.Getenv("OH_ASSET_SECRET")
	if len(s) < 16 {
		return nil, ErrTagDisabled
	}
	return []byte(s), nil
}

func tagSig(secret []byte, body string) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(body))
	return hex.EncodeToString(mac.Sum(nil))[:16]
}

// MintTag signs a tag for a registered asset.
func MintTag(facility, asset string, issued time.Time) (string, error) {
	secret, err := tagSecret()
	if err != nil {
		return "", err
	}
	if facility == "" || asset == "" || strings.ContainsAny(facility+asset, ".") {
		return "", errors.New("facility/asset must be non-empty and dot-free")
	}
	body := fmt.Sprintf("%s.%s.%s.%d", tagPrefix, facility, asset, issued.Unix())
	return body + "." + tagSig(secret, body), nil
}

// VerifyTag parses and authenticates a scanned tag.
func VerifyTag(tag string, now time.Time) (AssetTag, error) {
	secret, err := tagSecret()
	if err != nil {
		return AssetTag{}, err
	}
	parts := strings.Split(strings.TrimSpace(tag), ".")
	if len(parts) != 5 || parts[0] != tagPrefix {
		return AssetTag{}, errors.New("malformed tag")
	}
	body := strings.Join(parts[:4], ".")
	if !hmac.Equal([]byte(tagSig(secret, body)), []byte(parts[4])) {
		return AssetTag{}, ErrTagBadSig
	}
	issued, err := strconv.ParseInt(parts[3], 10, 64)
	if err != nil {
		return AssetTag{}, errors.New("bad issue timestamp")
	}
	if issued > now.Unix()+300 {
		return AssetTag{}, errors.New("issue timestamp in the future")
	}
	return AssetTag{
		Facility: parts[1], Asset: parts[2], Issued: issued,
		Stale: now.Unix()-issued > tagMaxAgeSec, Tag: tag,
	}, nil
}

// CustodyEvent is one scan action on the audit spine.
type CustodyEvent struct {
	At       string `json:"at"`
	Asset    string `json:"asset"`
	Facility string `json:"facility"`
	Action   string `json:"action"` // locate | transfer | maintenance_flag | check_in
	Actor    string `json:"actor"`
	Location string `json:"location,omitempty"`
	Notes    string `json:"notes,omitempty"`
	StaleTag bool   `json:"stale_tag,omitempty"`
}

// Scan applies a verified tag action: updates the asset's location
// (facility/ward string) and appends custody evidence. Actions:
//
//	locate            — asset seen at location (stocktake sweep)
//	transfer          — custody moves to location, named actor
//	maintenance_flag  — scan-to-report: opens a P3 ticket
var scanActions = map[string]bool{"locate": true, "transfer": true, "maintenance_flag": true, "check_in": true}

func (s *Service) Scan(tag, action, actor, location, notes string) (CustodyEvent, error) {
	t, err := VerifyTagAny(tag, s.now())
	if err != nil {
		return CustodyEvent{}, err
	}
	if !scanActions[action] {
		return CustodyEvent{}, fmt.Errorf("unknown action %q", action)
	}
	if actor == "" {
		return CustodyEvent{}, errors.New("actor required — the audit names who")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	eq, ok := s.equip[t.Asset]
	if !ok {
		return CustodyEvent{}, ErrNotFound
	}
	if location != "" {
		eq.Location = location
	}
	if action == "maintenance_flag" && eq.Status == "working" {
		eq.Status = "in-maintenance"
	}
	ev := CustodyEvent{
		At: s.now().UTC().Format(time.RFC3339), Asset: t.Asset,
		Facility: t.Facility, Action: action, Actor: actor,
		Location: location, Notes: notes, StaleTag: t.Stale,
	}
	s.custody = append(s.custody, ev)
	return ev, nil
}

// CustodyLog returns the audit trail, optionally for one asset.
func (s *Service) CustodyLog(assetID string) []CustodyEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []CustodyEvent{}
	for _, e := range s.custody {
		if assetID == "" || e.Asset == assetID {
			out = append(out, e)
		}
	}
	return out
}

// --------------------------------------------------------- v2 tags (ADR-0055)
//
// v1 (OHAST1) had one global secret — leak it and every tag in the field
// is silently trustworthy. v2 carries a key id and verifies against a
// KEYRING (OH_QR_KEYS='{"k2025":"…","k2026":"…"}', active via
// OH_QR_ACTIVE): old keys keep verifying after rotation, only the active
// key mints. Revocation list: OH_QR_REVOKED points at a JSONL file of
// revoked payload bodies — a leaked print sheet becomes a hard reject.

const tagPrefixV2 = "OHAST2"

type keyring struct {
	ring   map[string]string
	active string
}

func loadKeyring() (keyring, error) {
	raw := os.Getenv("OH_QR_KEYS")
	ring := map[string]string{}
	if strings.HasPrefix(strings.TrimSpace(raw), "{") {
		var m map[string]string
		if err := json.Unmarshal([]byte(raw), &m); err != nil {
			return keyring{}, fmt.Errorf("OH_QR_KEYS invalid JSON: %w", err)
		}
		for kid, secret := range m {
			if len(secret) >= 16 && kid != "" && !strings.Contains(kid, ".") {
				ring[kid] = secret
			}
		}
	} else if len(raw) >= 16 {
		ring["k0"] = raw
	}
	// legacy single-secret env degrades into the ring as k0
	if legacy := os.Getenv("OH_ASSET_SECRET"); len(ring) == 0 && len(legacy) >= 16 {
		ring["k0"] = legacy
	}
	if len(ring) == 0 {
		return keyring{}, ErrTagDisabled
	}
	active := os.Getenv("OH_QR_ACTIVE")
	if active == "" {
		keys := make([]string, 0, len(ring))
		for k := range ring {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		active = keys[len(keys)-1]
	}
	if _, ok := ring[active]; !ok {
		return keyring{}, fmt.Errorf("active key %q not in keyring", active)
	}
	return keyring{ring: ring, active: active}, nil
}

// revokedBodies loads the revocation JSONL, cached by path+mtime.
var revokedCache struct {
	path  string
	mtime time.Time
	set   map[string]bool
}

func revokedBodies() map[string]bool {
	path := os.Getenv("OH_QR_REVOKED")
	if path == "" {
		return nil
	}
	fi, err := os.Stat(path)
	if err != nil {
		return nil
	}
	if revokedCache.path == path && revokedCache.mtime.Equal(fi.ModTime()) {
		return revokedCache.set
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	set := map[string]bool{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var row struct {
			Body string `json:"body"`
		}
		if json.Unmarshal([]byte(line), &row) == nil && row.Body != "" {
			set[row.Body] = true
		}
	}
	revokedCache.path, revokedCache.mtime, revokedCache.set = path, fi.ModTime(), set
	return set
}

// MintTagV2 signs a keyring tag for a registered asset.
func MintTagV2(facility, asset string, issued time.Time) (string, error) {
	kr, err := loadKeyring()
	if err != nil {
		return "", err
	}
	if facility == "" || asset == "" || strings.ContainsAny(facility+asset, ".") {
		return "", errors.New("facility/asset must be non-empty and dot-free")
	}
	body := fmt.Sprintf("%s.%s.%s.%s.%d", tagPrefixV2, kr.active, facility, asset, issued.Unix())
	return body + "." + tagSig([]byte(kr.ring[kr.active]), body), nil
}

// VerifyTagAny accepts v1 (legacy single-secret) and v2 (keyring) tags,
// applying the revocation list to both. v1 keeps working so deployed
// stickers don't all need reprinting on rotation day.
func VerifyTagAny(tag string, now time.Time) (AssetTag, error) {
	parts := strings.Split(strings.TrimSpace(tag), ".")
	if len(parts) == 5 && parts[0] == tagPrefix {
		return VerifyTag(tag, now) // v1 path unchanged
	}
	if len(parts) != 6 || parts[0] != tagPrefixV2 {
		return AssetTag{}, errors.New("malformed tag")
	}
	kr, err := loadKeyring()
	if err != nil {
		return AssetTag{}, err
	}
	secret, ok := kr.ring[parts[1]]
	if !ok {
		return AssetTag{}, fmt.Errorf("unknown key id %q — retired or forged", parts[1])
	}
	body := strings.Join(parts[:5], ".")
	if !hmac.Equal([]byte(tagSig([]byte(secret), body)), []byte(parts[5])) {
		return AssetTag{}, ErrTagBadSig
	}
	if revokedBodies()[body] {
		return AssetTag{}, errors.New("tag revoked — leaked sheet or decommissioned asset")
	}
	issued, err := strconv.ParseInt(parts[4], 10, 64)
	if err != nil {
		return AssetTag{}, errors.New("bad issue timestamp")
	}
	if issued > now.Unix()+300 {
		return AssetTag{}, errors.New("issue timestamp in the future")
	}
	return AssetTag{
		Facility: parts[2], Asset: parts[3], Issued: issued,
		Stale: now.Unix()-issued > tagMaxAgeSec, Tag: tag,
	}, nil
}
