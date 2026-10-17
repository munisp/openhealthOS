package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"testing"
)

func keypair(t *testing.T) (ed25519.PublicKey, ed25519.PrivateKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return pub, priv
}

func sample() *Manifest {
	return NewManifest("1.4.2", "pilot", []Image{
		{Name: "sync-agent", Digest: "sha256:aaa"},
		{Name: "telemetry-collector", Digest: "sha256:bbb"},
	})
}

func TestSignVerifyRoundTrip(t *testing.T) {
	pub, priv := keypair(t)
	m := sample()
	m.Sign(priv)
	if err := m.Verify(pub); err != nil {
		t.Fatalf("fresh signature rejected: %v", err)
	}
}

func TestTamperDetected(t *testing.T) {
	pub, priv := keypair(t)
	m := sample()
	m.Sign(priv)
	m.Images[0].Digest = "sha256:evil" // attacker swaps an image post-signing
	if err := m.Verify(pub); !errors.Is(err, ErrBadSignature) {
		t.Fatalf("tampered manifest accepted: %v", err)
	}
	_, other := keypair(t)
	m2 := sample()
	m2.Sign(other) // wrong key
	if err := m2.Verify(pub); !errors.Is(err, ErrBadSignature) {
		t.Fatal("wrong-key signature accepted")
	}
}

func TestStaleVersionRefused(t *testing.T) {
	pub, priv := keypair(t)
	m := sample()
	m.MinVersion = "1.5.0" // manifest is 1.4.2 — below floor
	m.Sign(priv)
	if err := ShouldApply(m, pub, "dev-1", "1.4.1"); !errors.Is(err, ErrStaleVersion) {
		t.Fatalf("below-floor manifest applicable: %v", err)
	}
	// downgrade attempt: 1.4.2 onto a node already at 1.6.0
	m2 := sample()
	m2.Sign(priv)
	if err := ShouldApply(m2, pub, "dev-1", "1.6.0"); !errors.Is(err, ErrStaleVersion) {
		t.Fatalf("downgrade applicable: %v", err)
	}
}

func TestCohortDeterministicAndBounded(t *testing.T) {
	m := sample()
	m.CohortPct = 10
	in := 0
	for i := 0; i < 1000; i++ {
		if m.InCohort("device-" + string(rune(i))) {
			in++
		}
	}
	if in == 0 || in > 200 {
		t.Fatalf("cohort %d/1000 — expected roughly 100, bounded", in)
	}
	// determinism: same device, same answer
	a, b := m.InCohort("edge-kd-04"), m.InCohort("edge-kd-04")
	if a != b {
		t.Fatal("cohort decision not deterministic")
	}
	// 100% cohort admits everything
	m.CohortPct = 100
	if !m.InCohort("anything") {
		t.Fatal("full cohort must admit all devices")
	}
}

func TestShouldApplyHappyPath(t *testing.T) {
	pub, priv := keypair(t)
	m := sample()
	m.Sign(priv)
	if err := ShouldApply(m, pub, "edge-kd-04", "1.4.1"); err != nil {
		t.Fatalf("valid upgrade refused: %v", err)
	}
}
