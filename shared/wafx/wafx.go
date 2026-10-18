// Package wafx — in-platform web application firewall controls for the
// OpenHealth OS edge (ADR-0015), the OpenAppSec row made concrete.
//
// OpenAppSec is not embedded (license + footprint); its job at the
// ingress is instead done here, stdlib-only, as four explicit controls:
//
//  1. Method allowlist — anything outside the HTTP verbs the platform
//     actually uses gets 405 before it reaches routing or auth.
//  2. Content-Type enforcement — requests with bodies must declare a
//     content type the platform parses (JSON, FHIR+JSON, form,
//     multipart). A body without a known type is 415: it can only be a
//     client bug or a parser-confusion probe.
//  3. Body size cap — Content-Length over the cap is 413 immediately;
//     chunked bodies get a MaxBytesReader so a lying client is cut off
//     mid-stream.
//  4. Probe screening — URL path and query are screened for the classic
//     injection shapes: SQLi (multi-signal only, so "O'Brien" searches
//     pass), path traversal, XSS probes, null bytes. JSON bodies are
//     NOT screened: clinical free text legitimately contains quotes,
//     angle brackets and dashes, and false positives on a referral note
//     are worse than the marginal coverage — injection resistance in
//     bodies comes from parameterized queries (pgx) everywhere.
//
// Everything is fail-closed but observable: every rejection reports its
// reason through OnReject and carries a machine-readable code so ops
// can tune policy from evidence instead of guessing.
package wafx

import (
	"fmt"
	"net/http"
	"regexp"
	"strings"
)

// DefaultMaxBodyBytes caps request bodies at 1 MiB — far above any FHIR
// resource or claim bundle the platform accepts, far below memory-
// exhaustion territory.
const DefaultMaxBodyBytes = 1 << 20

// Policy configures the middleware. The zero Policy is production-ready:
// default methods, default content types, 1 MiB cap, screening on.
type Policy struct {
	// Methods overrides the allowed HTTP methods. nil →
	// GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS.
	Methods map[string]bool
	// BodyTypes overrides the accepted Content-Type prefixes for
	// requests with bodies. nil → JSON (incl. application/fhir+json),
	// form-urlencoded, multipart.
	BodyTypes []string
	// MaxBodyBytes overrides DefaultMaxBodyBytes.
	MaxBodyBytes int64
	// Screen disables probe screening when false... it does not:
	// screening is always on. This field reserved.
	// OnReject receives (reason, request) for every rejection.
	OnReject func(reason string, r *http.Request)
}

var defaultMethods = map[string]bool{
	http.MethodGet: true, http.MethodHead: true, http.MethodPost: true,
	http.MethodPut: true, http.MethodPatch: true, http.MethodDelete: true,
	http.MethodOptions: true,
}

var defaultBodyTypes = []string{
	"application/json",
	"application/fhir+json",
	"application/x-www-form-urlencoded",
	"multipart/form-data",
	"text/plain", // USSD aggregator webhooks
}

// probe patterns — each is deliberately multi-signal. A lone apostrophe
// or the word "select" in a search string must never trip the screen.
var probes = []*regexp.Regexp{
	regexp.MustCompile(`(?i)union\s+(all\s+)?select\b`),                                   // UNION SELECT
	regexp.MustCompile(`(?i)\b(drop|truncate)\s+table\b`),                                 // DDL in a query string
	regexp.MustCompile(`(?i)'\s*or\s+['"]?\d+['"]?\s*=\s*['"]?\d+`),                       // ' OR 1=1
	regexp.MustCompile(`(?i)'\s*(and|or)\s+'[^']*'\s*=\s*'[^']*'`),                        // ' OR 'a'='a
	regexp.MustCompile(`(?i);\s*(drop|delete\s+from|insert\s+into|update\s+\w+\s+set)\b`), // stacked query
	regexp.MustCompile(`(?i)(benchmark|sleep|pg_sleep)\s*\(\s*\d+`),                       // time-based blind
	regexp.MustCompile(`(?i)\.\./`),                                                       // traversal (decoded)
	regexp.MustCompile(`(?i)%2e%2e`),                                                      // traversal (encoded)
	regexp.MustCompile(`(?i)<\s*/?\s*script\b`),                                           // XSS probe
	regexp.MustCompile(`(?i)javascript\s*:`),                                              // javascript: URL
	regexp.MustCompile(`(?i)on(error|load|click)\s*=`),                                    // inline-handler probe
	regexp.MustCompile(`%00|\x00`),                                                        // null byte
}

// Middleware wraps next with the WAF controls. Rejections carry a
// machine-readable code in the JSON body: method_not_allowed,
// unsupported_media_type, body_too_large, probe_blocked.
func Middleware(next http.Handler, p Policy) http.Handler {
	methods := p.Methods
	if methods == nil {
		methods = defaultMethods
	}
	bodyTypes := p.BodyTypes
	if bodyTypes == nil {
		bodyTypes = defaultBodyTypes
	}
	maxBody := p.MaxBodyBytes
	if maxBody <= 0 {
		maxBody = DefaultMaxBodyBytes
	}
	reject := func(w http.ResponseWriter, r *http.Request, code, reason string, status int) {
		if p.OnReject != nil {
			p.OnReject(reason, r)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		fmt.Fprintf(w, `{"error":%q,"code":%q}`, reason, code)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 1. method allowlist
		if !methods[r.Method] {
			w.Header().Set("Allow", allowedList(methods))
			reject(w, r, "method_not_allowed", "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		// 2/3. content-type + size for requests carrying a body
		if r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodPatch {
			if r.ContentLength > maxBody { // -1 (chunked) passes to the reader cap
				reject(w, r, "body_too_large", "request body too large", http.StatusRequestEntityTooLarge)
				return
			}
			if r.ContentLength != 0 {
				ct := r.Header.Get("Content-Type")
				ok := false
				for _, prefix := range bodyTypes {
					if strings.HasPrefix(strings.ToLower(strings.TrimSpace(ct)), prefix) {
						ok = true
						break
					}
				}
				if !ok {
					reject(w, r, "unsupported_media_type", "unsupported or missing content type", http.StatusUnsupportedMediaType)
					return
				}
			}
			r.Body = http.MaxBytesReader(w, r.Body, maxBody)
		}
		// 4. probe screening on path + query. Screen both the raw target
		// (catches encoded probes like %2e%2e) and the fully decoded
		// key=value pairs (catches ' OR 1=1 hiding behind %27%20).
		target := r.URL.Path + "?" + r.URL.RawQuery
		var db strings.Builder
		db.WriteString(r.URL.Path)
		for k, vs := range r.URL.Query() {
			for _, v := range vs {
				db.WriteString("&")
				db.WriteString(k)
				db.WriteString("=")
				db.WriteString(v)
			}
		}
		decoded := db.String()
		for _, probe := range probes {
			if probe.MatchString(target) || probe.MatchString(decoded) {
				reject(w, r, "probe_blocked", "request blocked by edge screening", http.StatusBadRequest)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func allowedList(methods map[string]bool) string {
	out := make([]string, 0, len(methods))
	for m := range methods {
		out = append(out, m)
	}
	return strings.Join(out, ", ")
}
