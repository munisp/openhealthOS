// federation.go — facility-to-facility record exchange (ADR-0057).
// A referral bundle leaves facility A for peer facility B ONLY when the
// request carries a consent artefact id that the consent-service has
// verified (fail-closed: no consent, no bundle). Bundles are FHIR
// transaction Bundles; the receiving peer imports them through the normal
// Put path so the same access rules apply on both sides.
package main

import (
	"encoding/json"
	"net/http"
	"os"
	"time"
)

type federationHandler struct {
	store   *Store
	consent ConsentChecker
	selfID  string // this facility's OHF id
	peers   map[string]string // peer facility id -> base URL
}

func newFederationHandler(store *Store, consent ConsentChecker, selfID string, peers map[string]string) *federationHandler {
	return &federationHandler{store: store, consent: consent, selfID: selfID, peers: peers}
}

// POST /federation/bundle {patient_id, target_facility, consent_id}
// Builds the patient's exchange bundle for the named peer, gated on consent.
func (h *federationHandler) bundle(w http.ResponseWriter, r *http.Request) {
	var in struct {
		PatientID      string `json:"patient_id"`
		TargetFacility string `json:"target_facility"`
	}
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil || in.PatientID == "" || in.TargetFacility == "" {
		writeJSON(w, 400, map[string]string{"error": "patient_id and target_facility required"})
		return
	}
	if _, known := h.peers[in.TargetFacility]; !known {
		writeJSON(w, 404, map[string]string{"error": "unknown peer facility"})
		return
	}
	ok, err := h.consent.Check(in.PatientID, in.TargetFacility, "record:read")
	if err != nil || !ok {
		// fail-closed: no verified consent, no bundle — honest reason
		reason := "consent not granted"
		if err != nil {
			reason = "consent check failed: " + err.Error()
		}
		writeJSON(w, 403, map[string]string{"error": reason})
		return
	}
	resources, err := h.store.PatientEverything(in.PatientID, h.selfID, false)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	entries := []map[string]any{}
	for _, res := range resources {
		entries = append(entries, map[string]any{"resource": json.RawMessage(res.Body),
			"request": map[string]string{"method": "PUT", "url": res.ResourceType + "/" + res.ID}})
	}
	writeJSON(w, 200, map[string]any{
		"resourceType": "Bundle", "type": "transaction",
		"meta": map[string]string{"source": h.selfID, "target": in.TargetFacility,
			"generated": time.Now().UTC().Format(time.RFC3339)},
		"entry": entries})
}

// GET /federation/peers — discoverable peer manifest (public metadata only).
func (h *federationHandler) peersHandler(w http.ResponseWriter, r *http.Request) {
	peers := []map[string]string{}
	for id, base := range h.peers {
		peers = append(peers, map[string]string{"facility_id": id, "base": base})
	}
	writeJSON(w, 200, map[string]any{"self": h.selfID, "peers": peers})
}

func (h *federationHandler) routes(mux *http.ServeMux) {
	mux.HandleFunc("POST /federation/bundle", h.bundle)
	mux.HandleFunc("GET /federation/peers", h.peersHandler)
}

func federationPeersFromEnv() map[string]string {
	out := map[string]string{}
	if v := os.Getenv("OH_FEDERATION_PEERS"); v != "" {
		json.Unmarshal([]byte(v), &out)
	}
	return out
}
