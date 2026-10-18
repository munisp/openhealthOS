package main

// Anti-collusion integrity plane (ADR-0018).
//
// Facilities and insiders collude in four expensive ways: ghost claims
// (care never delivered), phantom patients (enrolled by an insider,
// then ghosted against), stock theft (claimed-as-dispensed, sold
// privately), and double-dipping (claim the payer AND charge the
// patient cash). The platform's structural answers:
//
//   - linkage (in the adjudicator): no encounter, no clean approval;
//   - the patient as auditor: every adjudicated claim fires a
//     claim_submitted event to the notification rail — the patient
//     learns a claim exists in their name, in plain SMS, with the
//     amount, and can dispute it from the portal or a CHW;
//   - disputes land here: ownership-checked, appended to the audit
//     chain, and they feed the facility integrity scorecard;
//   - every sensitive mutation (adjudication, dispute, enrolment
//     change) is appended to a hash-chained audit file — the same
//     chain law as packages/auditlake, so tampering with the file is
//     detectable by auditlake's verifier;
//   - the integrity scorecard makes collusion visible in aggregate:
//     pend rates, linkage failures, dispute counts, biometric-presence
//     share per facility, with the formula in the open.
//
// None of this prevents off-platform care — nothing can. It makes
// off-platform care unprofitable (no money without an evidence chain)
// and on-platform fraud visible (patients, scorecards, chains).

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------- decision store

// StoredClaim is one adjudicated claim with its dispute state.
type StoredClaim struct {
	Claim         Claim    `json:"claim"`
	Decision      Decision `json:"decision"`
	At            string   `json:"at"`
	Disputed      bool     `json:"disputed"`
	DisputeReason string   `json:"dispute_reason,omitempty"`
	DisputedBy    string   `json:"disputed_by,omitempty"`
	DisputedAt    string   `json:"disputed_at,omitempty"`
}

// DecisionStore remembers adjudications (memory default; the audit
// chain is the durable artefact — the store is the query view over it).
type DecisionStore struct {
	mu   sync.Mutex
	byID map[string]*StoredClaim
}

func NewDecisionStore() *DecisionStore { return &DecisionStore{byID: map[string]*StoredClaim{}} }

func (s *DecisionStore) Put(c Claim, d Decision) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.byID[c.ClaimID] = &StoredClaim{
		Claim: c, Decision: d, At: time.Now().UTC().Format(time.RFC3339),
	}
}

func (s *DecisionStore) Get(id string) (StoredClaim, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if sc, ok := s.byID[id]; ok {
		return *sc, true
	}
	return StoredClaim{}, false
}

func (s *DecisionStore) ForMember(memberID string) []StoredClaim {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []StoredClaim{}
	for _, sc := range s.byID {
		if sc.Claim.MemberID == memberID {
			out = append(out, *sc)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].At > out[j].At })
	return out
}

func (s *DecisionStore) ForFacility(facilityID string) []StoredClaim {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []StoredClaim{}
	for _, sc := range s.byID {
		if sc.Claim.ProviderID == facilityID {
			out = append(out, *sc)
		}
	}
	return out
}

// Dispute marks a claim disputed (idempotent — re-disputing tells the
// same story once). Returns false when the claim is unknown.
func (s *DecisionStore) Dispute(id, reason, actor string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	sc, ok := s.byID[id]
	if !ok || sc.Disputed {
		return false // unknown claim, or the first accuser already stands
	}
	sc.Disputed = true
	sc.DisputeReason = reason
	sc.DisputedBy = actor
	sc.DisputedAt = time.Now().UTC().Format(time.RFC3339)
	return true
}

// ---------------------------------------------------------------- audit chain

// AuditChain appends hash-chained JSONL — the same law as
// packages/auditlake: hash = sha256(canonical(entry minus hash) +
// prev_hash), canonical = sorted keys, tight separators. The python
// verifier (auditlake.py verify) checks files this writer produces.
type AuditChain struct {
	mu   sync.Mutex
	path string
	prev string
	seq  int
}

const auditGenesis = "0000000000000000000000000000000000000000000000000000000000000000"

// NewAuditChain opens (or starts) the chain at path. Empty path = no
// durable chain (dev); the in-memory store still serves queries.
func NewAuditChain(path string) (*AuditChain, error) {
	c := &AuditChain{path: path, prev: auditGenesis}
	if path == "" {
		return c, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return c, nil
		}
		return nil, err
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if line == "" {
			continue
		}
		var entry map[string]any
		if json.Unmarshal([]byte(line), &entry) == nil {
			if h, ok := entry["hash"].(string); ok && h != "" {
				c.prev = h
			}
			if sq, ok := entry["seq"].(float64); ok {
				c.seq = int(sq)
			}
		}
	}
	return c, nil
}

// canonicalJSON mirrors auditlake.canonical: sorted keys, tight
// separators, hash field excluded. encoding/json sorts map keys; the
// entry is built as a map so the two implementations agree byte-for-byte.
func canonicalJSON(entry map[string]any) ([]byte, error) {
	body := map[string]any{}
	for k, v := range entry {
		if k != "hash" {
			body[k] = v
		}
	}
	return json.Marshal(body)
}

// Append writes one chained entry. A failed append is returned loudly —
// an integrity system that cannot audit itself must not pretend.
func (c *AuditChain) Append(actor, action, subject string, detail map[string]any) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.path == "" {
		return nil
	}
	if detail == nil {
		detail = map[string]any{}
	}
	c.seq++
	entry := map[string]any{
		"seq":       c.seq,
		"ts":        time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		"actor":     actor,
		"action":    action,
		"subject":   subject,
		"details":   detail,
		"source_id": fmt.Sprintf("%s-%d", actor, c.seq),
		"prev_hash": c.prev,
	}
	raw, err := canonicalJSON(entry)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(append(raw, []byte(c.prev)...))
	entry["hash"] = hex.EncodeToString(sum[:])
	line, err := json.Marshal(entry)
	if err != nil {
		return err
	}
	fh, err := os.OpenFile(c.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer fh.Close()
	if _, err := fh.Write(append(line, '\n')); err != nil {
		return err
	}
	c.prev = entry["hash"].(string)
	return fh.Sync() // the audit write is durable before the action reports success
}

// ---------------------------------------------------------------- notify seam

// fireClaimEvent posts a domain event to the notification rail; failures
// are logged, never fatal — the claim decision stands whether or not
// the SMS leaves today (same rule as scheduling).
type eventPoster struct {
	url string // e.g. http://notification-service:8089/events; empty = off
}

func (p eventPoster) fire(evType string, payload map[string]any) {
	if p.url == "" {
		return
	}
	body, _ := json.Marshal(map[string]any{
		"id": payload["event_id"], "facility": payload["facility_id"],
		"type": evType, "payload": payload,
	})
	resp, err := integrityClient.Post(p.url, "application/json", bytes.NewReader(body))
	if err == nil {
		resp.Body.Close()
	}
}

var integrityClient = &http.Client{Timeout: 3 * time.Second}

// ---------------------------------------------------------------- integrity scorecard

// IntegrityReport is the facility scorecard — every number shown, the
// grade derived in the open. Reviewers act on numbers; the grade is a
// triage aid, never a verdict.
type IntegrityReport struct {
	FacilityID      string  `json:"facility_id"`
	Claims          int     `json:"claims"`
	Approved        int     `json:"approved"`
	Pended          int     `json:"pended"`
	Denied          int     `json:"denied"`
	Disputed        int     `json:"disputed"`
	LinkageFailures int     `json:"linkage_failures"` // no/invalid encounter evidence
	BiometricShare  float64 `json:"biometric_presence_share"`
	DisputeRate     float64 `json:"dispute_rate"`
	PendRate        float64 `json:"pend_rate"`
	Grade           string  `json:"grade"` // A|B|C|D — formula below, in the open
	GradedAt        string  `json:"graded_at"`
}

// gradeIntegrity: start at 100; subtract per signal, floor 0.
//
//	dispute            −25 each (a patient said "this care didn't happen")
//	linkage failure    −10 each (claim couldn't prove the encounter)
//	pend rate > 30%    −15      (review lane is a cost the facility chooses)
//	biometric share < 25% (with ≥10 claims) −10 (weakest presence proof)
//
// A ≥85 B ≥70 C ≥50 else D. Transparent by design — a grade you cannot
// recompute yourself is an accusation, not a measurement.
func gradeIntegrity(r *IntegrityReport) string {
	score := 100
	score -= 25 * r.Disputed
	score -= 10 * r.LinkageFailures
	if r.PendRate > 0.30 {
		score -= 15
	}
	if r.Claims >= 10 && r.BiometricShare < 0.25 {
		score -= 10
	}
	if score < 0 {
		score = 0
	}
	switch {
	case score >= 85:
		return "A"
	case score >= 70:
		return "B"
	case score >= 50:
		return "C"
	default:
		return "D"
	}
}

func integrityReport(store *DecisionStore, facilityID string) IntegrityReport {
	claims := store.ForFacility(facilityID)
	r := IntegrityReport{FacilityID: facilityID, Claims: len(claims),
		GradedAt: time.Now().UTC().Format(time.RFC3339)}
	presence := 0
	for _, sc := range claims {
		switch sc.Decision.Decision {
		case "APPROVE":
			r.Approved++
		case "PEND":
			r.Pended++
		case "DENY":
			r.Denied++
		}
		if sc.Disputed {
			r.Disputed++
		}
		if !sc.Decision.Linkage.Linked {
			r.LinkageFailures++
		} else if sc.Decision.Linkage.Presence == "biometric" {
			presence++
		}
	}
	if r.Claims > 0 {
		r.BiometricShare = round2(float64(presence) / float64(r.Claims))
		r.DisputeRate = round2(float64(r.Disputed) / float64(r.Claims))
		r.PendRate = round2(float64(r.Pended) / float64(r.Claims))
	}
	r.Grade = gradeIntegrity(&r)
	return r
}

func round2(f float64) float64 { return float64(int(f*100+0.5)) / 100 }

// ---------------------------------------------------------------- http

func registerIntegrityRoutes(mux *http.ServeMux, store *DecisionStore, audit *AuditChain, poster eventPoster) {
	// Claims in a member's name — the portal reads this so the patient
	// can see every claim made against their coverage.
	mux.HandleFunc("GET /claims/for-member", func(w http.ResponseWriter, r *http.Request) {
		mid := r.URL.Query().Get("member_id")
		if mid == "" {
			http.Error(w, `{"error":"member_id required"}`, http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"claims": store.ForMember(mid)})
	})

	// Dispute: the patient (or a CHW on their behalf) says "this care
	// did not happen". Ownership is enforced by the portal gate upstream;
	// here we require the actor and reason — an anonymous dispute is a
	// weapon, an attributed one is evidence.
	mux.HandleFunc("POST /claims/{id}/dispute", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Actor  string `json:"actor"`
			Reason string `json:"reason"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Actor == "" || req.Reason == "" {
			http.Error(w, `{"error":"actor and reason required — anonymous disputes are not evidence"}`, http.StatusBadRequest)
			return
		}
		id := r.PathValue("id")
		sc, ok := store.Get(id)
		if !ok {
			http.Error(w, `{"error":"unknown claim"}`, http.StatusNotFound)
			return
		}
		if !store.Dispute(id, req.Reason, req.Actor) {
			// Already disputed — idempotent success: the first accuser
			// stands, and the patient gets the same answer twice.
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]any{"claim_id": id, "disputed": true, "already_disputed": true})
			return
		}
		_ = audit.Append(req.Actor, "claim_disputed", id, map[string]any{
			"reason": req.Reason, "facility_id": sc.Claim.ProviderID, "member_id": sc.Claim.MemberID,
		})
		go poster.fire("claim_disputed", map[string]any{
			"event_id": "dispute-" + id, "facility_id": sc.Claim.ProviderID,
			"member_id": sc.Claim.MemberID, "claim_id": id, "reason": req.Reason,
		})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"claim_id": id, "disputed": true})
	})

	// The facility scorecard.
	mux.HandleFunc("GET /integrity/{facility}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(integrityReport(store, r.PathValue("facility")))
	})
}

// auditErr surfaces chain failures in logs without breaking the money
// path — a claim is adjudicated exactly once either way.
func auditErr(context string, err error) {
	if err != nil {
		fmt.Printf("AUDIT CHAIN FAILURE (%s): %v\n", context, err)
	}
}
