// tenant.go — per-tenant observability propagation (ADR-0040).
//
// Tenancy enters at the edge: the api-gateway resolves the tenant (from the
// X-Tenant-ID header set by the tenant router, or from the `tenant_id` JWT
// claim once authz has verified the token) and stamps it on the request
// context. Every service propagates it onward as the `x-tenant-id` header
// next to `traceparent`, and stamps it on spans and metric series, so the
// whole telemetry plane can be sliced per tenant without any tenant ever
// seeing another's data.
package otelx

import (
	"context"
	"net/http"
)

// TenantHeader is the propagation header (lowercase, per HTTP/2 style).
const TenantHeader = "x-tenant-id"

// TenantAttr is the span/metric attribute name, following the OTel
// semantic-convention shape (tenant.id).
const TenantAttr = "tenant.id"

type tenantKey struct{}

// WithTenant returns a context carrying the tenant id.
func WithTenant(ctx context.Context, tenant string) context.Context {
	return context.WithValue(ctx, tenantKey{}, tenant)
}

// TenantFrom extracts the tenant id ("" if unknown — never fabricated).
func TenantFrom(ctx context.Context) string {
	t, _ := ctx.Value(tenantKey{}).(string)
	return t
}

// InjectTenant sets the tenant header on an outgoing request so the next
// hop continues the same tenant slice. Pair with InjectOutbound.
func InjectTenant(req *http.Request) {
	if t := TenantFrom(req.Context()); t != "" {
		req.Header.Set(TenantHeader, t)
	}
}

// TenantMiddleware resolves the tenant at the platform edge and stores it
// on the context. Trusted sources, in order: a verified claim extractor
// (wired by the gateway after JWT validation) or the propagation header.
// Services behind the gateway trust the header only because the gateway
// strips it from external callers first (deploy contract, see ADR-0040).
func TenantMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if t := r.Header.Get(TenantHeader); t != "" {
			r = r.WithContext(WithTenant(r.Context(), t))
		}
		next.ServeHTTP(w, r)
	})
}
