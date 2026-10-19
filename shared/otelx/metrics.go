// metrics.go — Prometheus text-format metrics, stdlib-only.
//
// Every service exposes /metrics; Prometheus (or the OTel Collector's
// prometheus receiver) scrapes it. Labels always include `service`; the
// HTTP middleware adds `tenant` when the platform runs multi-tenant
// (ADR-0040) so every counter can be sliced per tenant.
package otelx

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
)

// Metrics is a per-process registry of counters, gauges and duration
// histograms. All series carry the service name; callers add labels
// (e.g. tenant, route, status) per observation.
type Metrics struct {
	service    string
	mu         sync.Mutex
	counters   map[string]*counterVec
	gauges     map[string]*gaugeVec
	histograms map[string]*histVec
}

// NewMetrics builds a registry for one service.
func NewMetrics(service string) *Metrics {
	return &Metrics{
		service:    service,
		counters:   map[string]*counterVec{},
		gauges:     map[string]*gaugeVec{},
		histograms: map[string]*histVec{},
	}
}

func labelKey(labels map[string]string) string {
	if len(labels) == 0 {
		return ""
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(k)
		b.WriteByte('=')
		b.WriteString(labels[k])
	}
	return b.String()
}

func renderLabels(service string, key string) string {
	if key == "" {
		return fmt.Sprintf("{service=%q}", service)
	}
	parts := strings.Split(key, ",")
	var b strings.Builder
	b.WriteString("{service=")
	b.WriteString(quoteLabel(service))
	for _, p := range parts {
		kv := strings.SplitN(p, "=", 2)
		b.WriteString(",")
		b.WriteString(kv[0])
		b.WriteString("=")
		b.WriteString(quoteLabel(kv[1]))
	}
	b.WriteString("}")
	return b.String()
}

// quoteLabel escapes a label value for the exposition format.
func quoteLabel(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}


type counterVec struct {
	help string
	// series labelKey -> value
	series sync.Map // string -> *int64
}

func (m *Metrics) counter(name, help string) *counterVec {
	m.mu.Lock()
	defer m.mu.Unlock()
	cv, ok := m.counters[name]
	if !ok {
		cv = &counterVec{help: help}
		m.counters[name] = cv
	}
	return cv
}

// CounterAdd increments counter `name` for the given label set.
func (m *Metrics) CounterAdd(name, help string, labels map[string]string, delta int64) {
	cv := m.counter(name, help)
	key := labelKey(labels)
	if v, ok := cv.series.Load(key); ok {
		atomic.AddInt64(v.(*int64), delta)
		return
	}
	nv := new(int64)
	atomic.StoreInt64(nv, delta)
	actual, _ := cv.series.LoadOrStore(key, nv)
	if actual != nv {
		atomic.AddInt64(actual.(*int64), delta)
	}
}

type gaugeVec struct {
	help   string
	series sync.Map // string -> *float64ish (atomic via mutex-free CAS is overkill; store *gaugeCell)
}

type gaugeCell struct{ v atomic.Int64 } // fixed-point x1000

func (m *Metrics) gauge(name, help string) *gaugeVec {
	m.mu.Lock()
	defer m.mu.Unlock()
	gv, ok := m.gauges[name]
	if !ok {
		gv = &gaugeVec{help: help}
		m.gauges[name] = gv
	}
	return gv
}

// GaugeSet sets gauge `name` for the label set (milli precision).
func (m *Metrics) GaugeSet(name, help string, labels map[string]string, value float64) {
	gv := m.gauge(name, help)
	key := labelKey(labels)
	cell, _ := gv.series.LoadOrStore(key, &gaugeCell{})
	cell.(*gaugeCell).v.Store(int64(value * 1000))
}

// Duration histogram buckets (ms): edges chosen for a health API —
// sub-10ms cache hits through 30s sync-document round trips.
var DurationBuckets = []float64{5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000}

type histVec struct {
	help   string
	series sync.Map // string -> *histCell
}

type histCell struct {
	count int64
	sumMs int64 // fixed-point x1000
	bkts  []int64
	mu    sync.Mutex
}

func (c *histCell) observe(ms float64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.count++
	c.sumMs += int64(ms * 1000)
	for i, edge := range DurationBuckets {
		if ms <= edge {
			c.bkts[i]++
		}
	}
}

func (m *Metrics) histogram(name, help string) *histVec {
	m.mu.Lock()
	defer m.mu.Unlock()
	hv, ok := m.histograms[name]
	if !ok {
		hv = &histVec{help: help}
		m.histograms[name] = hv
	}
	return hv
}

// ObserveMs records a duration in milliseconds.
func (m *Metrics) ObserveMs(name, help string, labels map[string]string, ms float64) {
	hv := m.histogram(name, help)
	key := labelKey(labels)
	cell, _ := hv.series.LoadOrStore(key, &histCell{bkts: make([]int64, len(DurationBuckets))})
	cell.(*histCell).observe(ms)
}

// Handler renders the registry in Prometheus text exposition format.
func (m *Metrics) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		w.Write([]byte(m.Render()))
	})
}

// Render produces the full exposition body (used by the handler and tests).
func (m *Metrics) Render() string {
	var b strings.Builder

	names := make([]string, 0, len(m.counters))
	for n := range m.counters {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		cv := m.counters[n]
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s counter\n", n, cv.help, n)
		cv.series.Range(func(k, v any) bool {
			fmt.Fprintf(&b, "%s%s %d\n", n, renderLabels(m.service, k.(string)), atomic.LoadInt64(v.(*int64)))
			return true
		})
	}

	gnames := make([]string, 0, len(m.gauges))
	for n := range m.gauges {
		gnames = append(gnames, n)
	}
	sort.Strings(gnames)
	for _, n := range gnames {
		gv := m.gauges[n]
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s gauge\n", n, gv.help, n)
		gv.series.Range(func(k, v any) bool {
			fmt.Fprintf(&b, "%s%s %.3f\n", n, renderLabels(m.service, k.(string)), float64(v.(*gaugeCell).v.Load())/1000)
			return true
		})
	}

	hnames := make([]string, 0, len(m.histograms))
	for n := range m.histograms {
		hnames = append(hnames, n)
	}
	sort.Strings(hnames)
	for _, n := range hnames {
		hv := m.histograms[n]
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s histogram\n", n, hv.help, n)
		hv.series.Range(func(k, v any) bool {
			c := v.(*histCell)
			c.mu.Lock()
			defer c.mu.Unlock()
			lbl := k.(string)
			for i, edge := range DurationBuckets {
				fmt.Fprintf(&b, "%s_bucket%s %d\n", n, renderLabels(m.service, joinLe(lbl, edge)), c.bkts[i])
			}
			fmt.Fprintf(&b, "%s_bucket%s %d\n", n, renderLabels(m.service, joinLeInf(lbl)), c.count)
			fmt.Fprintf(&b, "%s_sum%s %.3f\n", n, renderLabels(m.service, lbl), float64(c.sumMs)/1000)
			fmt.Fprintf(&b, "%s_count%s %d\n", n, renderLabels(m.service, lbl), c.count)
			return true
		})
	}
	return b.String()
}

func joinLe(key string, edge float64) string {
	le := fmt.Sprintf("%g", edge)
	if key == "" {
		return "le=" + le
	}
	return key + ",le=" + le
}

func joinLeInf(key string) string {
	if key == "" {
		return "le=+Inf"
	}
	return key + ",le=+Inf"
}
