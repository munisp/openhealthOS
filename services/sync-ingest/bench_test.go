package main

// Throughput benchmarks for the edge-ingest hot path (Workstream B).
//
// Run:
//
//	go test -bench=. -benchtime=2s
//
// The platform target is set per hop, not as a marketing aggregate: this
// single stdlib process must clear 100k events/sec on commodity hardware so
// that a modest replica set (and the Kafka/Fluvio fan-out behind it) rolls
// up to millions of platform events per second.

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"testing"
)

func benchBatch(b *testing.B, eventsPerBatch int) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	for i := 0; i < eventsPerBatch; i++ {
		fmt.Fprintf(gz, `{"id":"ev-%d","facility":"F-001","type":"encounter","ts":"2026-10-20T00:00:00Z","payload":{"code":"hr","value":72}}`, i)
		fmt.Fprintln(gz)
	}
	gz.Close()
	raw := buf.Bytes()

	store := NewMemoryStore()
	b.ResetTimer()
	b.ReportAllocs()
	b.SetBytes(int64(eventsPerBatch))
	for i := 0; i < b.N; i++ {
		// Fresh IDs per iteration so dedupe doesn't turn the bench into a no-op.
		st := &memNoDedupe{inner: store}
		if _, err := IngestBatch(bytes.NewReader(raw), st); err != nil {
			b.Fatal(err)
		}
	}
	b.ReportMetric(float64(b.N*eventsPerBatch)/b.Elapsed().Seconds(), "events/s")
}

// memNoDedupe strips the dedupe map (kept hot in production for idempotency)
// so the benchmark measures parse+apply throughput, not map-hit speed.
type memNoDedupe struct{ inner *MemoryStore }

func (m *memNoDedupe) Seen(id string) bool      { return false }
func (m *memNoDedupe) Apply(ev Event) bool      { return m.inner.Apply(ev) }
func (m *memNoDedupe) Stats() (int, int, int)   { return m.inner.Stats() }
func (m *memNoDedupe) Events() []Event          { return m.inner.Events() }

func BenchmarkIngestBatch100(b *testing.B)   { benchBatch(b, 100) }
func BenchmarkIngestBatch1000(b *testing.B)  { benchBatch(b, 1000) }
func BenchmarkIngestBatch10000(b *testing.B) { benchBatch(b, 10000) }
