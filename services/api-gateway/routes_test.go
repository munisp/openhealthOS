package main

import (
	"strings"
	"testing"
)

// ADR-0036 regression guard: every platform service must be reachable
// through the gateway (single public entry). If you add a service, add
// its route here; if you retire one, remove it. A service that only
// listens on its own port is a private door the doctrine forbids.
func TestRouteTableCoversEveryPlatformService(t *testing.T) {
	routes := DefaultRoutes(func(string) string { return "" })

	byService := map[string][]string{}
	for _, r := range routes {
		byService[r.Service] = append(byService[r.Service], r.Prefix)
	}

	want := map[string]string{
		"claims-orchestrator":  "/api/claims",
		"registry-service":     "/api/registry",
		"consent-service":      "/api/consents",
		"ussd-gateway":         "/api/ussd",
		"wallet-service":       "/api/wallet",
		"shr-service":          "/api/shr",
		"equipment-service":    "/api/equipment",
		"telehealth-service":   "/api/telehealth",
		"pharmacy-service":     "/api/pharmacy",
		"notification-service": "/api/notify",
		"wellness-service":     "/api/wellness",
		"scheduling-service":   "/api/scheduling",
		"dispatch-service":     "/api/dispatch",
		"telemed-service":      "/api/telemed",
		"media-service":        "/api/media",
		"imaging-service":      "/api/imaging",
		"training-service":     "/api/training",
		"ipd-service":          "/api/ipd",
		"sync-ingest":          "/api/sync",
	}
	for svc, prefix := range want {
		found := false
		for _, p := range byService[svc] {
			if p == prefix {
				found = true
			}
		}
		if !found {
			t.Errorf("service %s has no gateway route %s (has: %s)", svc, prefix, strings.Join(byService[svc], ", "))
		}
	}
}

// No two routes may share a prefix: first match wins, so a duplicate is
// a silent shadow.
func TestRoutePrefixesAreUnique(t *testing.T) {
	seen := map[string]string{}
	for _, r := range DefaultRoutes(func(string) string { return "" }) {
		if prev, dup := seen[r.Prefix]; dup {
			t.Errorf("prefix %s routed to both %s and %s", r.Prefix, prev, r.Service)
		}
		seen[r.Prefix] = r.Service
	}
}

// Default upstreams must agree with each service's LISTEN default — a
// wrong port here is a dead route in every demo and dev deployment.
func TestDefaultUpstreamPortsMatchServiceDefaults(t *testing.T) {
	want := map[string]string{
		"SCHEDULING_URL": "8092",
		"DISPATCH_URL":   "8093",
		"TELEMED_URL":    "8094",
		"MEDIA_URL":      "8095",
		"IMAGING_URL":    "8096",
		"TRAINING_URL":   "8097",
		"IPD_URL":        "8098",
		"WELLNESS_URL":   "8090",
	}
	for _, r := range DefaultRoutes(func(string) string { return "" }) {
		for env, port := range want {
			if strings.Contains(r.Upstream, ":"+port) && !strings.HasSuffix(r.Upstream, ":"+port) {
				t.Errorf("route %s: malformed upstream %s", r.Prefix, r.Upstream)
			}
			_ = env
		}
	}
	for env, port := range want {
		found := false
		for _, r := range DefaultRoutes(func(string) string { return "" }) {
			if strings.HasSuffix(r.Upstream, ":"+port) {
				found = true
			}
		}
		if !found {
			t.Errorf("no default route targets port %s (env %s)", port, env)
		}
	}
}
