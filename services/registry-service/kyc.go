package main

// KYC — progressive identity assurance for a federation where most patients
// are enrolled offline at a PHC and national-id verification is a privilege
// of connectivity, not a precondition of care (ADR-0012).
//
// The ladder (never blocks care, always bounds what a record can do):
//
//	self         — registered at the edge, demographics as stated
//	phone        — phone binding proven by OTP (the patient really holds
//	               that handset — anchors the patient portal + SMS rail)
//	nin-format   — NIN present and syntactically valid (offline check)
//	nin-verified — NIN confirmed against NIMC with demographic match
//
// Fraud gates are deterministic and auditable: registration refuses exact
// duplicates (same NIN, same phone+DOB, same normalised name+DOB), the
// database enforces NIN uniqueness as the final backstop, and /kyc/signals
// surfaces the residual risk (shared phones, level distribution) for ops.

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// KYC levels, in ascending assurance order.
const (
	KYCSelf        = "self"
	KYCPhone       = "phone"
	KYCNINFormat   = "nin-format"
	KYCNINVerified = "nin-verified"
)

// kycRank orders levels so an upgrade never silently downgrades.
func kycRank(level string) int {
	switch level {
	case KYCPhone:
		return 1
	case KYCNINFormat:
		return 2
	case KYCNINVerified:
		return 3
	case KYCBiometricVerified:
		return 4
	default:
		return 0
	}
}

var (
	ErrDuplicate    = errors.New("duplicate patient")
	ErrOTPExpired   = errors.New("otp expired")
	ErrOTPLocked    = errors.New("otp locked after too many attempts")
	ErrOTPMismatch  = errors.New("otp mismatch")
	ErrNINNoMatch   = errors.New("nin verification: no match")
	ErrNINDeferred  = errors.New("nin verification deferred (authority unreachable)")
	ErrNINBadFormat = errors.New("nin must be 11 digits")
)

// ---------- NIN verification seam ----------

// Verification is the authority's answer to a NIN check.
type Verification struct {
	Status string `json:"status"` // verified | no_match | deferred | invalid_format
	Name   string `json:"name,omitempty"`
	Detail string `json:"detail,omitempty"`
}

// Verifier checks a NIN against an authority. The seam keeps NIMC (or a
// state HIE proxy) behind one method, so the edge runs offline by default
// and verification simply defers until connectivity exists.
type Verifier interface {
	Verify(nin, dob string) Verification
}

// ValidNINFormat is the offline check: 11 digits, nothing more.
func ValidNINFormat(nin string) bool {
	if len(nin) != 11 {
		return false
	}
	for _, r := range nin {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// OfflineVerifier is the default: it never claims a match it cannot prove.
// Every verification defers — the record keeps working at a lower level.
type OfflineVerifier struct{}

func (OfflineVerifier) Verify(nin, _ string) Verification {
	if !ValidNINFormat(nin) {
		return Verification{Status: "invalid_format", Detail: "NIN is 11 digits"}
	}
	return Verification{Status: "deferred", Detail: "no NIMC channel configured — verify when online"}
}

// HTTPVerifier calls a NIMC-compatible verification endpoint
// (NIN_VERIFY_URL): POST {"nin","dob"} → {"match":bool,"name":string}.
// Any aggregator or state proxy that speaks this shape plugs in — no
// vendor SDK, per the licence policy (ADR-0005).
type HTTPVerifier struct {
	URL   string
	Token string
}

func (h HTTPVerifier) Verify(nin, dob string) Verification {
	if !ValidNINFormat(nin) {
		return Verification{Status: "invalid_format", Detail: "NIN is 11 digits"}
	}
	body := fmt.Sprintf(`{"nin":%q,"dob":%q}`, nin, dob)
	req, err := http.NewRequest(http.MethodPost, h.URL, strings.NewReader(body))
	if err != nil {
		return Verification{Status: "deferred", Detail: err.Error()}
	}
	req.Header.Set("Content-Type", "application/json")
	if h.Token != "" {
		req.Header.Set("Authorization", "Bearer "+h.Token)
	}
	client := &http.Client{Timeout: 8 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return Verification{Status: "deferred", Detail: "authority unreachable: " + err.Error()}
	}
	defer resp.Body.Close()
	// A non-200 is an authority/configuration problem (bad credentials,
	// rate limit, outage), never evidence about the patient — defer,
	// don't condemn the record to no_match.
	if resp.StatusCode != http.StatusOK {
		return Verification{Status: "deferred", Detail: fmt.Sprintf("authority returned status %d", resp.StatusCode)}
	}
	var out struct {
		Match bool   `json:"match"`
		Name  string `json:"name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return Verification{Status: "deferred", Detail: "bad authority response"}
	}
	if !out.Match {
		return Verification{Status: "no_match", Name: out.Name, Detail: "NIMC has no matching record"}
	}
	return Verification{Status: "verified", Name: out.Name}
}

// SandboxVerifier is the deterministic fixture registry for tests and
// demos — mirrors packages/adapters/nimc.py sandbox mode.
type SandboxVerifier struct {
	Fixtures map[string][2]string // nin -> [name, dob]
}

func (s SandboxVerifier) Verify(nin, dob string) Verification {
	if !ValidNINFormat(nin) {
		return Verification{Status: "invalid_format", Detail: "NIN is 11 digits"}
	}
	rec, ok := s.Fixtures[nin]
	if !ok {
		return Verification{Status: "deferred", Detail: "not in sandbox registry"}
	}
	if dob != "" && dob != rec[1] {
		return Verification{Status: "no_match", Name: rec[0], Detail: "date of birth mismatch"}
	}
	return Verification{Status: "verified", Name: rec[0]}
}

// ---------- deterministic dedupe ----------

// NormalizeName makes name comparison deterministic: trim, collapse
// whitespace, lowercase. Probabilistic/fuzzy matching is deliberately NOT
// a registration gate — it belongs to state-plane analytics where a human
// reviews candidates; registration must be explainable.
func NormalizeName(name string) string {
	return strings.ToLower(strings.Join(strings.Fields(name), " "))
}

// DuplicateReason explains why Register refused a record (and to whom the
// new attempt duplicates). Surfaced to the registrar so the resolution —
// find the existing record — is immediate.
func DuplicateReason(existing Patient) string {
	return fmt.Sprintf("duplicate of %s (%s, dob %s, facility %s)",
		existing.ID, existing.Name, existing.DOB, existing.FacilityID)
}

// ---------- phone OTP ----------

// otpHash stores codes as SHA-256 — the plain code never rests anywhere.
func otpHash(code string) string {
	sum := sha256.Sum256([]byte(code))
	return hex.EncodeToString(sum[:])
}

// newOTP generates a 6-digit code from crypto/rand.
func newOTP() (string, error) {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	n := int(b[0])<<16 | int(b[1])<<8 | int(b[2])
	return fmt.Sprintf("%06d", n%1000000), nil
}

// OTPSender delivers the challenge code. LogSender is the dev default;
// WebhookSender posts to SMS_WEBHOOK_URL (same generic seam as the
// notification rail — one aggregator integration serves both).
type OTPSender interface {
	Send(phone, text string) error
}

type LogSender struct {
	Logf func(format string, args ...any)
}

func (l LogSender) Send(phone, text string) error {
	l.Logf("otp sms -> %s: %s", phone, text)
	return nil
}

// WebhookSender posts {"to","text"} to SMS_WEBHOOK_URL — the same generic
// aggregator seam as the notification rail, so one SMS integration serves
// both OTP challenges and patient notifications.
type WebhookSender struct {
	URL   string
	Token string
}

func (s WebhookSender) Send(phone, text string) error {
	body := fmt.Sprintf(`{"to":%q,"text":%q}`, phone, text)
	req, err := http.NewRequest(http.MethodPost, s.URL, strings.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if s.Token != "" {
		req.Header.Set("Authorization", "Bearer "+s.Token)
	}
	client := &http.Client{Timeout: 8 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("sms webhook: status %d", resp.StatusCode)
	}
	return nil
}

// ---------- fraud signals ----------

// KYCSignals is the ops view of identity risk across the registry.
type KYCSignals struct {
	Total             int            `json:"total"`
	LevelDistribution map[string]int `json:"level_distribution"`
	SharedPhones      []SharedPhone  `json:"shared_phones"`  // phone bound to >1 patient
	UnverifiedNIN     int            `json:"unverified_nin"` // NIN present but never authority-verified
}

// SharedPhone flags a handset shared by multiple patients — normal for
// families, but a count above a handful is a fraud smell (agent
// registering ghosts on their own phone).
type SharedPhone struct {
	Phone      string   `json:"phone"`
	PatientIDs []string `json:"patient_ids"`
}

// ComputeSignals is pure — both store implementations feed it ListPatients.
func ComputeSignals(patients []Patient) KYCSignals {
	sig := KYCSignals{Total: len(patients), LevelDistribution: map[string]int{}}
	byPhone := map[string][]string{}
	for _, p := range patients {
		lvl := p.KYCLevel
		if lvl == "" {
			lvl = KYCSelf
		}
		sig.LevelDistribution[lvl]++
		if p.NIN != "" && p.KYCLevel != KYCNINVerified {
			sig.UnverifiedNIN++
		}
		if p.Phone != "" {
			byPhone[p.Phone] = append(byPhone[p.Phone], p.ID)
		}
	}
	for phone, ids := range byPhone {
		if len(ids) > 1 {
			sig.SharedPhones = append(sig.SharedPhones, SharedPhone{Phone: phone, PatientIDs: ids})
		}
	}
	return sig
}
