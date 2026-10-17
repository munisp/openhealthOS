// Package main — signed, staged OTA image updates for edge fleets.
//
// Edge nodes must never run unsigned software (technical spec §security).
// An update is a manifest: image digest, version, stage, rollout cohort —
// ed25519-signed by the release key. Nodes verify before applying; rollout
// is deterministic by device id so a bad stage hits a bounded cohort and
// rollback is publishing the previous manifest.
package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"
)

// Manifest describes one releasable image set.
type Manifest struct {
	Version    string   `json:"version"`     // semver-ish, e.g. "1.4.2"
	Images     []Image  `json:"images"`      // every image in the release
	Stage      string   `json:"stage"`       // pilot | lga | state
	CohortPct  int      `json:"cohort_pct"`  // 1..100 — deterministic rollout slice
	MinVersion string   `json:"min_version"` // refuse to apply below this
	IssuedAt   string   `json:"issued_at"`
	Signature  string   `json:"signature"` // base64 ed25519 over canonical(manifest sans signature)
}

// Image pins one container image by digest.
type Image struct {
	Name   string `json:"name"`   // e.g. ghcr.io/munisp/openhealthos/sync-agent
	Digest string `json:"digest"` // sha256:...
}

var (
	ErrBadSignature = errors.New("ota: signature invalid")
	ErrStaleVersion = errors.New("ota: manifest older than minimum version")
	ErrWrongCohort  = errors.New("ota: device not in rollout cohort")
)

// canonical is the signed byte form: JSON with the signature field zeroed,
// keys sorted (encoding/json sorts map keys; struct order is fixed).
func (m *Manifest) canonical() []byte {
	cp := *m
	cp.Signature = ""
	cp.Images = append([]Image(nil), m.Images...)
	sort.Slice(cp.Images, func(i, j int) bool { return cp.Images[i].Name < cp.Images[j].Name })
	raw, _ := json.Marshal(cp)
	return raw
}

// Sign stamps the manifest with priv.
func (m *Manifest) Sign(priv ed25519.PrivateKey) {
	m.Images = append([]Image(nil), m.Images...)
	sort.Slice(m.Images, func(i, j int) bool { return m.Images[i].Name < m.Images[j].Name })
	m.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, m.canonical()))
}

// Verify checks the signature against pub.
func (m *Manifest) Verify(pub ed25519.PublicKey) error {
	sig, err := base64.StdEncoding.DecodeString(m.Signature)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return ErrBadSignature
	}
	if !ed25519.Verify(pub, m.canonical(), sig) {
		return ErrBadSignature
	}
	return nil
}

// versionLess compares dotted numeric versions ("1.4.2" < "1.10.0").
func versionLess(a, b string) bool {
	pa, pb := splitVer(a), splitVer(b)
	for i := 0; i < 3; i++ {
		if pa[i] != pb[i] {
			return pa[i] < pb[i]
		}
	}
	return false
}

func splitVer(v string) [3]int {
	var out [3]int
	fmt.Sscanf(v, "%d.%d.%d", &out[0], &out[1], &out[2])
	return out
}

// InCohort reports whether deviceID falls inside the manifest's rollout
// slice — deterministic: sha256(stage|version|device) mod 100 < cohort.
// Same manifest + same device = same decision on every node, no central
// coordinator needed.
func (m *Manifest) InCohort(deviceID string) bool {
	if m.CohortPct >= 100 {
		return true
	}
	h := sha256.Sum256([]byte(m.Stage + "|" + m.Version + "|" + deviceID))
	return int(binary.BigEndian.Uint32(h[:4])%100) < m.CohortPct
}

// ShouldApply is the updater's full decision: signature, freshness, cohort.
func ShouldApply(m *Manifest, pub ed25519.PublicKey, deviceID, currentVersion string) error {
	if err := m.Verify(pub); err != nil {
		return err
	}
	if versionLess(m.Version, m.MinVersion) || versionLess(m.Version, currentVersion) && m.Version != currentVersion {
		return ErrStaleVersion
	}
	if !m.InCohort(deviceID) {
		return ErrWrongCohort
	}
	return nil
}

// NewManifest builds an unsigned manifest with sane defaults.
func NewManifest(version, stage string, images []Image) *Manifest {
	return &Manifest{
		Version: version, Stage: stage, Images: images,
		CohortPct: 100, MinVersion: "0.0.0",
		IssuedAt: time.Now().UTC().Format(time.RFC3339),
	}
}
