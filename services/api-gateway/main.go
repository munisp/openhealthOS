// Package main — OpenHealth OS API gateway: the single public edge of the
// platform (technical spec L4/L5). Every external call enters here; the
// gateway owns routing, rate limits, OIDC verification (shared/authz), and
// claim-to-header mapping (the OIDC facility_id claim becomes the
// X-Facility-ID header the SHR consent gate enforces).
//
// This is the stdlib reference for the blueprint's APISIX role (ADR-0005):
// httputil.ReverseProxy + explicit route table. A production swap to APISIX
// keeps the same contract: path prefixes below and the X-Facility-ID
// mapping are the interface, not the implementation.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/cachex"
	"github.com/munisp/openhealthOS/shared/otelx"
	"github.com/munisp/openhealthOS/shared/servex"
	"github.com/munisp/openhealthOS/shared/wafx"
)

// Route maps one public prefix to one upstream service.
type Route struct {
	Prefix   string // public path prefix, e.g. "/api/claims"
	ToPrefix string // when set, rewrite Prefix → ToPrefix (e.g. /api/fhir → /fhir)
	Service  string // logical name (health matrix, spans)
	Upstream string // base URL, e.g. http://claims-orchestrator:8080
	Strip    bool   // strip the public prefix before forwarding
}

// DefaultRoutes is the platform's public surface. Order matters: first
// match wins (longest prefixes first).
func DefaultRoutes(env func(string) string) []Route {
	up := func(key, def string) string {
		if v := env(key); v != "" {
			return v
		}
		return def
	}
	return []Route{
		{Prefix: "/api/claims", Service: "claims-orchestrator", Upstream: up("CLAIMS_URL", "http://claims-orchestrator:8080"), Strip: true},
		{Prefix: "/api/preauth", Service: "claims-orchestrator", Upstream: up("CLAIMS_URL", "http://claims-orchestrator:8080"), Strip: true},
		{Prefix: "/api/registry", Service: "registry-service", Upstream: up("REGISTRY_URL", "http://registry-service:8081"), Strip: true},
		{Prefix: "/api/consents", Service: "consent-service", Upstream: up("CONSENT_URL", "http://consent-service:8082"), Strip: true},
		{Prefix: "/api/ussd", Service: "ussd-gateway", Upstream: up("USSD_URL", "http://ussd-gateway:8083"), Strip: true},
		{Prefix: "/api/wallet", Service: "wallet-service", Upstream: up("WALLET_URL", "http://wallet-service:8084"), Strip: true},
		{Prefix: "/api/shr", Service: "shr-service", Upstream: up("SHR_URL", "http://shr-service:8085"), Strip: true},
		// Medplum rest-hook intake: external FHIR servers POST resources
		// here; must precede the /api/fhir catch-all (first match wins).
		{Prefix: "/api/fhir/inbox", Service: "notification-service", Upstream: up("NOTIFY_URL", "http://notification-service:8089"), ToPrefix: "/fhir/inbox"},
		{Prefix: "/api/fhir", Service: "shr-service", Upstream: up("SHR_URL", "http://shr-service:8085"), ToPrefix: "/fhir"},
		{Prefix: "/api/equipment", Service: "equipment-service", Upstream: up("EQUIPMENT_URL", "http://equipment-service:8086"), Strip: true},
		{Prefix: "/api/telehealth", Service: "telehealth-service", Upstream: up("TELEHEALTH_URL", "http://telehealth-service:8087"), Strip: true},
		{Prefix: "/api/pharmacy", Service: "pharmacy-service", Upstream: up("PHARMACY_URL", "http://pharmacy-service:8088"), Strip: true},
		{Prefix: "/api/notify", Service: "notification-service", Upstream: up("NOTIFY_URL", "http://notification-service:8089"), Strip: true},
		{Prefix: "/api/wellness", Service: "wellness-service", Upstream: up("WELLNESS_URL", "http://wellness-service:8090"), Strip: true},
		// Single public entry (ADR-0036): every platform service is
		// reachable through the gateway; per-service ports are a
		// development convenience, never the public contract.
		{Prefix: "/api/scheduling", Service: "scheduling-service", Upstream: up("SCHEDULING_URL", "http://scheduling-service:8092"), Strip: true},
		{Prefix: "/api/dispatch", Service: "dispatch-service", Upstream: up("DISPATCH_URL", "http://dispatch-service:8093"), Strip: true},
		{Prefix: "/api/telemed", Service: "telemed-service", Upstream: up("TELEMED_URL", "http://telemed-service:8094"), Strip: true},
		{Prefix: "/api/media", Service: "media-service", Upstream: up("MEDIA_URL", "http://media-service:8095"), Strip: true},
		{Prefix: "/api/imaging", Service: "imaging-service", Upstream: up("IMAGING_URL", "http://imaging-service:8096"), Strip: true},
		{Prefix: "/api/training", Service: "training-service", Upstream: up("TRAINING_URL", "http://training-service:8097"), Strip: true},
		{Prefix: "/api/ipd", Service: "ipd-service", Upstream: up("IPD_URL", "http://ipd-service:8098"), Strip: true},
		{Prefix: "/api/whatsapp", Service: "whatsapp-gateway", Upstream: up("WHATSAPP_URL", "http://whatsapp-gateway:8099"), Strip: true},
		{Prefix: "/api/sync", Service: "sync-ingest", Upstream: up("SYNC_URL", "http://sync-ingest:8443"), Strip: true},
	}
}

// metricsProbes lists the upstreams whose /metrics the platform overview
// aggregates. Kept separate from routes: fixtures (payment-switch-mock)
// and webhooks (ussd-gateway) carry no operational metrics.
func metricsProbes(env func(string) string) map[string]string {
	up := func(key, def string) string {
		if v := env(key); v != "" {
			return v
		}
		return def
	}
	return map[string]string{
		"claims-orchestrator":  up("CLAIMS_URL", "http://claims-orchestrator:8080"),
		"registry-service":     up("REGISTRY_URL", "http://registry-service:8081"),
		"consent-service":      up("CONSENT_URL", "http://consent-service:8082"),
		"wallet-service":       up("WALLET_URL", "http://wallet-service:8084"),
		"shr-service":          up("SHR_URL", "http://shr-service:8085"),
		"equipment-service":    up("EQUIPMENT_URL", "http://equipment-service:8086"),
		"telehealth-service":   up("TELEHEALTH_URL", "http://telehealth-service:8087"),
		"pharmacy-service":     up("PHARMACY_URL", "http://pharmacy-service:8088"),
		"notification-service": up("NOTIFY_URL", "http://notification-service:8089"),
		"sync-ingest":          up("SYNC_URL", "http://sync-ingest:8443"),
	}
}

// ---------- rate limiting ----------

// rateLimiter is a per-client token bucket (stdlib sync.Map). Defaults:
// 20 req/s burst, refill 10/s — interactive clinical use passes, scrapers
// and runaway clients get 429.
type rateLimiter struct {
	mu      sync.Mutex
	buckets map[string]*bucket
	rate    float64
	burst   float64
}

type bucket struct {
	tokens float64
	last   time.Time
}

func newRateLimiter(rate, burst float64) *rateLimiter {
	return &rateLimiter{buckets: map[string]*bucket{}, rate: rate, burst: burst}
}

func (rl *rateLimiter) allow(key string) bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()
	b, ok := rl.buckets[key]
	if !ok {
		b = &bucket{tokens: rl.burst, last: time.Now()}
		rl.buckets[key] = b
	}
	elapsed := time.Since(b.last).Seconds()
	b.tokens += elapsed * rl.rate
	if b.tokens > rl.burst {
		b.tokens = rl.burst
	}
	b.last = time.Now()
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

// ---------- gateway ----------

type gateway struct {
	routes   []Route
	limiter  *rateLimiter
	probes   map[string]string // service -> health URL
	metrics  map[string]string // service -> metrics URL
	appsRoot string            // static app shell root ("" disables serving)
	kpiPath  string            // ADR-0033 nightly KPI plane JSON ("" disables)

	breakersMu sync.Mutex
	breakers   map[string]*upBreaker // service -> circuit breaker
	now        func() time.Time      // test seam
}

func newGateway(routes []Route, rl *rateLimiter) *gateway {
	g := &gateway{
		routes: routes, limiter: rl,
		probes: map[string]string{}, metrics: map[string]string{},
		breakers: map[string]*upBreaker{}, now: time.Now,
	}
	for _, r := range routes {
		g.probes[r.Service] = strings.TrimSuffix(r.Upstream, "/") + "/healthz"
	}
	return g
}

// breakerFor returns (creating on first use) the breaker for a service.
// One breaker per logical service: all routes to the same upstream share
// its health view.
func (g *gateway) breakerFor(service string) *upBreaker {
	g.breakersMu.Lock()
	defer g.breakersMu.Unlock()
	b, ok := g.breakers[service]
	if !ok {
		b = newUpBreaker(5, 30*time.Second)
		g.breakers[service] = b
	}
	return b
}

func (g *gateway) proxyFor(route Route) http.Handler {
	target, err := url.Parse(route.Upstream)
	if err != nil {
		log.Fatalf("gateway: bad upstream %s: %v", route.Upstream, err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	original := proxy.Director
	proxy.Director = func(req *http.Request) {
		original(req)
		switch {
		case route.ToPrefix != "":
			req.URL.Path = route.ToPrefix + strings.TrimPrefix(req.URL.Path, route.Prefix)
		case route.Strip:
			req.URL.Path = strings.TrimPrefix(req.URL.Path, route.Prefix)
			if req.URL.Path == "" {
				req.URL.Path = "/"
			}
		}
		// identity mapping: the OIDC facility claim becomes the header the
		// SHR consent gate enforces, and the subject becomes the header the
		// ReBAC patient gate checks — one trust decision, made here.
		if claims, ok := authz.FromContext(req.Context()); ok {
			if claims.Facility != "" {
				req.Header.Set("X-Facility-ID", claims.Facility)
			}
			if claims.Subject != "" {
				req.Header.Set("X-User-ID", claims.Subject)
			}
		}
		// trace continuity into the upstream service
		otelx.InjectOutbound(req)
		req.Host = target.Host
	}
	br := g.breakerFor(route.Service)
	// 5xx from the upstream counts as a failure toward the breaker; 4xx is
	// the client's fault and must not trip it.
	proxy.ModifyResponse = func(resp *http.Response) error {
		if resp.StatusCode >= 500 {
			br.failure(g.now())
		} else {
			br.success()
		}
		return nil
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		br.failure(g.now())
		log.Printf("gateway: upstream %s error: %v", route.Service, err)
		http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
	}
	return proxy
}

func (g *gateway) clientKey(r *http.Request) string {
	host := r.RemoteAddr
	if i := strings.LastIndex(host, ":"); i > 0 {
		host = host[:i]
	}
	return host
}

func (g *gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/healthz", "/api/health":
		g.serveHealth(w)
		return
	case "/api/platform/overview":
		g.serveOverview(w)
		return
	case "/api/kpi/plane":
		g.serveKPIPlane(w, r)
		return
	}
	for _, route := range g.routes {
		if strings.HasPrefix(r.URL.Path, route.Prefix) {
			if !g.limiter.allow(g.clientKey(r)) {
				w.Header().Set("Retry-After", "1")
				http.Error(w, `{"error":"rate limited"}`, http.StatusTooManyRequests)
				return
			}
			br := g.breakerFor(route.Service)
			if !br.allow(g.now()) {
				w.Header().Set("Retry-After", "5")
				http.Error(w, `{"error":"upstream circuit open"}`, http.StatusServiceUnavailable)
				return
			}
			g.proxyFor(route).ServeHTTP(w, r)
			return
		}
	}
	// Client apps: /app/ is the offline-first nurse/CHW PWA, /dash/ the
	// facility & state operations dashboards, /patient/ the patient
	// portal PWA. Same origin as the API, so the apps inherit OIDC and
	// never fight CORS.
	if g.appsRoot != "" && (r.URL.Path == "/" || strings.HasPrefix(r.URL.Path, "/app") ||
		strings.HasPrefix(r.URL.Path, "/dash") || strings.HasPrefix(r.URL.Path, "/patient")) {
		g.serveApp(w, r)
		return
	}
	http.Error(w, `{"error":"no route"}`, http.StatusNotFound)
}

// serveKPIPlane serves the nightly KPI document (ADR-0033) built by
// packages/kpiplane. It is a file, not a fan-out: the dashboards must
// render from last-night's snapshot even when every upstream is down,
// so the gateway never computes it live. Only GET; 404 until the first
// nightly build lands.
func (g *gateway) serveKPIPlane(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	if g.kpiPath == "" {
		http.Error(w, `{"error":"kpi plane not configured"}`, http.StatusNotFound)
		return
	}
	data, err := os.ReadFile(g.kpiPath)
	if err != nil {
		http.Error(w, `{"error":"kpi plane not built"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "max-age=300")
	w.Write(data)
}

// serveApp maps / and /app/* to apps/pwa, /dash/* to apps/dashboards,
// /patient/* to apps/patient.
func (g *gateway) serveApp(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	sub := "pwa"
	switch {
	case path == "/" || path == "/app" || path == "/app/":
		path = "/index.html"
	case strings.HasPrefix(path, "/app/"):
		path = strings.TrimPrefix(path, "/app")
	case path == "/patient" || path == "/patient/":
		sub, path = "patient", "/index.html"
	case strings.HasPrefix(path, "/patient/"):
		sub, path = "patient", strings.TrimPrefix(path, "/patient")
	case path == "/dash" || path == "/dash/":
		sub, path = "dashboards", "/index.html"
	default:
		sub, path = "dashboards", strings.TrimPrefix(path, "/dash")
	}
	// no traversal: the path is joined under a fixed root
	clean := filepath.Clean("/" + path)
	http.ServeFile(w, r, filepath.Join(g.appsRoot, sub, clean))
}

// serveOverview fans out to every service /metrics and returns one
// aggregate document — the state dashboard's single pane of glass.
// A down service is reported, never fatal: the platform degrades, the
// overview still renders.
func (g *gateway) serveOverview(w http.ResponseWriter) {
	type svcMetrics struct {
		Up            bool    `json:"up"`
		RequestsTotal int64   `json:"requests_total"`
		Errors4xx     int64   `json:"errors_4xx"`
		Errors5xx     int64   `json:"errors_5xx"`
		LatencyMeanMs float64 `json:"latency_ms_mean"`
		UptimeSec     int64   `json:"uptime_seconds"`
	}
	out := map[string]svcMetrics{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	client := &http.Client{Timeout: 2 * time.Second}
	for svc, base := range g.metrics {
		wg.Add(1)
		go func(svc, base string) {
			defer wg.Done()
			var m svcMetrics
			resp, err := client.Get(strings.TrimSuffix(base, "/") + "/metrics")
			if err == nil && resp.StatusCode == 200 {
				var raw struct {
					RequestsTotal *int64   `json:"requests_total"`
					Errors4xx     *int64   `json:"errors_4xx"`
					Errors5xx     *int64   `json:"errors_5xx"`
					LatencyMeanMs *float64 `json:"latency_ms_mean"`
					UptimeSec     *int64   `json:"uptime_seconds"`
				}
				if json.NewDecoder(resp.Body).Decode(&raw) == nil {
					m.Up = true
					if raw.RequestsTotal != nil {
						m.RequestsTotal = *raw.RequestsTotal
					}
					if raw.Errors4xx != nil {
						m.Errors4xx = *raw.Errors4xx
					}
					if raw.Errors5xx != nil {
						m.Errors5xx = *raw.Errors5xx
					}
					if raw.LatencyMeanMs != nil {
						m.LatencyMeanMs = *raw.LatencyMeanMs
					}
					if raw.UptimeSec != nil {
						m.UptimeSec = *raw.UptimeSec
					}
				}
			}
			if resp != nil {
				resp.Body.Close()
			}
			mu.Lock()
			out[svc] = m
			mu.Unlock()
		}(svc, base)
	}
	wg.Wait()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

// serveHealth probes every upstream /healthz and reports each service's
// circuit-breaker state — one pane of glass. A service with an open
// breaker is failing fast (503s without upstream waits); the probe still
// runs so the dashboard sees the moment the upstream recovers.
func (g *gateway) serveHealth(w http.ResponseWriter) {
	type result struct {
		Up      bool          `json:"up"`
		Latency int64         `json:"latency_ms"`
		Err     string        `json:"error,omitempty"`
		Breaker BreakerStatus `json:"breaker"`
	}
	out := map[string]result{}
	client := &http.Client{Timeout: 2 * time.Second}
	for svc, url := range g.probes {
		start := time.Now()
		resp, err := client.Get(url)
		res := result{Latency: time.Since(start).Milliseconds()}
		if err != nil {
			res.Err = err.Error()
		} else {
			res.Up = resp.StatusCode == 200
			resp.Body.Close()
		}
		g.breakersMu.Lock()
		if b, ok := g.breakers[svc]; ok {
			res.Breaker = b.status(g.now())
		} else {
			res.Breaker = BreakerStatus{State: "closed"}
		}
		g.breakersMu.Unlock()
		out[svc] = res
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

func main() {
	addr := ":8000"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	g := newGateway(DefaultRoutes(os.Getenv), newRateLimiter(10, 20))
	g.metrics = metricsProbes(os.Getenv)
	g.kpiPath = os.Getenv("KPI_PLANE_PATH") // ADR-0033, e.g. /srv/openhealth/lakehouse/gold/kpi_plane.json
	g.appsRoot = os.Getenv("APPS_ROOT")     // e.g. /srv/openhealth/apps
	if g.appsRoot != "" {
		log.Printf("serving client apps from %s (/app, /dash)", g.appsRoot)
	}
	var handler http.Handler = g
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		// JWKS warm standby: every gateway replica shares one cache
		// (CACHE_URL=redis://…); a verified key set survives an IdP
		// outage for 24h. Without CACHE_URL the replica still gets a
		// process-local LRU — degraded, but better than fail-closed on a
		// 30-second IdP blip.
		cache := cachex.FromEnv(os.Getenv)
		authV.SetSharedCache(cache)
		authV.OnEvent = func(msg string) { log.Printf("jwks: %s", msg) }
		handler = authV.Middleware(handler)
		log.Print("oidc: enforcing bearer auth at the platform edge (jwks warm standby on)")
	}
	// Edge screening (the in-platform WAF controls, shared/wafx) runs
	// before auth: junk requests are rejected before any JWT crypto, and
	// request-id sits outermost so even rejections are traceable.
	handler = wafx.Middleware(handler, wafx.Policy{
		OnReject: func(reason string, r *http.Request) {
			log.Printf("wafx: %s %s from %s rejected: %s", r.Method, r.URL.Path, g.clientKey(r), reason)
		},
	})
	handler = requestID(handler)
	// Observability (ADR-0040): outermost layer — every request, including
	// WAF rejections and auth failures, emits a span and RED metrics with
	// per-tenant labels. /metrics stays scrape-only (no auth).
	exp, mx := otelx.Instrument("api-gateway", os.Getenv)
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", mx.Handler())
	metricsMux.Handle("/", handler)
	handler = otelx.MiddlewareMetrics("api-gateway", exp, mx, metricsMux)
	log.Printf("OpenHealth OS api-gateway on %s (%d routes)", addr, len(g.routes))
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{MetricsPath: "/metrics-legacy"}))
}
