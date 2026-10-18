package main

// End-to-end KYC conformance (ADR-0015): the platform's real HTTPVerifier
// against the real nimc-sandbox binary, over real HTTP on localhost.
// This is the proof that "swap in the licensed NIMC endpoint" is a
// configuration change, not a code change: the wire contract is tested
// here, and every status the authority can produce has a defined effect
// on the KYC ladder.

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func startNIMCSandbox(t *testing.T) (url, token string) {
	t.Helper()
	token = "conformance-token"

	// Build the real binary — the conformance claim covers the shipped
	// artifact, not a test double. Build from inside the tool's own
	// module (it sits outside this one).
	bin := filepath.Join(t.TempDir(), "nimc-sandbox")
	build := exec.Command("go", "build", "-o", bin, ".")
	build.Dir = "../../tools/nimc-sandbox"
	if out, err := build.CombinedOutput(); err != nil {
		t.Skipf("sandbox build unavailable in this environment: %v\n%s", err, out)
	}

	// Free port, then hand it to the server.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()

	cmd := exec.Command(bin, "-listen", addr, "-token", token)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("start sandbox: %v", err)
	}
	t.Cleanup(func() { cmd.Process.Kill(); cmd.Wait() })

	// Wait for readiness (bounded).
	url = "http://" + addr
	deadline := time.Now().Add(10 * time.Second)
	for {
		resp, err := http.Get(url + "/healthz")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return url + "/verify", token
			}
		}
		if time.Now().After(deadline) {
			t.Fatal("sandbox never became ready")
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func TestHTTPVerifierAgainstNIMCSandbox(t *testing.T) {
	url, token := startNIMCSandbox(t)
	v := HTTPVerifier{URL: url, Token: token}

	cases := []struct {
		name       string
		nin, dob   string
		wantStatus string
		wantName   string
	}{
		{"known identity, matching dob", "10000000001", "1990-04-12", "verified", "Adaeze Ngozi Okafor"},
		{"known identity, dob omitted", "10000000002", "", "verified", "Ibrahim Musa Bello"},
		{"dob mismatch", "10000000001", "1970-01-01", "no_match", "Adaeze Ngozi Okafor"},
		{"unknown nin", "99999999999", "", "no_match", ""},
		{"bad format never hits the wire", "123", "", "invalid_format", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := v.Verify(tc.nin, tc.dob)
			if got.Status != tc.wantStatus {
				t.Fatalf("status = %q, want %q (detail %q)", got.Status, tc.wantStatus, got.Detail)
			}
			if got.Name != tc.wantName {
				t.Fatalf("name = %q, want %q", got.Name, tc.wantName)
			}
		})
	}

	// Wrong credentials: deferred, NOT no_match — a config problem must
	// never condemn a patient's record.
	bad := HTTPVerifier{URL: url, Token: "wrong"}
	got := bad.Verify("10000000001", "1990-04-12")
	if got.Status != "deferred" {
		t.Fatalf("bad token: status = %q, want deferred", got.Status)
	}

	// Authority down: deferred.
	dead := HTTPVerifier{URL: "http://127.0.0.1:1/verify", Token: token}
	if got := dead.Verify("10000000001", ""); got.Status != "deferred" {
		t.Fatalf("down authority: status = %q, want deferred", got.Status)
	}
}

// The ladder end to end over HTTP: register at "self" → POST /verify →
// the sandbox confirms → the record climbs to nin-verified.
func TestKycLadderEndToEndOverHTTP(t *testing.T) {
	url, token := startNIMCSandbox(t)
	ps := NewMemoryPatientStore()
	mux := http.NewServeMux()
	registerPatientRoutes(mux, ps)
	registerKYCRoutes(mux, ps, HTTPVerifier{URL: url, Token: token}, LogSender{Logf: func(string, ...any) {}})

	p, err := ps.Register(Patient{ID: "P-KYC", Name: "Adaeze Okafor", DOB: "1990-04-12", Phone: "+2348001", FacilityID: "F1"})
	if err != nil {
		t.Fatal(err)
	}
	if p.KYCLevel != KYCSelf {
		t.Fatalf("fresh registration at %q", p.KYCLevel)
	}
	req := httptest.NewRequest(http.MethodPost, "/patients/P-KYC/verify",
		strings.NewReader(`{"nin":"10000000001"}`))
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("verify: %d %s", rr.Code, rr.Body.String())
	}
	after, _ := ps.GetPatient("P-KYC")
	if after.KYCLevel != KYCNINVerified {
		t.Fatalf("level = %q, want nin-verified (detail %q)", after.KYCLevel, after.KYCDetail)
	}
	if after.NIN != "10000000001" {
		t.Fatalf("nin not linked: %q", after.NIN)
	}
	if !strings.Contains(after.KYCDetail, "Adaeze Ngozi Okafor") {
		t.Fatalf("detail missing authority name: %q", after.KYCDetail)
	}
}
