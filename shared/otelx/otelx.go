// Package otelx — OpenTelemetry-compatible tracing, stdlib-only.
//
// The OTel wire conventions are an open specification, not a licensed
// product (ADR-0005): we implement W3C traceparent propagation and JSON span
// export directly. A production deployment can point an OTel Collector at
// the same spans file (or swap this package for the OTel SDK behind the same
// two functions) without touching service code.
package otelx

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// Span is one timed unit of work in a trace.
type Span struct {
	TraceID  string            `json:"trace_id"`
	SpanID   string            `json:"span_id"`
	ParentID string            `json:"parent_id,omitempty"`
	Service  string            `json:"service"`
	Name     string            `json:"name"`
	Start    time.Time         `json:"start"`
	End      time.Time         `json:"end"`
	DurMs    float64           `json:"dur_ms"`
	Status   int               `json:"status"` // http status for server spans
	Attrs    map[string]string `json:"attrs,omitempty"`
}

// Exporter receives completed spans.
type Exporter interface{ Export(Span) }

// JSONLExporter appends spans as JSON lines (collector-scrapeable).
type JSONLExporter struct {
	mu sync.Mutex
	w  *os.File
}

// NewJSONLExporter opens path for append (fsync per span: spans are
// cheap, losing them silently is not).
func NewJSONLExporter(path string) (*JSONLExporter, error) {
	fh, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	return &JSONLExporter{w: fh}, nil
}

func (e *JSONLExporter) Export(s Span) {
	raw, err := json.Marshal(s)
	if err != nil {
		return
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	e.w.Write(append(raw, '\n'))
	e.w.Sync()
}

// Close flushes and closes the underlying file.
func (e *JSONLExporter) Close() error { return e.w.Close() }

// ---------- W3C traceparent ----------

// TraceContext is the propagated identity of a request.
type TraceContext struct {
	TraceID  string
	ParentID string
	Sampled  bool
}

func randHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// ParseTraceparent parses "00-<32hex>-<16hex>-<flags>". Malformed headers
// are ignored (start a fresh trace) — propagation must never break traffic.
func ParseTraceparent(h string) (TraceContext, bool) {
	var tc TraceContext
	parts := strings.Split(h, "-")
	if len(parts) != 4 {
		return tc, false
	}
	tid, sid, fl := parts[1], parts[2], parts[3]
	if len(tid) != 32 || len(sid) != 16 || len(fl) != 2 {
		return tc, false
	}
	if tid == "00000000000000000000000000000000" || sid == "0000000000000000" {
		return tc, false
	}
	for _, c := range tid + sid + fl {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return tc, false
		}
	}
	return TraceContext{TraceID: tid, ParentID: sid, Sampled: fl == "01"}, true
}

// Header renders a traceparent for outbound calls (child span of ctx).
func (tc TraceContext) Header(spanID string) string {
	sampled := "00"
	if tc.Sampled {
		sampled = "01"
	}
	return fmt.Sprintf("00-%s-%s-%s", tc.TraceID, spanID, sampled)
}

type ctxKey struct{}

// WithTrace returns a context carrying tc.
func WithTrace(ctx context.Context, tc TraceContext) context.Context {
	return context.WithValue(ctx, ctxKey{}, tc)
}

// TraceFrom extracts the trace context (zero value if absent).
func TraceFrom(ctx context.Context) TraceContext {
	tc, _ := ctx.Value(ctxKey{}).(TraceContext)
	return tc
}

// InjectOutbound sets traceparent on an outgoing request so the next hop
// continues the same trace.
func InjectOutbound(req *http.Request) {
	tc := TraceFrom(req.Context())
	if tc.TraceID == "" {
		return
	}
	req.Header.Set("traceparent", tc.Header(randHex(8)))
}

// ---------- middleware ----------

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// Middleware traces every request: continues an incoming trace if present,
// exports a completed span with method/route/status/duration. The tenant
// (ADR-0040) is resolved from the propagation header and stamped on the
// span and the request context.
func Middleware(service string, exp Exporter, next http.Handler) http.Handler {
	return middleware(service, exp, nil, next)
}

// MiddlewareMetrics is Middleware plus RED metrics (rate/errors/duration)
// into mx, labelled by tenant so every series slices per tenant.
func MiddlewareMetrics(service string, exp Exporter, mx *Metrics, next http.Handler) http.Handler {
	return middleware(service, exp, mx, next)
}

func middleware(service string, exp Exporter, mx *Metrics, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		tc, ok := ParseTraceparent(r.Header.Get("traceparent"))
		if !ok {
			tc = TraceContext{TraceID: randHex(16), Sampled: true}
		}
		spanID := randHex(8)
		ctx := WithTrace(r.Context(), tc)
		tenant := r.Header.Get(TenantHeader)
		if tenant != "" {
			ctx = WithTenant(ctx, tenant)
		}
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		next.ServeHTTP(rec, r.WithContext(ctx))
		dur := float64(time.Since(start).Microseconds()) / 1000.0
		attrs := map[string]string{}
		if tenant != "" {
			attrs[TenantAttr] = tenant
		}
		exp.Export(Span{
			TraceID: tc.TraceID, SpanID: spanID, ParentID: tc.ParentID,
			Service: service, Name: r.Method + " " + r.URL.Path,
			Start: start, End: time.Now(),
			DurMs:  dur, Status: rec.status, Attrs: attrs,
		})
		if mx != nil {
			labels := map[string]string{"tenant": tenant, "status": fmt.Sprintf("%d", rec.status)}
			mx.CounterAdd("http_requests_total", "HTTP requests by tenant and status", labels, 1)
			mx.ObserveMs("http_request_duration_ms", "HTTP request duration by tenant", map[string]string{"tenant": tenant}, dur)
		}
	})
}
