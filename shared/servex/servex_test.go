package servex

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"syscall"
	"testing"
	"time"
)

func TestMetricsEndpointAndCounters(t *testing.T) {
	m := &Metrics{Started: time.Now()}
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/ok":
			w.Write([]byte("fine"))
		case "/bad":
			http.Error(w, "nope", http.StatusBadRequest)
		case "/boom":
			http.Error(w, "boom", http.StatusInternalServerError)
		}
	})
	srv := httptest.NewServer(m.observe(inner, "/metrics"))
	defer srv.Close()

	for _, path := range []string{"/ok", "/ok", "/bad", "/boom"} {
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatalf("get %s: %v", path, err)
		}
		resp.Body.Close()
	}
	resp, err := http.Get(srv.URL + "/metrics")
	if err != nil {
		t.Fatalf("metrics: %v", err)
	}
	defer resp.Body.Close()
	var metrics map[string]any
	json.NewDecoder(resp.Body).Decode(&metrics)
	if metrics["requests_total"].(float64) != 4 {
		t.Fatalf("requests: %v", metrics)
	}
	if metrics["errors_4xx_total"].(float64) != 1 || metrics["errors_5xx_total"].(float64) != 1 {
		t.Fatalf("error classes: %v", metrics)
	}
}

func TestGracefulShutdownOnSignal(t *testing.T) {
	// Find a free port, then serve until SIGTERM arrives.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close()

	done := make(chan error, 1)
	go func() {
		done <- ListenAndServe(addr, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Write([]byte("ok"))
		}), Options{})
	}()
	// Wait for the server to come up, prove it serves, then signal.
	deadline := time.Now().Add(3 * time.Second)
	for {
		resp, err := http.Get(fmt.Sprintf("http://%s/ok", addr))
		if err == nil {
			resp.Body.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("server never came up")
		}
		time.Sleep(20 * time.Millisecond)
	}
	syscall.Kill(syscall.Getpid(), syscall.SIGTERM)
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("shutdown returned error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("shutdown hung — graceful drain broken")
	}
}

func TestTimeoutsApplied(t *testing.T) {
	// A handler that sleeps past the write timeout must be cut off.
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := ln.Addr().String()
	ln.Close()
	go ListenAndServe(addr, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(2 * time.Second)
		w.Write([]byte("late"))
	}), Options{WriteTimeout: 100 * time.Millisecond})
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := http.Get("http://" + addr + "/healthz"); err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	resp, err := http.Get("http://" + addr + "/slow")
	if err == nil {
		body := make([]byte, 16)
		n, _ := resp.Body.Read(body)
		resp.Body.Close()
		if n > 0 {
			t.Fatalf("slow handler response not truncated by write timeout: %q", body[:n])
		}
	}
	syscall.Kill(syscall.Getpid(), syscall.SIGTERM)
}
