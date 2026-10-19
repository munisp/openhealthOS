// otlp.go — OTLP/HTTP span export with bounded batching (stdlib-only).
//
// Spans are buffered and pushed to an OpenTelemetry Collector at
// {endpoint}/v1/traces as OTLP/JSON. The queue is bounded and drop-oldest:
// telemetry must never apply backpressure to clinical traffic — a saturated
// collector degrades observability, not care delivery.
package otelx

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// OTLPExporter batches spans to an OTel Collector over HTTP.
type OTLPExporter struct {
	endpoint string // base URL, e.g. http://otel-collector:4318
	service  string
	client   *http.Client

	mu       sync.Mutex
	queue    []Span
	dropped  int64
	maxQueue int
	flush    time.Duration
	stop     chan struct{}
	done     chan struct{}
	stopOnce sync.Once
}

// NewOTLPExporter starts an async exporter. Environment:
//
//	OTEL_EXPORTER_OTLP_ENDPOINT  collector base URL (required)
//	OTEL_QUEUE_MAX               max buffered spans (default 4096)
//	OTEL_FLUSH_MS                flush interval (default 2000)
//	OTEL_TIMEOUT_MS              per-push timeout (default 5000)
func NewOTLPExporter(service string, env func(string) string) (*OTLPExporter, error) {
	ep := strings.TrimRight(env("OTEL_EXPORTER_OTLP_ENDPOINT"), "/")
	if ep == "" {
		return nil, fmt.Errorf("OTEL_EXPORTER_OTLP_ENDPOINT not set")
	}
	e := &OTLPExporter{
		endpoint: ep,
		service:  service,
		client:   &http.Client{Timeout: envMS(env, "OTEL_TIMEOUT_MS", 5000)},
		maxQueue: envInt(env, "OTEL_QUEUE_MAX", 4096),
		flush:    envMS(env, "OTEL_FLUSH_MS", 2000),
		stop:     make(chan struct{}),
		done:     make(chan struct{}),
	}
	go e.loop()
	return e, nil
}

func envInt(env func(string) string, key string, def int) int {
	if v := env(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}

func envMS(env func(string) string, key string, defMs int) time.Duration {
	return time.Duration(envInt(env, key, defMs)) * time.Millisecond
}

// Export buffers a span; drop-oldest when the queue is full.
func (e *OTLPExporter) Export(s Span) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if len(e.queue) >= e.maxQueue {
		copy(e.queue, e.queue[1:])
		e.queue[len(e.queue)-1] = s
		e.dropped++
		return
	}
	e.queue = append(e.queue, s)
}

// Dropped reports how many spans were shed under saturation (exported as
// the otelx_dropped_spans_total counter by Instrument).
func (e *OTLPExporter) Dropped() int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.dropped
}

func (e *OTLPExporter) loop() {
	t := time.NewTicker(e.flush)
	defer t.Stop()
	defer close(e.done)
	for {
		select {
		case <-t.C:
			e.push()
		case <-e.stop:
			e.push()
			return
		}
	}
}

// Close flushes once and stops the exporter. Safe to call twice.
func (e *OTLPExporter) Close() error {
	e.stopOnce.Do(func() { close(e.stop) })
	<-e.done
	return nil
}

func (e *OTLPExporter) push() {
	e.mu.Lock()
	if len(e.queue) == 0 {
		e.mu.Unlock()
		return
	}
	batch := e.queue
	e.queue = nil
	e.mu.Unlock()

	body := marshalTraces(e.service, batch)
	req, err := http.NewRequest(http.MethodPost, e.endpoint+"/v1/traces", bytes.NewReader(body))
	if err != nil {
		e.requeue(batch)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := e.client.Do(req)
	if err != nil {
		e.requeue(batch)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		e.requeue(batch)
	}
}

// requeue returns a failed batch to the front of the queue, still bounded.
func (e *OTLPExporter) requeue(batch []Span) {
	e.mu.Lock()
	defer e.mu.Unlock()
	room := e.maxQueue - len(e.queue)
	if room < len(batch) {
		e.dropped += int64(len(batch) - room)
		batch = batch[len(batch)-room:]
		if room <= 0 {
			return
		}
	}
	e.queue = append(batch, e.queue...)
	if len(e.queue) > e.maxQueue {
		e.dropped += int64(len(e.queue) - e.maxQueue)
		e.queue = e.queue[len(e.queue)-e.maxQueue:]
	}
}

// marshalTraces renders the OTLP/JSON ExportTraceServiceRequest shape.
// Attribute values are all strings in our Span model, which keeps the
// encoder honest and small.
func marshalTraces(service string, spans []Span) []byte {
	otlpSpans := make([]map[string]any, 0, len(spans))
	for _, s := range spans {
		attrs := []map[string]any{}
		for k, v := range s.Attrs {
			attrs = append(attrs, map[string]any{
				"key":   k,
				"value": map[string]any{"stringValue": v},
			})
		}
		attrs = append(attrs,
			map[string]any{"key": "http.status_code", "value": map[string]any{"intValue": fmt.Sprintf("%d", s.Status)}},
		)
		code := 1 // STATUS_CODE_OK
		if s.Status >= 500 {
			code = 2 // STATUS_CODE_ERROR
		}
		otlpSpans = append(otlpSpans, map[string]any{
			"traceId":           s.TraceID,
			"spanId":            s.SpanID,
			"parentSpanId":      s.ParentID,
			"name":              s.Name,
			"kind":              2, // SPAN_KIND_SERVER
			"startTimeUnixNano": fmt.Sprintf("%d", s.Start.UnixNano()),
			"endTimeUnixNano":   fmt.Sprintf("%d", s.End.UnixNano()),
			"attributes":        attrs,
			"status":            map[string]any{"code": code},
		})
	}
	payload := map[string]any{
		"resourceSpans": []map[string]any{{
			"resource": map[string]any{
				"attributes": []map[string]any{{
					"key":   "service.name",
					"value": map[string]any{"stringValue": service},
				}},
			},
			"scopeSpans": []map[string]any{{
				"scope": map[string]any{"name": "otelx"},
				"spans": otlpSpans,
			}},
		}},
	}
	raw, _ := json.Marshal(payload)
	return raw
}

// ---------- fan-out ----------

// MultiExporter fans a span out to several exporters (e.g. JSONL audit
// file + OTLP collector). A nil entry is skipped.
type MultiExporter []Exporter

// Export implements Exporter.
func (m MultiExporter) Export(s Span) {
	for _, e := range m {
		if e != nil {
			e.Export(s)
		}
	}
}

// ---------- one-call wiring ----------

// Instrument is the single entry point every service calls:
//
//	exp, mx := otelx.Instrument("notification-service", os.Getenv)
//	mux.Handle("/metrics", mx.Handler())
//	handler := otelx.Middleware("notification-service", exp, mux)
//
// OTEL_EXPORTER_OTLP_ENDPOINT set  → OTLP push exporter.
// OTEL_SPANS_PATH set              → JSONL file exporter (audit-grade).
// Both set                         → fan-out to both.
// Neither                          → in-memory devnull exporter (tests/dev).
func Instrument(service string, env func(string) string) (Exporter, *Metrics) {
	mx := NewMetrics(service)
	var exp Exporter = devnull{}
	var multi MultiExporter
	if ep := env("OTEL_EXPORTER_OTLP_ENDPOINT"); ep != "" {
		if oe, err := NewOTLPExporter(service, env); err == nil {
			multi = append(multi, oe)
		}
	}
	if p := env("OTEL_SPANS_PATH"); p != "" {
		if je, err := NewJSONLExporter(p); err == nil {
			multi = append(multi, je)
		}
	}
	if len(multi) == 1 {
		exp = multi[0]
	} else if len(multi) > 1 {
		exp = multi
	}
	return exp, mx
}

type devnull struct{}

func (devnull) Export(Span) {}

