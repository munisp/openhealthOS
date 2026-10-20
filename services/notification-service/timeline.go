// timeline.go — Patient 360: the longitudinal timeline (innovation 1).
//
// One chronological feed of everything that has happened TO the patient
// across the platform: appointments booked, lab results delivered,
// prescriptions, imaging, vitals alerts, wellness nudges, consults. The
// data already flows through this service's FHIR inbox (ADR-0013) — the
// timeline is a VIEW over the notification journal plus the telehealth
// consult thread, merged and sorted, clinical summaries only (the detail
// lives behind /portal/record, which is itself elevated).
//
// PHI: the timeline is an elevated endpoint — step-up gate required
// (ADR-0039). Degradation doctrine as portal_record: a dark telehealth
// leg marks the feed incomplete, never 500s the phone.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"time"
)

// TimelineItem is one thing that happened, newest first.
type TimelineItem struct {
	TS      string `json:"ts"`
	Kind    string `json:"kind"`    // appointment | lab_result | prescription | imaging_result | vitals_danger | consult | ...
	Summary string `json:"summary"` // clinical-detail-free one-liner
	State   string `json:"state,omitempty"`
	Source  string `json:"source"` // notifications | telehealth
}

// PortalTimeline is the merged feed.
type PortalTimeline struct {
	PatientID   string         `json:"patient_id"`
	GeneratedAt string         `json:"generated_at"`
	Complete    bool           `json:"complete"`
	Items      []TimelineItem `json:"items"`
}

// consultEntry is the minimal shape we read from the telehealth thread.
type consultEntry struct {
	CreatedAt string `json:"created_at"`
	Status    string `json:"status"`
	Reason    string `json:"reason"`
}

func (s server) buildTimeline(ctx context.Context, patientID string) PortalTimeline {
	tl := PortalTimeline{
		PatientID:   patientID,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Complete:    true,
		Items:       []TimelineItem{},
	}
	for _, n := range s.svc.store.ForPatient(patientID) {
		tl.Items = append(tl.Items, TimelineItem{
			TS: n.CreatedAt, Kind: n.Kind, Summary: n.Text, State: n.State, Source: "notifications",
		})
	}
	// Telehealth leg: the consult thread, same fail-soft pattern as
	// portal_record.
	url := telehealthBase() + "/consults?patient_id=" + patientID
	raw, ok := fetchPortalLeg(ctx, url, map[string]string{"X-Patient-Self": patientID}, json.RawMessage(`[]`))
	if !ok {
		tl.Complete = false
	} else {
		var consults []consultEntry
		if json.Unmarshal(raw, &consults) == nil {
			for _, c := range consults {
				summary := "consult"
				if c.Reason != "" {
					summary = "consult: " + c.Reason
				}
				tl.Items = append(tl.Items, TimelineItem{
					TS: c.CreatedAt, Kind: "consult", Summary: summary, State: c.Status, Source: "telehealth",
				})
			}
		}
	}
	// Referral leg (innovation 9): the closed-loop tracker is where the
	// patient sees "referred → accepted → completed". Same fail-soft
	// doctrine — a dark referral-service degrades completeness, never
	// the feed.
	refURL := envOr("REFERRAL_URL", "http://127.0.0.1:8094") + "/referrals"
	raw, ok = fetchPortalLeg(ctx, refURL, nil, json.RawMessage(`{"referrals":[]}`))
	if !ok {
		tl.Complete = false
	} else {
		var out struct {
			Referrals []struct {
				PatientID string `json:"patient_id"`
				ToFacility string `json:"to_facility"`
				Status    string `json:"status"`
				CreatedAt string `json:"created_at"`
				UpdatedAt string `json:"updated_at"`
			} `json:"referrals"`
		}
		if json.Unmarshal(raw, &out) == nil {
			for _, r := range out.Referrals {
				if r.PatientID != patientID {
					continue
				}
				ts := r.UpdatedAt
				if ts == "" {
					ts = r.CreatedAt
				}
				tl.Items = append(tl.Items, TimelineItem{
					TS: ts, Kind: "referral",
					Summary: fmt.Sprintf("referral to %s", r.ToFacility),
					State:   r.Status, Source: "referrals",
				})
			}
		}
	}
	sort.SliceStable(tl.Items, func(i, j int) bool { return tl.Items[i].TS > tl.Items[j].TS })
	return tl
}

// registerTimeline adds GET /portal/timeline (elevated — ADR-0039 gate).
func registerTimeline(mux *http.ServeMux, srv server) {
	mux.HandleFunc("GET /portal/timeline", func(w http.ResponseWriter, r *http.Request) {
		patientID, ok := srv.requireElevation(w, r) // 404 on wrong pair, 428 step_up_required
		if !ok {
			return
		}
		writeJSON(w, http.StatusOK, srv.buildTimeline(r.Context(), patientID))
	})
}
