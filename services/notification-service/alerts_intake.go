// alerts_intake.go — Alertmanager webhook intake (ADR-0040).
//
// The open-source alert stack (Prometheus + Alertmanager) fires here; we
// fan the alert out through the platform's own provider chain — Matrix
// first, SMS fallback — to the ops contacts for the alert's tenant. The
// alert platform and the delivery rail are the same pipe, so an alert that
// says "notifications are broken" can still get out through a healthy leg.
//
// Contact routing is env-driven, no database:
//
//	ALERT_OPS_CONTACTS                 — platform on-call, comma phones/users
//	ALERT_OPS_CONTACTS_TENANT_<NAME>   — per-tenant override (uppercased)
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// alertmanagerWebhook is the v4 webhook payload shape.
type alertmanagerWebhook struct {
	Status  string `json:"status"` // firing | resolved
	Alerts  []struct {
		Status      string            `json:"status"`
		Labels      map[string]string `json:"labels"`
		Annotations map[string]string `json:"annotations"`
		StartsAt    time.Time         `json:"startsAt"`
	} `json:"alerts"`
}

// opsContactsFor resolves the contact list for a tenant: tenant-specific
// override wins, then the platform-wide list, else nil (fail-loud 503 —
// an alert with nowhere to go must not be silently swallowed).
func opsContactsFor(tenant string) []string {
	if tenant != "" {
		key := "ALERT_OPS_CONTACTS_TENANT_" + strings.ToUpper(strings.Map(func(r rune) rune {
			if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r >= 'A' && r <= 'Z' {
				return r
			}
			return '_'
		}, tenant))
		if v := os.Getenv(key); v != "" {
			return splitCSV(v)
		}
	}
	return splitCSV(os.Getenv("ALERT_OPS_CONTACTS"))
}

func splitCSV(v string) []string {
	var out []string
	for _, p := range strings.Split(v, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// renderAlert composes the ops text — operational detail is fine here
// (this is staff traffic, not the clinical-detail-free patient rail).
func renderAlert(a struct {
	Status      string            `json:"status"`
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	StartsAt    time.Time         `json:"startsAt"`
}) string {
	name := a.Labels["alertname"]
	sev := a.Labels["severity"]
	summary := a.Annotations["summary"]
	if summary == "" {
		summary = name
	}
	icon := "FIRING"
	if a.Status == "resolved" {
		icon = "RESOLVED"
	}
	return fmt.Sprintf("[%s] %s (%s): %s", icon, name, sev, summary)
}

// registerAlertsIntake adds POST /alerts/intake to the mux.
func registerAlertsIntake(mux *http.ServeMux, svc *Service) {
	mux.HandleFunc("POST /alerts/intake", func(w http.ResponseWriter, r *http.Request) {
		var hook alertmanagerWebhook
		if err := json.NewDecoder(r.Body).Decode(&hook); err != nil {
			http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
			return
		}
		if len(hook.Alerts) == 0 {
			writeJSON(w, http.StatusOK, map[string]any{"delivered": 0})
			return
		}
		delivered, failed := 0, 0
		for _, a := range hook.Alerts {
			tenant := a.Labels["tenant"]
			contacts := opsContactsFor(tenant)
			if len(contacts) == 0 {
				log.Printf("alerts-intake: no ops contacts for tenant %q — alert %s NOT delivered",
					tenant, a.Labels["alertname"])
				failed++
				continue
			}
			text := renderAlert(a)
			for _, c := range contacts {
				if err := svc.provider.Send(c, text); err != nil {
					log.Printf("alerts-intake: send to contact failed: %v", err)
					failed++
				} else {
					delivered++
				}
			}
		}
		if failed > 0 && delivered == 0 {
			// 503 so Alertmanager retries on its own schedule.
			http.Error(w, `{"error":"no alert delivered"}`, http.StatusServiceUnavailable)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"delivered": delivered, "failed": failed})
	})
}
