// Package servex — production HTTP serving for every OpenHealth OS
// service, stdlib-only. One call replaces http.ListenAndServe and adds:
//
//   - read/write/idle timeouts (slow-loris resistance; the zero timeouts
//     of http.ListenAndServe are a standing DoS invitation)
//   - graceful shutdown on SIGINT/SIGTERM with a bounded drain, so a
//     rolling deploy never drops an in-flight claim or consent check
//   - a /metrics endpoint with per-service request counters, status
//     classes, and latency accumulators (Prometheus scrapes it; the
//     pilot-gates reporter consumes it)
//
// /metrics and /healthz bypass the wrapped handler chain's concerns for
// infrastructure traffic; NetworkPolicy restricts who can reach them.
//
// Tracing is opt-in via environment: OTEL_SPANS_FILE=/var/log/spans.jsonl
// (plus OTEL_SERVICE_NAME) wraps the handler in W3C traceparent
// propagation with JSON span export — no service code changes.
package servex

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/munisp/openhealthOS/shared/otelx"
)

// Metrics is the per-service request telemetry.
type Metrics struct {
	Requests   atomic.Int64
	Errors5xx  atomic.Int64
	Client4xx  atomic.Int64
	LatencySum atomic.Int64 // milliseconds
	Started    time.Time
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// Unwrap lets middleware further up the chain reach the underlying
// ResponseWriter (http.ResponseController support).
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

func (m *Metrics) observe(next http.Handler, path string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if path != "" && r.URL.Path == path {
			m.serve(w)
			return
		}
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		next.ServeHTTP(rec, r)
		m.Requests.Add(1)
		m.LatencySum.Add(time.Since(start).Milliseconds())
		switch {
		case rec.status >= 500:
			m.Errors5xx.Add(1)
		case rec.status >= 400:
			m.Client4xx.Add(1)
		}
	})
}

func (m *Metrics) serve(w http.ResponseWriter) {
	reqs := m.Requests.Load()
	var meanMs int64
	if reqs > 0 {
		meanMs = m.LatencySum.Load() / reqs
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"requests_total":   reqs,
		"errors_5xx_total": m.Errors5xx.Load(),
		"errors_4xx_total": m.Client4xx.Load(),
		"latency_ms_mean":  meanMs,
		"uptime_seconds":   int64(time.Since(m.Started).Seconds()),
	})
}

// Options tunes the server; zero values take the defaults.
type Options struct {
	// MetricsPath is where the legacy JSON metrics endpoint lives
	// (default /metrics). Services instrumented with otelx Prometheus
	// /metrics (ADR-0040) pass "/metrics-legacy" so the old JSON endpoint
	// survives without shadowing the Prometheus one.
	MetricsPath string
	ReadHeaderTimeout time.Duration // default 5s
	ReadTimeout       time.Duration // default 10s
	WriteTimeout      time.Duration // default 30s
	IdleTimeout       time.Duration // default 60s
	DrainTimeout      time.Duration // default 10s
}

func (o Options) withDefaults() Options {
	if o.ReadHeaderTimeout == 0 {
		o.ReadHeaderTimeout = 5 * time.Second
	}
	if o.ReadTimeout == 0 {
		o.ReadTimeout = 10 * time.Second
	}
	if o.WriteTimeout == 0 {
		o.WriteTimeout = 30 * time.Second
	}
	if o.IdleTimeout == 0 {
		o.IdleTimeout = 60 * time.Second
	}
	if o.DrainTimeout == 0 {
		o.DrainTimeout = 10 * time.Second
	}
	if o.MetricsPath == "" {
		o.MetricsPath = "/metrics"
	}
	return o
}

// InstrumentedListenAndServe is ListenAndServe plus the ADR-0040
// observability wiring: OTel traces, per-tenant RED metrics and a
// Prometheus /metrics endpoint; the legacy JSON metrics endpoint moves to
// /metrics-legacy. One line per service:
//
//	log.Fatal(servex.InstrumentedListenAndServe("my-service", os.Getenv, addr, handler))
func InstrumentedListenAndServe(service string, env func(string) string, addr string, handler http.Handler) error {
	exp, mx := otelx.Instrument(service, env)
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", mx.Handler())
	metricsMux.Handle("/", handler)
	handler = otelx.MiddlewareMetrics(service, exp, mx, metricsMux)
	return ListenAndServe(addr, handler, Options{MetricsPath: "/metrics-legacy"})
}

// ListenAndServe serves handler with timeouts, metrics and graceful
// shutdown. Returns nil on a clean signal-driven stop.
func ListenAndServe(addr string, handler http.Handler, opts Options) error {
	opts = opts.withDefaults()
	if spansFile := os.Getenv("OTEL_SPANS_FILE"); spansFile != "" {
		service := os.Getenv("OTEL_SERVICE_NAME")
		if service == "" {
			service = addr
		}
		exp, err := otelx.NewJSONLExporter(spansFile)
		if err != nil {
			return err
		}
		handler = otelx.Middleware(service, exp, handler)
		log.Printf("tracing: W3C traceparent, spans -> %s", spansFile)
	}
	m := &Metrics{Started: time.Now()}
	srv := &http.Server{
		Addr:              addr,
		Handler:           m.observe(handler, opts.MetricsPath),
		ReadHeaderTimeout: opts.ReadHeaderTimeout,
		ReadTimeout:       opts.ReadTimeout,
		WriteTimeout:      opts.WriteTimeout,
		IdleTimeout:       opts.IdleTimeout,
		BaseContext:       func(net.Listener) context.Context { return context.Background() },
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()

	select {
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-sig:
		ctx, cancel := context.WithTimeout(context.Background(), opts.DrainTimeout)
		defer cancel()
		if err := srv.Shutdown(ctx); err != nil {
			return err
		}
		return nil
	}
}
