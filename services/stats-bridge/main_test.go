package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCountersAccumulateGaugesTakeLatest(t *testing.T) {
	reg := newRegistry()
	reg.record(point{Name: "tb_requests_total", Type: "counter", Help: "h", Labels: map[string]string{"op": "create_transfers"}, Value: 100})
	reg.record(point{Name: "tb_requests_total", Type: "counter", Help: "h", Labels: map[string]string{"op": "create_transfers"}, Value: 50})
	reg.record(point{Name: "tb_storage_bytes", Type: "gauge", Help: "h", Value: 900})
	reg.record(point{Name: "tb_storage_bytes", Type: "gauge", Help: "h", Value: 800})

	out := reg.render()
	if !strings.Contains(out, `tb_requests_total{op="create_transfers"} 150`) {
		t.Fatalf("counter must accumulate:\n%s", out)
	}
	if !strings.Contains(out, "tb_storage_bytes 800") {
		t.Fatalf("gauge must take latest:\n%s", out)
	}
	if !strings.Contains(out, "# TYPE tb_requests_total counter") {
		t.Fatalf("missing type header:\n%s", out)
	}
}

func TestTailPicksUpAppends(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "stats.jsonl")
	os.WriteFile(path, []byte(`{"name":"a_total","type":"counter","value":1}`+"\n"), 0o600)

	reg := newRegistry()
	stop := make(chan struct{})
	defer close(stop)
	go tail(path, reg, 0, 10*time.Millisecond, stop)

	deadline := time.Now().Add(2 * time.Second)
	for !strings.Contains(reg.render(), "a_total 1") && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(reg.render(), "a_total 1") {
		t.Fatalf("tail did not pick up initial line:\n%s", reg.render())
	}

	fh, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	fh.WriteString(`{"name":"a_total","type":"counter","value":2}` + "\n")
	fh.Close()
	deadline = time.Now().Add(2 * time.Second)
	for !strings.Contains(reg.render(), "a_total 3") && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !strings.Contains(reg.render(), "a_total 3") {
		t.Fatalf("tail did not pick up append:\n%s", reg.render())
	}
}
