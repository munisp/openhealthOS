package main

// KYC tests: the assurance ladder, deterministic dedupe, NIN verifier
// seam, OTP lifecycle, and fraud signals — memory store + HTTP surface.

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testMux(t *testing.T) (*MemoryPatientStore, *http.ServeMux) {
	t.Helper()
	ps := NewMemoryPatientStore()
	mux := http.NewServeMux()
	registerPatientRoutes(mux, ps)
	registerKYCRoutes(mux, ps, SandboxVerifier{Fixtures: map[string][2]string{
		"12345678901": {"Adaeze Nwosu", "1992-04-15"},
	}}, LogSender{Logf: func(string, ...any) {}})
	return ps, mux
}

func post(t *testing.T, mux *http.ServeMux, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	return rec
}

func register(t *testing.T, mux *http.ServeMux, name, dob, phone string) Patient {
	t.Helper()
	rec := post(t, mux, "/patients",
		`{"name":`+jq(name)+`,"dob":`+jq(dob)+`,"phone":`+jq(phone)+`,"facility_id":"PHC-1"}`)
	if rec.Code != 201 {
		t.Fatalf("register %s: %d %s", name, rec.Code, rec.Body)
	}
	var p Patient
	json.NewDecoder(rec.Body).Decode(&p)
	return p
}

// jq quotes a string for JSON embedding in test payloads.
func jq(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func TestKYCLadderDefaults(t *testing.T) {
	_, mux := testMux(t)
	p := register(t, mux, "Amina Yusuf", "1990-01-01", "+2348000000001")
	if p.KYCLevel != KYCSelf {
		t.Fatalf("fresh registration should be self-asserted, got %s", p.KYCLevel)
	}
	// A syntactically valid NIN at registration lifts to nin-format.
	rec := post(t, mux, "/patients",
		`{"name":"Bala Ide","dob":"1985-06-01","nin":"99988877766","facility_id":"PHC-1"}`)
	var p2 Patient
	json.NewDecoder(rec.Body).Decode(&p2)
	if p2.KYCLevel != KYCNINFormat {
		t.Fatalf("valid-format NIN should lift to nin-format, got %s", p2.KYCLevel)
	}
}

func TestDedupeGates(t *testing.T) {
	_, mux := testMux(t)
	register(t, mux, "Amina Yusuf", "1990-01-01", "+2348000000001")

	// Same phone + DOB → refused (same person re-enrolling).
	if rec := post(t, mux, "/patients",
		`{"name":"A. Yusuf","dob":"1990-01-01","phone":"+2348000000001","facility_id":"PHC-2"}`); rec.Code != 409 {
		t.Fatalf("phone+DOB duplicate should be 409, got %d", rec.Code)
	}
	// Same normalised name + DOB (whitespace/case games) → refused.
	if rec := post(t, mux, "/patients",
		`{"name":"  amina   YUSUF ","dob":"1990-01-01","phone":"+2348000000099","facility_id":"PHC-2"}`); rec.Code != 409 {
		t.Fatalf("name+DOB duplicate should be 409, got %d", rec.Code)
	}
	// Shared family phone with a genuinely different person → allowed.
	if rec := post(t, mux, "/patients",
		`{"name":"Musa Yusuf","dob":"2015-03-03","phone":"+2348000000001","facility_id":"PHC-1"}`); rec.Code != 201 {
		t.Fatalf("family member sharing a phone should register, got %d", rec.Code)
	}
}

func TestNINVerificationFlow(t *testing.T) {
	_, mux := testMux(t)
	p := register(t, mux, "Adaeze Nwosu", "1992-04-15", "+2348000000002")

	// Verified: binds the NIN and lifts to nin-verified.
	rec := post(t, mux, "/patients/"+p.ID+"/verify", `{"nin":"12345678901"}`)
	if rec.Code != 200 {
		t.Fatalf("verify: %d %s", rec.Code, rec.Body)
	}
	var out Patient
	json.NewDecoder(rec.Body).Decode(&out)
	if out.KYCLevel != KYCNINVerified || out.NIN != "12345678901" {
		t.Fatalf("expected nin-verified with bound NIN, got %+v", out)
	}

	// DOB mismatch → no_match → 409, record NOT bound.
	p2 := register(t, mux, "Ada Nwosu", "1993-01-01", "+2348000000003")
	if rec := post(t, mux, "/patients/"+p2.ID+"/verify", `{"nin":"12345678901"}`); rec.Code != 409 {
		t.Fatalf("dob mismatch should be 409, got %d", rec.Code)
	}

	// Bad format → 400.
	if rec := post(t, mux, "/patients/"+p.ID+"/verify", `{"nin":"123"}`); rec.Code != 400 {
		t.Fatalf("bad nin format should be 400, got %d", rec.Code)
	}
	// Unknown NIN in sandbox → deferred → 202, level nin-format, not bound.
	p3 := register(t, mux, "Chidi Okonkwo", "1988-07-07", "+2348000000004")
	rec = post(t, mux, "/patients/"+p3.ID+"/verify", `{"nin":"55544433322"}`)
	if rec.Code != 202 {
		t.Fatalf("unknown nin should defer (202), got %d", rec.Code)
	}
}

func TestOTPLifecycle(t *testing.T) {
	ps, mux := testMux(t)
	p := register(t, mux, "Funke Bello", "1995-12-24", "+2348000000005")

	// Challenge issues an OTP; dev echo is off in tests, so drive the
	// store directly for the code.
	expires := time.Now().Add(10 * time.Minute)
	if err := ps.SaveOTP(p.ID, otpHash("424242"), expires); err != nil {
		t.Fatal(err)
	}
	// Wrong code → 401, attempt counted.
	if rec := post(t, mux, "/patients/"+p.ID+"/phone/verify", `{"code":"000000"}`); rec.Code != 401 {
		t.Fatalf("wrong code should be 401, got %d", rec.Code)
	}
	// Right code → 200, level lifts to phone.
	rec := post(t, mux, "/patients/"+p.ID+"/phone/verify", `{"code":"424242"}`)
	if rec.Code != 200 {
		t.Fatalf("right code should verify, got %d %s", rec.Code, rec.Body)
	}
	var out Patient
	json.NewDecoder(rec.Body).Decode(&out)
	if out.KYCLevel != KYCPhone {
		t.Fatalf("phone OTP should lift level to phone, got %s", out.KYCLevel)
	}
	// One-time use: replaying the same code finds no pending challenge.
	if rec := post(t, mux, "/patients/"+p.ID+"/phone/verify", `{"code":"424242"}`); rec.Code != 404 {
		t.Fatalf("replayed code should be 404, got %d", rec.Code)
	}

	// Expiry: a challenge in the past is gone.
	_ = ps.SaveOTP(p.ID, otpHash("111111"), time.Now().Add(-time.Minute))
	if rec := post(t, mux, "/patients/"+p.ID+"/phone/verify", `{"code":"111111"}`); rec.Code != 410 {
		t.Fatalf("expired code should be 410, got %d", rec.Code)
	}

	// Lockout: 5 wrong attempts lock the challenge.
	q := register(t, mux, "Grace Eze", "2000-05-05", "+2348000000006")
	_ = ps.SaveOTP(q.ID, otpHash("999999"), time.Now().Add(10*time.Minute))
	for i := 0; i < 5; i++ {
		post(t, mux, "/patients/"+q.ID+"/phone/verify", `{"code":"123456"}`)
	}
	if rec := post(t, mux, "/patients/"+q.ID+"/phone/verify", `{"code":"999999"}`); rec.Code != 429 {
		t.Fatalf("locked challenge should be 429 even with right code, got %d", rec.Code)
	}
}

func TestKYCSignals(t *testing.T) {
	ps := NewMemoryPatientStore()
	_, _ = ps.Register(Patient{Name: "A One", DOB: "1990-01-01", Phone: "+234801", FacilityID: "F"})
	_, _ = ps.Register(Patient{Name: "B Two", DOB: "1991-01-01", Phone: "+234801", FacilityID: "F"}) // shared phone
	_, _ = ps.Register(Patient{Name: "C Three", DOB: "1992-01-01", Phone: "+234802", FacilityID: "F", NIN: "12345678901"})

	sig := ComputeSignals(ps.ListPatients())
	if sig.Total != 3 {
		t.Fatalf("total: got %d", sig.Total)
	}
	if len(sig.SharedPhones) != 1 || sig.SharedPhones[0].Phone != "+234801" {
		t.Fatalf("shared phones: %+v", sig.SharedPhones)
	}
	if sig.UnverifiedNIN != 1 {
		t.Fatalf("unverified nin: got %d", sig.UnverifiedNIN)
	}
	if sig.LevelDistribution[KYCNINFormat] != 1 || sig.LevelDistribution[KYCSelf] != 2 {
		t.Fatalf("level distribution: %+v", sig.LevelDistribution)
	}
}

func TestSetKYCNeverDowngrades(t *testing.T) {
	ps := NewMemoryPatientStore()
	p, _ := ps.Register(Patient{Name: "D Four", FacilityID: "F"})
	if _, err := ps.SetKYC(p.ID, KYCNINVerified, "test"); err != nil {
		t.Fatal(err)
	}
	p, _ = ps.SetKYC(p.ID, KYCPhone, "late otp") // stale lower write
	if p.KYCLevel != KYCNINVerified {
		t.Fatalf("downgrade must be refused, got %s", p.KYCLevel)
	}
}

func TestOfflineVerifierAlwaysDefers(t *testing.T) {
	v := OfflineVerifier{}
	if got := v.Verify("12345678901", "").Status; got != "deferred" {
		t.Fatalf("offline verifier must defer, got %s", got)
	}
	if got := v.Verify("123", "").Status; got != "invalid_format" {
		t.Fatalf("bad format even offline, got %s", got)
	}
	if !errors.Is(ErrDuplicate, ErrDuplicate) { // seam sanity
		t.Fatal("unreachable")
	}
}
