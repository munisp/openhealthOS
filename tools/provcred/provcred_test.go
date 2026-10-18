package main

import (
	"crypto/ed25519"
	"testing"
	"time"
)

func fixture(t *testing.T) (ed25519.PublicKey, ed25519.PrivateKey, Credential) {
	pub, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	cred, err := Issue("MDCN", Provider{ProviderID: "PR-1", Name: "Dr. Ada",
		Council: "MDCN", LicenceNo: "MDCN-12345", Scope: "physician",
		FacilityID: "GH-KAWO"}, priv, 365*24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	return pub, priv, cred
}

func TestIssueVerifyRoundTrip(t *testing.T) {
	pub, _, cred := fixture(t)
	if err := Verify(cred, pub, nil, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
}

func TestTamperedSubjectRejected(t *testing.T) {
	pub, _, cred := fixture(t)
	cred.Subject.Scope = "specialist-surgeon" // upgrade attempt
	if err := Verify(cred, pub, nil, time.Now().UTC()); err != ErrBadSig {
		t.Fatalf("want ErrBadSig, got %v", err)
	}
}

func TestWrongIssuerKeyRejected(t *testing.T) {
	_, _, cred := fixture(t)
	pub2, _, _ := ed25519.GenerateKey(nil)
	if err := Verify(cred, pub2, nil, time.Now().UTC()); err != ErrBadSig {
		t.Fatalf("want ErrBadSig, got %v", err)
	}
}

func TestExpiredRejected(t *testing.T) {
	pub, _, cred := fixture(t)
	future := time.Now().Add(400 * 24 * time.Hour).UTC()
	if err := Verify(cred, pub, nil, future); err != ErrExpired {
		t.Fatalf("want ErrExpired, got %v", err)
	}
}

func TestRevokedRejected(t *testing.T) {
	pub, _, cred := fixture(t)
	revoked := ParseRevocations([]string{"MDCN-12345"})
	if err := Verify(cred, pub, revoked, time.Now().UTC()); err != ErrRevoked {
		t.Fatalf("want ErrRevoked, got %v", err)
	}
}
