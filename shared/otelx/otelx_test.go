package otelx

import (
	"bufio"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseTraceparentValid(t *testing.T) {
	tc, ok := ParseTraceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
	if !ok {
		t.Fatal("valid traceparent rejected")
	}
	if tc.TraceID != "4bf92f3577b34da6a3ce929d0e0e4736" || tc.ParentID != "00f067aa0ba902b7" || !tc.Sampled {
		t.Fatalf("misparsed: %+v", tc)
	}
}

func TestParseTraceparentRejectsGarbage(t *testing.T) {
	for _, h := range []string{
		"", "garbage",
		"00-00000000000000000000000000000000-00f067aa0ba902b7-01", // all-zero trace id forbidden
		"00-4bf92f3577b34da6a3ce929d0e0e4736",                     // missing parts
		"00-XYZ92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", // non-hex
		"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",    // no flags
	} {
		if _, ok := ParseTraceparent(h); ok {
			t.Fatalf("accepted malformed header %q", h)
		}
	}
}

type memExporter struct{ spans []Span }

func (m *memExporter) Export(s Span) { m.spans = append(m.spans, s) }

func TestMiddlewareContinuesTraceAndRecordsStatus(t *testing.T) {
	exp := &memExporter{}
	h := Middleware("claims", exp, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tc := TraceFrom(r.Context())
		if tc.TraceID != "4bf92f3577b34da6a3ce929d0e0e4736" {
			t.Error("incoming trace not continued in context")
		}
		w.WriteHeader(http.StatusTeapot)
	}))
	req := httptest.NewRequest("GET", "/claims", nil)
	req.Header.Set("traceparent", "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
	h.ServeHTTP(httptest.NewRecorder(), req)

	if len(exp.spans) != 1 {
		t.Fatalf("span not exported: %v", exp.spans)
	}
	s := exp.spans[0]
	if s.TraceID != "4bf92f3577b34da6a3ce929d0e0e4736" || s.ParentID != "00f067aa0ba902b7" {
		t.Fatalf("span lineage wrong: %+v", s)
	}
	if s.Status != http.StatusTeapot || s.Service != "claims" || s.Name != "GET /claims" {
		t.Fatalf("span fields wrong: %+v", s)
	}
}

func TestMiddlewareStartsFreshTraceOnMissingHeader(t *testing.T) {
	exp := &memExporter{}
	h := Middleware("registry", exp, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/healthz", nil))
	if len(exp.spans) != 1 || len(exp.spans[0].TraceID) != 32 || exp.spans[0].ParentID != "" {
		t.Fatalf("fresh trace broken: %+v", exp.spans)
	}
}

func TestInjectOutboundPropagates(t *testing.T) {
	tc := TraceContext{TraceID: "4bf92f3577b34da6a3ce929d0e0e4736", Sampled: true}
	req, _ := http.NewRequest("POST", "http://upstream/x", nil)
	req = req.WithContext(WithTrace(req.Context(), tc))
	InjectOutbound(req)
	got, ok := ParseTraceparent(req.Header.Get("traceparent"))
	if !ok || got.TraceID != tc.TraceID {
		t.Fatalf("outbound propagation broken: %q", req.Header.Get("traceparent"))
	}
	// no trace in context: no header
	req2, _ := http.NewRequest("GET", "http://upstream/y", nil)
	InjectOutbound(req2)
	if req2.Header.Get("traceparent") != "" {
		t.Fatal("header injected without trace context")
	}
}

func TestJSONLExporterWritesParseableSpans(t *testing.T) {
	path := filepath.Join(t.TempDir(), "spans.jsonl")
	exp, err := NewJSONLExporter(path)
	if err != nil {
		t.Fatal(err)
	}
	exp.Export(Span{TraceID: strings.Repeat("a", 32), SpanID: "1", Service: "x", Name: "GET /"})
	exp.Close()

	fh, _ := os.Open(path)
	defer fh.Close()
	sc := bufio.NewScanner(fh)
	n := 0
	for sc.Scan() {
		var s Span
		if json.Unmarshal(sc.Bytes(), &s) != nil {
			t.Fatal("exported span not valid JSON")
		}
		n++
	}
	if n != 1 {
		t.Fatalf("expected 1 span line, got %d", n)
	}
}
