package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"
)

func die(err error) { fmt.Fprintln(os.Stderr, "provcred:", err); os.Exit(1) }

func main() {
	if len(os.Args) < 2 {
		die(fmt.Errorf("usage: provcred genkey | issue | verify"))
	}
	switch os.Args[1] {
	case "genkey":
		pub, priv, err := ed25519.GenerateKey(nil)
		if err != nil {
			die(err)
		}
		fmt.Println("seed:", base64.RawURLEncoding.EncodeToString(priv.Seed()))
		fmt.Println("pub: ", base64.RawURLEncoding.EncodeToString(pub))
	case "issue":
		fs := flag.NewFlagSet("issue", flag.ExitOnError)
		seed := fs.String("seed", "", "issuer base64url seed")
		issuer := fs.String("issuer", "MDCN", "issuing authority")
		subjFile := fs.String("subject", "", "provider JSON file")
		ttlDays := fs.Int("ttl-days", 365, "validity in days")
		fs.Parse(os.Args[2:])
		raw, err := base64.RawURLEncoding.DecodeString(*seed)
		if err != nil {
			die(err)
		}
		priv := ed25519.NewKeyFromSeed(raw)
		data, err := os.ReadFile(*subjFile)
		if err != nil {
			die(err)
		}
		var p Provider
		if err := json.Unmarshal(data, &p); err != nil {
			die(err)
		}
		cred, err := Issue(*issuer, p, priv, time.Duration(*ttlDays)*24*time.Hour)
		if err != nil {
			die(err)
		}
		out, _ := json.MarshalIndent(cred, "", "  ")
		fmt.Println(string(out))
	case "verify":
		fs := flag.NewFlagSet("verify", flag.ExitOnError)
		pubStr := fs.String("pub", "", "issuer base64url public key")
		revFile := fs.String("revocations", "", "revoked licence numbers, one per line")
		fs.Parse(os.Args[2:])
		pub, err := base64.RawURLEncoding.DecodeString(*pubStr)
		if err != nil {
			die(err)
		}
		var cred Credential
		if err := json.NewDecoder(os.Stdin).Decode(&cred); err != nil {
			die(err)
		}
		var revoked map[string]bool
		if *revFile != "" {
			data, err := os.ReadFile(*revFile)
			if err != nil {
				die(err)
			}
			revoked = ParseRevocations(strings.Split(string(data), "\n"))
		}
		if err := Verify(cred, pub, revoked, time.Now().UTC()); err != nil {
			die(err)
		}
		fmt.Println("VALID", cred.Subject.LicenceNo, cred.Subject.Scope)
	default:
		die(fmt.Errorf("unknown subcommand %q", os.Args[1]))
	}
}
