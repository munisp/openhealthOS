package otelx

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestMetricsRenderPrometheus(t *testing.T) {
	mx := NewMetrics("svc")
	mx.CounterAdd("http_requests_total", "reqs", map[string]string{"tenant": "lagos", "status": "200"}, 2)
	mx.CounterAdd("http_requests_total", "reqs", map[string]string{"tenant": "lagos", "status": "200"}, 3)
	mx.CounterAdd("http_requests_total", "reqs", map[string]string{"tenant": "kano", "status": "500"}, 1)
	mx.GaugeSet("outbox_depth", "depth", map[string]string{"tenant": "lagos"}, 42.5)
	mx.ObserveMs("http_request_duration_ms", "dur", map[string]string{"tenant": "lagos"}, 12.0)
	mx.ObserveMs("http_request_duration_ms", "dur", map[string]string{"tenant": "lagos"}, 600.0)

	out := mx.Render()
	if !strings.Contains(out, "# TYPE http_requests_total counter") {
		t.Fatalf("missing counter header:\n%s", out)
	}
	if !strings.Contains(out, `http_requests_total{service="svc",status="200",tenant="lagos"} 5`) {
		t.Fatalf("counter not aggregated per tenant:\n%s", out)
	}
	if !strings.Contains(out, `http_requests_total{service="svc",status="500",tenant="kano"} 1`) {
		t.Fatalf("second tenant series missing:\n%s", out)
	}
	if !strings.Contains(out, `outbox_depth{service="svc",tenant="lagos"} 42.500`) {
		t.Fatalf("gauge missing:\n%s", out)
	}
	if !strings.Contains(out, `http_request_duration_ms_count{service="svc",tenant="lagos"} 2`) {
		t.Fatalf("histogram count missing:\n%s", out)
	}
	// both observations <=30000, only one <=25
	if !strings.Contains(out, `le="25"`) || !strings.Contains(out, `le="+Inf"`) {
		t.Fatalf("buckets missing:\n%s", out)
	}
}

func TestMiddlewareMetricsPerTenant(t *testing.T) {
	var got []Span
	exp := exportFunc(func(s Span) { got = append(got, s) })
	mx := NewMetrics("svc")
	h := MiddlewareMetrics("svc", exp, mx, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if TenantFrom(r.Context()) != "lagos" {
			t.Error("tenant not on context")
		}
		w.WriteHeader(201)
	}))
	req := httptest.NewRequest("GET", "/x", nil)
	req.Header.Set(TenantHeader, "lagos")
	h.ServeHTTP(httptest.NewRecorder(), req)

	if len(got) != 1 || got[0].Attrs[TenantAttr] != "lagos" {
		t.Fatalf("span missing tenant attr: %+v", got)
	}
	out := mx.Render()
	if !strings.Contains(out, `status="201"`) || !strings.Contains(out, `tenant="lagos"`) {
		t.Fatalf("metrics not labelled: %s", out)
	}
}

type exportFunc func(Span)

func (f exportFunc) Export(s Span) { f(s) }

func TestOTLPExporterBatchesAndBackpressures(t *testing.T) {
	var hits int64
	var lastBody []byte
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" {
			t.Errorf("wrong path %s", r.URL.Path)
		}
		body, _ := io.ReadAll(r.Body)
		lastBody = body
		atomic.AddInt64(&hits, 1)
		w.WriteHeader(200)
	}))
	defer collector.Close()

	env := func(k string) string {
		switch k {
		case "OTEL_EXPORTER_OTLP_ENDPOINT":
			return collector.URL
		case "OTEL_FLUSH_MS":
			return "20"
		}
		return ""
	}
	exp, err := NewOTLPExporter("svc", env)
	if err != nil {
		t.Fatal(err)
	}
	defer exp.Close()
	for i := 0; i < 3; i++ {
		exp.Export(Span{TraceID: strings.Repeat("a", 32), SpanID: "0123456789abcdef", Service: "svc", Name: "op", Start: time.Now(), End: time.Now(), Status: 200, Attrs: map[string]string{"tenant.id": "lagos"}})
	}
	deadline := time.Now().Add(2 * time.Second)
	for atomic.LoadInt64(&hits) == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if atomic.LoadInt64(&hits) == 0 {
		t.Fatal("collector never received a batch")
	}
	var payload map[string]any
	if err := json.Unmarshal(lastBody, &payload); err != nil {
		t.Fatalf("bad OTLP body: %v\n%s", err, lastBody)
	}
	rs := payload["resourceSpans"].([]any)
	spans := rs[0].(map[string]any)["scopeSpans"].([]any)[0].(map[string]any)["spans"].([]any)
	if len(spans) != 3 {
		t.Fatalf("expected 3 spans in one batch, got %d", len(spans))
	}

	// Backpressure: queue max 4096 default; overflow must drop, not block.
	for i := 0; i < 6000; i++ {
		exp.Export(Span{Name: "flood", Start: time.Now(), End: time.Now()})
	}
	// The 3-span batch already flushed; of the 6000 flood spans at most
	// 4096 survive, the rest counted as dropped (some may have flushed).
	exp.Close()
	// Reaching here without deadlock is the assertion: telemetry shed load.
}

func TestInstrumentFromEnv(t *testing.T) {
	exp, mx := Instrument("svc", func(k string) string { return "" })
	if mx == nil || exp == nil {
		t.Fatal("Instrument must never return nils")
	}
	exp.Export(Span{Name: "x", Start: time.Now(), End: time.Now()}) // devnull must not panic

	rec := httptest.NewRecorder()
	mx.Handler().ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if rec.Header().Get("Content-Type") == "" {
		t.Fatal("metrics handler content-type missing")
	}
}

func TestInjectTenantHeader(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	InjectTenant(req)
	if req.Header.Get(TenantHeader) != "" {
		t.Fatal("must not fabricate a tenant")
	}
	req = req.WithContext(WithTenant(req.Context(), "kaduna"))
	InjectTenant(req)
	if req.Header.Get(TenantHeader) != "kaduna" {
		t.Fatal("tenant not propagated")
	}
}
