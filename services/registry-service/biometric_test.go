package main

// Biometric engine tests (ADR-0017) — matcher bounds, the anti-gaming
// dedupe gate, ladder upgrade on verify, seal/open honesty, threshold
// policy, and the HTTP surface.

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func bioFixture(t *testing.T) (*BiometricEngine, *MemoryPatientStore) {
	t.Helper()
	patients := NewMemoryPatientStore()
	if _, err := patients.Register(Patient{Name: "Amina Bello", Phone: "08031234567", DOB: "1990-01-01", FacilityID: "FAC-1"}); err != nil {
		t.Fatalf("register Amina: %v", err)
	}
	if _, err := patients.Register(Patient{Name: "Chidi Okafor", Phone: "08037654321", DOB: "1985-06-15", FacilityID: "FAC-1"}); err != nil {
		t.Fatalf("register Chidi: %v", err)
	}
	e := NewBiometricEngine(NewMemoryBiometricStore(), patients, nil, func(string) string { return "" })
	return e, patients
}

func patientIDByName(t *testing.T, ps *MemoryPatientStore, name string) string {
	t.Helper()
	for _, p := range ps.ListPatients() {
		if p.Name == name {
			return p.ID
		}
	}
	t.Fatalf("patient %s not found", name)
	return ""
}

// tpl builds a deterministic 32-byte template from a seed.
func tpl(seed byte) []byte {
	b := make([]byte, 32)
	for i := range b {
		b[i] = seed ^ byte(i*7)
	}
	return b
}

func TestSimMatcherBounds(t *testing.T) {
	m := SimMatcher{}
	a := tpl(0x5A)
	if got := m.Similarity(a, a); got != 1.0 {
		t.Fatalf("identical templates must score 1.0, got %f", got)
	}
	b := tpl(0xA5)
	if got := m.Similarity(a, b); got >= 1.0 || got < 0 {
		t.Fatalf("distinct templates must score in [0,1), got %f", got)
	}
	if got := m.Similarity(nil, a); got != 0 {
		t.Fatalf("empty template scores 0, got %f", got)
	}
	// One byte different out of 32 → high but not perfect.
	c := append([]byte(nil), a...)
	c[0] ^= 0xFF
	if got := m.Similarity(a, c); got <= 0.85 || got >= 1.0 {
		t.Fatalf("near-identical expected in (0.85, 1.0), got %f", got)
	}
}

func TestMatchThresholdPolicy(t *testing.T) {
	if got := matchThreshold(func(string) string { return "" }); got != 0.85 {
		t.Fatalf("default threshold 0.85, got %f", got)
	}
	env := map[string]string{"BIOMETRIC_THRESHOLD": "0.92"}
	if got := matchThreshold(func(k string) string { return env[k] }); got != 0.92 {
		t.Fatalf("env threshold 0.92, got %f", got)
	}
	bad := map[string]string{"BIOMETRIC_THRESHOLD": "1.7"}
	if got := matchThreshold(func(k string) string { return bad[k] }); got != 0.85 {
		t.Fatalf("out-of-range env falls back to 0.85, got %f", got)
	}
}

func TestEnrolDedupeRefusesSecondIdentity(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	chidi := patientIDByName(t, ps, "Chidi Okafor")
	if err := e.EnrolBiometric(amina, "right-index", tpl(0x11)); err != nil {
		t.Fatalf("enrol Amina: %v", err)
	}
	// The same body presenting as another patient is the fraud case —
	// refused loudly.
	if err := e.EnrolBiometric(chidi, "left-thumb", tpl(0x11)); err != errDuplicateIdentity {
		t.Fatalf("expected duplicate identity, got %v", err)
	}
	// A genuinely different finger/person enrols fine.
	if err := e.EnrolBiometric(chidi, "right-index", tpl(0x77)); err != nil {
		t.Fatalf("enrol Chidi: %v", err)
	}
	// Re-enrolling the same patient+finger is idempotent.
	if err := e.EnrolBiometric(amina, "right-index", tpl(0x11)); err != nil {
		t.Fatalf("re-enrol same finger: %v", err)
	}
	if got := len(e.Store.ForPatient(amina)); got != 1 {
		t.Fatalf("idempotent re-enrol keeps one row, got %d", got)
	}
}

func TestEnrolUnknownPatient(t *testing.T) {
	e, _ := bioFixture(t)
	if err := e.EnrolBiometric("PT-9999", "right-index", tpl(0x11)); err == nil {
		t.Fatal("enrolment for an unknown patient must fail")
	}
}

func TestVerifyUpgradesLadder(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	if err := e.EnrolBiometric(amina, "right-index", tpl(0x11)); err != nil {
		t.Fatalf("enrol: %v", err)
	}
	score, ok, err := e.VerifyBiometric(amina, tpl(0x11))
	if err != nil || !ok {
		t.Fatalf("verify: ok=%v score=%f err=%v", ok, score, err)
	}
	p, _ := ps.GetPatient(amina)
	if p.KYCLevel != KYCBiometricVerified {
		t.Fatalf("ladder must reach biometric-verified, got %q", p.KYCLevel)
	}
	// A wrong finger does not match.
	_, ok, err = e.VerifyBiometric(amina, tpl(0x99))
	if err != nil {
		t.Fatalf("verify wrong finger: %v", err)
	}
	if ok {
		t.Fatal("wrong finger must not match")
	}
	// Unenrolled patient → errNotEnrolled.
	chidi := patientIDByName(t, ps, "Chidi Okafor")
	if _, _, err := e.VerifyBiometric(chidi, tpl(0x11)); err != errNotEnrolled {
		t.Fatalf("expected errNotEnrolled, got %v", err)
	}
}

func TestVerifyNeverDowngradesNINVerified(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	if _, err := ps.SetKYC(amina, KYCNINVerified, "nin verified"); err != nil {
		t.Fatalf("set nin-verified: %v", err)
	}
	// A failed biometric attempt must not touch the ladder.
	e.EnrolBiometric(amina, "right-index", tpl(0x11))
	e.VerifyBiometric(amina, tpl(0x42))
	p, _ := ps.GetPatient(amina)
	if p.KYCLevel != KYCNINVerified {
		t.Fatalf("failed verify must not downgrade, got %q", p.KYCLevel)
	}
	// And once biometric-verified, a later nin-format bump cannot drag down.
	e.VerifyBiometric(amina, tpl(0x11))
	ps.SetKYC(amina, KYCNINFormat, "late format check")
	p, _ = ps.GetPatient(amina)
	if p.KYCLevel != KYCBiometricVerified {
		t.Fatalf("biometric-verified is top of ladder, got %q", p.KYCLevel)
	}
}

func TestTemplatesSealedOrLoudlyPlain(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	e.EnrolBiometric(amina, "right-index", tpl(0x11))
	stored := e.Store.ForPatient(amina)[0].Sealed
	if !strings.HasPrefix(stored, "PLAIN:") {
		t.Fatalf("unprovisioned dev must store the PLAIN sentinel, got %q", stored[:8])
	}
	raw, err := e.openTemplate(stored)
	if err != nil || !bytes.Equal(raw, tpl(0x11)) {
		t.Fatalf("open round-trip: %v", err)
	}
}

func TestIdentifyReturnsRankedCandidates(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	chidi := patientIDByName(t, ps, "Chidi Okafor")
	e.EnrolBiometric(amina, "right-index", tpl(0x11))
	e.EnrolBiometric(chidi, "right-index", tpl(0x22))
	hits := e.identify(tpl(0x11))
	if len(hits) != 1 || hits[0].PatientID != amina {
		t.Fatalf("identify must find Amina only, got %+v", hits)
	}
}

func TestBiometricHTTP(t *testing.T) {
	e, ps := bioFixture(t)
	amina := patientIDByName(t, ps, "Amina Bello")
	mux := http.NewServeMux()
	registerBiometricRoutes(mux, e)
	post := func(path string, body map[string]string) *httptest.ResponseRecorder {
		b, _ := json.Marshal(body)
		req := httptest.NewRequest("POST", path, bytes.NewReader(b))
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		return rec
	}
	hexTpl := hex.EncodeToString(tpl(0x11))
	rec := post("/kyc/biometric/enrol", map[string]string{"patient_id": amina, "finger": "right-index", "template": hexTpl})
	if rec.Code != http.StatusCreated {
		t.Fatalf("enrol status %d body %s", rec.Code, rec.Body)
	}
	rec = post("/kyc/biometric/verify", map[string]string{"patient_id": amina, "template": hexTpl})
	if rec.Code != 200 {
		t.Fatalf("verify status %d", rec.Code)
	}
	var vr struct {
		Match bool    `json:"match"`
		Score float64 `json:"score"`
	}
	json.NewDecoder(rec.Body).Decode(&vr)
	if !vr.Match || vr.Score != 1.0 {
		t.Fatalf("verify body %+v", vr)
	}
	rec = post("/kyc/biometric/identify", map[string]string{"template": hexTpl})
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), amina) {
		t.Fatalf("identify status %d body %s", rec.Code, rec.Body)
	}
	// Bad template length → 400.
	rec = post("/kyc/biometric/enrol", map[string]string{"patient_id": amina, "finger": "left-thumb", "template": "abcd"})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("short template must be 400, got %d", rec.Code)
	}
	// Unknown patient → 404.
	rec = post("/kyc/biometric/enrol", map[string]string{"patient_id": "PT-9999", "finger": "right-index", "template": hexTpl})
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown patient must be 404, got %d", rec.Code)
	}
}
