package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// fake gateway + homeserver pair for the matrix leg.
func matrixTestRig(t *testing.T, bound bool, hsDown bool) (*MatrixProvider, *int) {
	t.Helper()
	sent := 0
	hs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if hsDown {
			http.Error(w, "down", 502)
			return
		}
		if r.Method == "PUT" && strings.Contains(r.URL.Path, "/send/m.room.message/") {
			var b map[string]string
			json.NewDecoder(r.Body).Decode(&b)
			if b["body"] == "" || r.Header.Get("Authorization") == "" {
				http.Error(w, "bad", 400)
				return
			}
			sent++
			json.NewEncoder(w).Encode(map[string]string{"event_id": "$x"})
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(hs.Close)

	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/internal/matrix-room" && bound {
			json.NewEncoder(w).Encode(map[string]string{"room_id": "!room1:hs"})
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(gw.Close)

	return NewMatrixProvider(gw.URL, hs.URL, "tok"), &sent
}

func TestMatrixProviderDeliversToBoundRoom(t *testing.T) {
	p, sent := matrixTestRig(t, true, false)
	if err := p.Send("08011112222", "OpenHealth: your test result is ready."); err != nil {
		t.Fatalf("send failed: %v", err)
	}
	if *sent != 1 {
		t.Fatalf("homeserver did not receive the message (%d)", *sent)
	}
}

func TestMatrixProviderUnboundPhoneFails(t *testing.T) {
	p, sent := matrixTestRig(t, false, false)
	err := p.Send("08000000000", "text")
	if err == nil {
		t.Fatal("unbound phone must error so the chain falls through")
	}
	if *sent != 0 {
		t.Fatal("nothing must be sent for an unbound phone")
	}
}

func TestMatrixProviderHomeserverDownFails(t *testing.T) {
	p, _ := matrixTestRig(t, true, true)
	if err := p.Send("08011112222", "text"); err == nil {
		t.Fatal("dead homeserver must error so the breaker opens")
	}
}

func TestMatrixProviderFromEnvLoudWhenUnconfigured(t *testing.T) {
	t.Setenv("MATRIX_HOMESERVER", "")
	t.Setenv("MATRIX_ACCESS_TOKEN", "")
	p := matrixProviderFromEnv("http://gw")
	if err := p.Send("x", "y"); err == nil {
		t.Fatal("unconfigured matrix leg must fail loudly, not skip")
	}
	t.Setenv("MATRIX_HOMESERVER", "http://hs")
	t.Setenv("MATRIX_ACCESS_TOKEN", "tok")
	if p2 := matrixProviderFromEnv("http://gw"); p2.Name() != "matrix" {
		t.Fatalf("configured leg must be the matrix provider, got %s", p2.Name())
	}
}

func TestMatrixProviderInChainSpec(t *testing.T) {
	t.Setenv("MATRIX_HOMESERVER", "http://hs")
	t.Setenv("MATRIX_ACCESS_TOKEN", "tok")
	t.Setenv("SMS_PROVIDERS", "matrix:http://gw,log")
	p := providerFromEnv()
	chain, ok := p.(*ChainProvider)
	if !ok || len(chain.legs) != 2 {
		t.Fatalf("expected 2-leg chain, got %#v", p)
	}
	if chain.legs[0].p.Name() != "matrix" {
		t.Fatalf("matrix must be the first (sovereign) leg, got %s", chain.legs[0].p.Name())
	}
}
