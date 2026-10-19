// stats-bridge — Prometheus /metrics for components that cannot serve HTTP.
//
// TigerBeetle (no HTTP endpoint by design), embedded engines, and batch
// jobs all need metrics in the observability plane (ADR-0040). The contract
// is one file: the component (or its sidecar script) appends JSON lines
//
//	{"name":"tigerbeetle_requests_total","help":"...","type":"counter",
//	 "labels":{"op":"create_transfers"},"value":8189}
//
// to STATS_PATH (default /var/openhealth/stats.jsonl). stats-bridge tails
// it, keeps the latest value per series in memory, and renders Prometheus
// text format. Stdlib-only, ~no dependencies: this is a hot-path-adjacent
// service, it must never be the thing that falls over.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

type point struct {
	Name   string            `json:"name"`
	Help   string            `json:"help"`
	Type   string            `json:"type"` // counter | gauge
	Labels map[string]string `json:"labels"`
	Value  float64           `json:"value"`
}

type seriesKey struct{ name, labels string }

type registry struct {
	mu     sync.RWMutex
	meta   map[string]point            // name -> help/type
	series map[seriesKey]float64
}

func newRegistry() *registry {
	return &registry{meta: map[string]point{}, series: map[seriesKey]float64{}}
}

func labelKey(l map[string]string) string {
	if len(l) == 0 {
		return ""
	}
	keys := make([]string, 0, len(l))
	for k := range l {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "%s=%q", k, l[k])
	}
	return b.String()
}

func (r *registry) record(p point) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.meta[p.Name] = p
	k := seriesKey{p.Name, labelKey(p.Labels)}
	if p.Type == "counter" {
		r.series[k] += p.Value // counters accumulate
	} else {
		r.series[k] = p.Value // gauges take latest
	}
}

func (r *registry) render() string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	names := make([]string, 0, len(r.meta))
	for n := range r.meta {
		names = append(names, n)
	}
	sort.Strings(names)
	var b strings.Builder
	for _, n := range names {
		m := r.meta[n]
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s %s\n", n, m.Help, n, m.Type)
		for k, v := range r.series {
			if k.name != n {
				continue
			}
			if k.labels == "" {
				fmt.Fprintf(&b, "%s %g\n", n, v)
			} else {
				fmt.Fprintf(&b, "%s{%s} %g\n", n, k.labels, v)
			}
		}
	}
	return b.String()
}

// tail follows path, recording every complete line; partial/last-line
// growth (writes in flight) is picked up on the next poll. Missing file is
// not an error: the component may not have produced stats yet.
func tail(path string, reg *registry, offset int64, poll time.Duration, stop <-chan struct{}) int64 {
	for {
		select {
		case <-stop:
			return offset
		default:
		}
		fh, err := os.Open(path)
		if err != nil {
			time.Sleep(poll)
			continue
		}
		st, err := fh.Stat()
		if err == nil && st.Size() < offset { // rotated/truncated: restart
			offset = 0
		}
		fh.Seek(offset, 0)
		sc := bufio.NewScanner(fh)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		for sc.Scan() {
			line := sc.Bytes()
			if len(line) == 0 || line[len(line)-1] == '\r' {
				continue
			}
			var p point
			if json.Unmarshal(line, &p) == nil && p.Name != "" {
				reg.record(p)
			}
		}
		pos, _ := fh.Seek(0, 1)
		offset = pos
		fh.Close()
		select {
		case <-stop:
			return offset
		case <-time.After(poll):
		}
	}
}

func main() {
	path := os.Getenv("STATS_PATH")
	if path == "" {
		path = "/var/openhealth/stats.jsonl"
	}
	addr := os.Getenv("LISTEN")
	if addr == "" {
		addr = ":9100"
	}
	reg := newRegistry()
	stop := make(chan struct{})
	go tail(path, reg, 0, 2*time.Second, stop)

	mux := http.NewServeMux()
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprint(w, reg.render())
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	log.Printf("stats-bridge on %s tailing %s", addr, path)
	log.Fatal(http.ListenAndServe(addr, mux))
}
