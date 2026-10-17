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
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/otelx"
	"github.com/munisp/openhealthOS/shared/servex"
)

// Route maps one public prefix to one upstream service.
type Route struct {
	Prefix  string // public path prefix, e.g. "/api/claims"
	Service string // logical name (health matrix, spans)
	Upstream string // base URL, e.g. http://claims-orchestrator:8080
	Strip   bool   // strip the public prefix before forwarding
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
		{"/api/claims", "claims-orchestrator", up("CLAIMS_URL", "http://claims-orchestrator:8080"), true},
		{"/api/preauth", "claims-orchestrator", up("CLAIMS_URL", "http://claims-orchestrator:8080"), true},
		{"/api/registry", "registry-service", up("REGISTRY_URL", "http://registry-service:8081"), true},
		{"/api/consents", "consent-service", up("CONSENT_URL", "http://consent-service:8082"), true},
		{"/api/ussd", "ussd-gateway", up("USSD_URL", "http://ussd-gateway:8083"), true},
		{"/api/wallet", "wallet-service", up("WALLET_URL", "http://wallet-service:8084"), true},
		{"/api/shr", "shr-service", up("SHR_URL", "http://shr-service:8085"), true},
		{"/api/fhir", "shr-service", up("SHR_URL", "http://shr-service:8085"), false},
		{"/api/equipment", "equipment-service", up("EQUIPMENT_URL", "http://equipment-service:8086"), true},
		{"/api/telehealth", "telehealth-service", up("TELEHEALTH_URL", "http://telehealth-service:8087"), true},
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
	routes  []Route
	limiter *rateLimiter
	probes  map[string]string // service -> health URL
}

func newGateway(routes []Route, rl *rateLimiter) *gateway {
	g := &gateway{routes: routes, limiter: rl, probes: map[string]string{}}
	for _, r := range routes {
		g.probes[r.Service] = strings.TrimSuffix(r.Upstream, "/") + "/healthz"
	}
	return g
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
		if route.Strip {
			req.URL.Path = strings.TrimPrefix(req.URL.Path, route.Prefix)
			if req.URL.Path == "" {
				req.URL.Path = "/"
			}
		}
		// identity mapping: the OIDC facility claim becomes the header the
		// SHR consent gate enforces — one trust decision, made here.
		if claims, ok := authz.FromContext(req.Context()); ok && claims.Facility != "" {
			req.Header.Set("X-Facility-ID", claims.Facility)
		}
		// trace continuity into the upstream service
		otelx.InjectOutbound(req)
		req.Host = target.Host
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
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
	if r.URL.Path == "/healthz" || r.URL.Path == "/api/health" {
		g.serveHealth(w)
		return
	}
	for _, route := range g.routes {
		if strings.HasPrefix(r.URL.Path, route.Prefix) {
			if !g.limiter.allow(g.clientKey(r)) {
				w.Header().Set("Retry-After", "1")
				http.Error(w, `{"error":"rate limited"}`, http.StatusTooManyRequests)
				return
			}
			g.proxyFor(route).ServeHTTP(w, r)
			return
		}
	}
	http.Error(w, `{"error":"no route"}`, http.StatusNotFound)
}

// serveHealth probes every upstream /healthz — one pane of glass.
func (g *gateway) serveHealth(w http.ResponseWriter) {
	type result struct {
		Up      bool   `json:"up"`
		Latency int64  `json:"latency_ms"`
		Err     string `json:"error,omitempty"`
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
	var handler http.Handler = g
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth at the platform edge")
	}
	log.Printf("OpenHealth OS api-gateway on %s (%d routes)", addr, len(g.routes))
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
