package main

import (
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"os"
)

func die(err error) { fmt.Fprintln(os.Stderr, "ipsqr:", err); os.Exit(1) }

func main() {
	if len(os.Args) < 2 {
		die(fmt.Errorf("usage: ipsqr genkey | sign -seed B64 -in FILE | verify -pub B64 QR"))
	}
	switch os.Args[1] {
	case "genkey":
		pub, priv, err := GenerateKey()
		if err != nil {
			die(err)
		}
		fmt.Println("seed:", base64.RawURLEncoding.EncodeToString(priv.Seed()))
		fmt.Println("pub: ", base64.RawURLEncoding.EncodeToString(pub))
	case "sign":
		fs := flag.NewFlagSet("sign", flag.ExitOnError)
		seed := fs.String("seed", "", "base64url private seed")
		in := fs.String("in", "", "IPS JSON file")
		fs.Parse(os.Args[2:])
		raw, err := base64.RawURLEncoding.DecodeString(*seed)
		if err != nil {
			die(err)
		}
		priv, err := PrivateFromSeed(raw)
		if err != nil {
			die(err)
		}
		var v any
		src, err := os.ReadFile(*in)
		if err != nil {
			die(err)
		}
		if err := json.Unmarshal(src, &v); err != nil {
			die(err)
		}
		payload, err := CanonicalJSON(v)
		if err != nil {
			die(err)
		}
		fmt.Println(SignPayload(payload, priv))
	case "verify":
		fs := flag.NewFlagSet("verify", flag.ExitOnError)
		pubStr := fs.String("pub", "", "base64url public key")
		fs.Parse(os.Args[2:])
		pub, err := base64.RawURLEncoding.DecodeString(*pubStr)
		if err != nil {
			die(err)
		}
		if fs.NArg() != 1 {
			die(fmt.Errorf("verify needs the QR payload as an argument"))
		}
		payload, err := VerifyPayload(fs.Arg(0), pub)
		if err != nil {
			die(err)
		}
		fmt.Println(string(payload))
	default:
		die(fmt.Errorf("unknown subcommand %q", os.Args[1]))
	}
}
