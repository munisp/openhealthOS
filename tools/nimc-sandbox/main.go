// NIMC sandbox — a deterministic stand-in for the national identity
// authority's verification API (ADR-0015). It speaks exactly the wire
// contract registry-service's HTTPVerifier consumes:
//
//	POST /verify {"nin":"10000000001","dob":"1990-04-12"}
//	    Authorization: Bearer <token>   (when -token is set)
//	→ 200 {"match":true, "name":"Adaeze Ngozi Okafor"}
//	→ 200 {"match":false, "name":...}   unknown NIN or DOB mismatch
//	→ 400 {"match":false}               malformed request / bad NIN format
//	→ 401 {"match":false}               wrong/missing bearer token
//
//	POST /biometric/verify {"nin":"10000000001","template":"<hex>"}
//	→ 200 {"match":true, "score":1.0, "name":...}  authority-side
//	  biometric confirmation (ADR-0017): NIMC's real service verifies a
//	  live capture against the biometric bound to the NIN at enrolment.
//	  Fixture fingerprints are deterministic synthetics — sha256 of the
//	  NIN — so the default fingerprint for a NIN is computable in tests.
//
// The point: the platform's NIN verification seam is proven end-to-end
// over real HTTP against this binary (see the registry-service
// conformance test), so swapping in the licensed NIMC endpoint is a
// configuration change (NIN_VERIFY_URL + token), never a code change.
// Stdlib-only, per the three-language doctrine.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"os"
	"strings"
)

// fixture is one identity the sandbox "authority" knows.
type fixture struct {
	Name string `json:"name"`
	DOB  string `json:"dob"`
}

// defaultFixtures keep demos honest out of the box — deterministic,
// obviously-synthetic identities. Production fixtures come from
// -fixtures (a deployment artifact, never committed with real PII).
var defaultFixtures = map[string]fixture{
	"10000000001": {Name: "Adaeze Ngozi Okafor", DOB: "1990-04-12"},
	"10000000002": {Name: "Ibrahim Musa Bello", DOB: "1985-11-30"},
	"10000000003": {Name: "Chinwe Adaeze Eze", DOB: "1978-02-14"},
	"10000000004": {Name: "Oluwaseun Adebayo", DOB: "2001-07-08"},
	"10000000005": {Name: "Fatima Abubakar Sule", DOB: "1995-09-21"},
}

func validNIN(nin string) bool {
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

func newSandboxHandler(fixtures map[string]fixture, token string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true,"identities":` + itoa(len(fixtures)) + `}`))
	})
	mux.HandleFunc("/verify", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			w.Write([]byte(`{"match":false,"error":"method not allowed"}`))
			return
		}
		if token != "" {
			got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			if got != token {
				w.WriteHeader(http.StatusUnauthorized)
				w.Write([]byte(`{"match":false,"error":"unauthorized"}`))
				return
			}
		}
		var body struct {
			NIN string `json:"nin"`
			DOB string `json:"dob"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || !validNIN(body.NIN) {
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"match":false,"error":"bad request"}`))
			return
		}
		f, ok := fixtures[body.NIN]
		switch {
		case !ok:
			w.Write([]byte(`{"match":false}`))
		case body.DOB != "" && body.DOB != f.DOB:
			raw, _ := json.Marshal(map[string]any{"match": false, "name": f.Name})
			w.Write(raw)
		default:
			raw, _ := json.Marshal(map[string]any{"match": true, "name": f.Name})
			w.Write(raw)
		}
	})
	mux.HandleFunc("/biometric/verify", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			w.Write([]byte(`{"match":false,"error":"method not allowed"}`))
			return
		}
		if token != "" {
			got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			if got != token {
				w.WriteHeader(http.StatusUnauthorized)
				w.Write([]byte(`{"match":false,"error":"unauthorized"}`))
				return
			}
		}
		var body struct {
			NIN      string `json:"nin"`
			Template string `json:"template"` // hex-encoded live capture
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || !validNIN(body.NIN) {
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"match":false,"error":"bad request"}`))
			return
		}
		raw, derr := hex.DecodeString(body.Template)
		if derr != nil || len(raw) < 16 {
			w.WriteHeader(http.StatusBadRequest)
			w.Write([]byte(`{"match":false,"error":"bad request"}`))
			return
		}
		f, ok := fixtures[body.NIN]
		if !ok {
			w.Write([]byte(`{"match":false,"score":0}`))
			return
		}
		score := hammingSimilarity(raw, fixtureFingerprint(body.NIN))
		raw2, _ := json.Marshal(map[string]any{
			"match": score >= 0.85, "score": score, "name": f.Name,
		})
		w.Write(raw2)
	})
	return mux
}

// fixtureFingerprint is the deterministic synthetic biometric bound to a
// NIN — sha256(nin). Obviously synthetic; a deployment fixture set never
// carries real biometrics in this sandbox.
func fixtureFingerprint(nin string) []byte {
	sum := sha256.Sum256([]byte("nimc-fp:" + nin))
	return sum[:]
}

// hammingSimilarity mirrors the registry's SimMatcher semantics so the
// two sides agree on what a score means over the wire.
func hammingSimilarity(a, b []byte) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	same := 0
	for i := 0; i < n; i++ {
		x := a[i] ^ b[i]
		set := 0
		for x != 0 {
			set += int(x & 1)
			x >>= 1
		}
		same += 8 - set
	}
	return float64(same) / float64(n*8)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func loadFixtures(path string) (map[string]fixture, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var out map[string]fixture
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	for nin := range out {
		if !validNIN(nin) {
			return nil, errInvalidFixture{nin}
		}
	}
	return out, nil
}

type errInvalidFixture struct{ nin string }

func (e errInvalidFixture) Error() string { return "fixture NIN not 11 digits: " + e.nin }

func main() {
	listen := flag.String("listen", ":8095", "listen address")
	fixturesPath := flag.String("fixtures", "", "JSON file of nin -> {name, dob} (defaults to built-in synthetic identities)")
	token := flag.String("token", "", "bearer token to require (empty = open, dev only)")
	flag.Parse()

	fixtures := defaultFixtures
	if *fixturesPath != "" {
		loaded, err := loadFixtures(*fixturesPath)
		if err != nil {
			log.Fatalf("fixtures: %v", err)
		}
		fixtures = loaded
	}
	if *token == "" {
		log.Print("WARNING: no -token set — sandbox accepts unauthenticated verifies (dev only)")
	}
	log.Printf("nimc-sandbox on %s (%d identities)", *listen, len(fixtures))
	log.Fatal(http.ListenAndServe(*listen, newSandboxHandler(fixtures, *token)))
}
