package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
)

// CLI:
//   ota keygen                          → prints base64 pub/priv pair
//   ota sign    <manifest.json> <priv>  → signs in place
//   ota verify  <manifest.json> <pub>   → exit 1 unless signature valid
//   ota check   <manifest.json> <pub> <device-id> <current-version>
//                                       → exit 0 iff the device should apply
func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "keygen":
		err = keygen()
	case "sign":
		err = withManifest(os.Args[2], func(m *Manifest) error {
			priv := decodeKey(os.Args[3], ed25519.PrivateKeySize)
			m.Sign(priv)
			raw, _ := json.MarshalIndent(m, "", "  ")
			return os.WriteFile(os.Args[2], raw, 0o600)
		})
	case "verify":
		err = withManifest(os.Args[2], func(m *Manifest) error {
			return m.Verify(decodeKey(os.Args[3], ed25519.PublicKeySize))
		})
	case "check":
		err = withManifest(os.Args[2], func(m *Manifest) error {
			return ShouldApply(m, decodeKey(os.Args[3], ed25519.PublicKeySize), os.Args[4], os.Args[5])
		})
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "ota:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: ota keygen | sign <m> <priv> | verify <m> <pub> | check <m> <pub> <device> <cur>")
}

func keygen() error {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	fmt.Println("public: ", base64.StdEncoding.EncodeToString(pub))
	fmt.Println("private:", base64.StdEncoding.EncodeToString(priv))
	return nil
}

func decodeKey(s string, size int) []byte {
	raw, err := base64.StdEncoding.DecodeString(s)
	if err != nil || len(raw) != size {
		fmt.Fprintln(os.Stderr, "ota: bad key")
		os.Exit(2)
	}
	return raw
}

func withManifest(path string, f func(*Manifest) error) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var m Manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	return f(&m)
}
