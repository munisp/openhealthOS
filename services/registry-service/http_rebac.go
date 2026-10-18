// ReBAC enforcement + grant management (ADR-0015) — the Permify row's
// consumer. The platform rule (shared/rebac) becomes concrete here:
//
//   - patient reads (GET /patients/{id}, /fhir/Patient/{id}) pass a gate:
//     the X-User-ID the gateway set from the OIDC subject must satisfy
//     Checker.CanAccessPatient — facility staff reach their facility's
//     patients, guardians/care-team/referral tuples reach beyond it,
//     everything else is 403. Unattributed access (no X-User-ID) is
//     denied: same doctrine as the SHR consent gate.
//   - grants are managed over HTTP (POST/DELETE/GET /rebac/grants) so
//     onboarding a nurse or registering a guardian is an API call, not
//     a SQL session. Only facility admins may manage grants touching
//     their facility; platform operators (REBAC_BOOTSTRAP_ADMINS) seed
//     the first admins, solving the bootstrap problem without a
//     backdoor.
//
// The gate binds only when REBAC_ENFORCE=true — edge nodes and dev run
// open, the state plane runs closed, and the toggle is explicit.
package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/munisp/openhealthOS/shared/rebac"
)

type rebacAPI struct {
	checker   *rebac.Checker
	patients  PatientStore
	bootstrap map[string]bool // platform operators from env
}

func newRebacAPI(checker *rebac.Checker, patients PatientStore, bootstrapAdmins []string) *rebacAPI {
	a := &rebacAPI{checker: checker, patients: patients, bootstrap: map[string]bool{}}
	for _, id := range bootstrapAdmins {
		if id = strings.TrimSpace(id); id != "" {
			a.bootstrap[id] = true
		}
	}
	return a
}

// mayManage reports whether caller may manage grants on object:
// platform operators always; otherwise facility admins for their own
// facility, or for patients registered at their facility.
func (a *rebacAPI) mayManage(caller string, object rebac.Entity) bool {
	if caller == "" {
		return false
	}
	if a.bootstrap[caller] {
		return true
	}
	facilityID := ""
	switch object.Type {
	case "facility":
		facilityID = object.ID
	case "patient":
		p, err := a.patients.GetPatient(object.ID)
		if err != nil {
			return false
		}
		facilityID = p.FacilityID
	default:
		return false
	}
	ok, err := a.checker.IsFacilityAdmin(caller, facilityID)
	return err == nil && ok
}

// grantBody is the wire shape for grant management.
type grantBody struct {
	SubjectType  string `json:"subject_type"`
	SubjectID    string `json:"subject_id"`
	Relation     string `json:"relation"`
	ObjectType   string `json:"object_type"`
	ObjectID     string `json:"object_id"`
	ExpiresInSec int64  `json:"expires_in_seconds,omitempty"`
}

func (b grantBody) tuple(caller string) rebac.Tuple {
	t := rebac.Tuple{
		Subject:   rebac.Entity{Type: b.SubjectType, ID: b.SubjectID},
		Relation:  b.Relation,
		Object:    rebac.Entity{Type: b.ObjectType, ID: b.ObjectID},
		GrantedBy: caller,
	}
	if b.ExpiresInSec > 0 {
		exp := time.Now().Add(time.Duration(b.ExpiresInSec) * time.Second)
		t.ExpiresAt = &exp
	}
	return t
}

func (a *rebacAPI) registerRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/rebac/grants", func(w http.ResponseWriter, r *http.Request) {
		caller := r.Header.Get("X-User-ID")
		switch r.Method {
		case http.MethodPost, http.MethodDelete:
			var b grantBody
			if err := json.NewDecoder(r.Body).Decode(&b); err != nil {
				http.Error(w, "bad grant: "+err.Error(), http.StatusBadRequest)
				return
			}
			t := b.tuple(caller)
			if !a.mayManage(caller, t.Object) {
				http.Error(w, `{"error":"only facility admins manage grants"}`, http.StatusForbidden)
				return
			}
			var err error
			if r.Method == http.MethodPost {
				err = a.checker.Store.Grant(t)
			} else {
				err = a.checker.Store.Revoke(t.Subject, t.Relation, t.Object)
			}
			if err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, 200, map[string]any{"ok": true, "granted_by": caller})
		case http.MethodGet:
			object := rebac.Entity{Type: r.URL.Query().Get("object_type"), ID: r.URL.Query().Get("object_id")}
			if object.Type == "" || object.ID == "" {
				http.Error(w, "object_type and object_id required", http.StatusBadRequest)
				return
			}
			if !a.mayManage(caller, object) {
				http.Error(w, `{"error":"only facility admins view grants"}`, http.StatusForbidden)
				return
			}
			tuples, err := a.checker.Store.List(object)
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(w, 200, map[string]any{"tuples": tuples})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
}

// gate enforces patient-level ReBAC on reads. It wraps the mux: only
// patient-read paths are gated, everything else flows through.
func (a *rebacAPI) gate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var patientID string
		switch {
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/patients/") &&
			!strings.HasPrefix(r.URL.Path, "/patients/search"):
			patientID = strings.TrimPrefix(r.URL.Path, "/patients/")
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/fhir/Patient/"):
			patientID = strings.TrimPrefix(r.URL.Path, "/fhir/Patient/")
		default:
			next.ServeHTTP(w, r)
			return
		}
		if strings.Contains(patientID, "/") { // sub-actions (e.g. /nin) pass
			next.ServeHTTP(w, r)
			return
		}
		caller := r.Header.Get("X-User-ID")
		if caller == "" {
			http.Error(w, `{"error":"X-User-ID required — unattributed access is never allowed"}`, http.StatusForbidden)
			return
		}
		p, err := a.patients.GetPatient(patientID)
		if err != nil { // unknown patient: let the route produce its own 404
			next.ServeHTTP(w, r)
			return
		}
		ok, err := a.checker.CanAccessPatient(caller, rebac.PatientRef{ID: p.ID, FacilityID: p.FacilityID})
		if err != nil {
			http.Error(w, `{"error":"authorization store unavailable"}`, http.StatusServiceUnavailable)
			return
		}
		if !ok {
			http.Error(w, `{"error":"no grant on this patient"}`, http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}
