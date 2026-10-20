// recommend.go — smart slot recommender (innovation 4).
//
// A patient in a network-constrained state should not have to phone three
// facilities to find one with a free slot. The recommender ranks facilities
// by earliest free slot for the requested service, breaking ties by load
// (booked/capacity across the window) — so a full day at a big facility
// never hides a free morning at the small one down the road.
//
// Used by the patient app (care tab), the WhatsApp/Matrix booking dialogues
// and USSD: one ranked answer instead of a per-facility scavenger hunt.
package main

import (
	"net/http"
	"sort"
	"strings"
)

// Recommendation is one facility's ranked offer.
type Recommendation struct {
	FacilityID string  `json:"facility_id"`
	NextFree   string  `json:"next_free"`  // RFC3339 of the earliest bookable slot
	FreeSlots  int     `json:"free_slots"` // in the window
	Load       float64 `json:"load"`       // booked/capacity across the window, 0..1
}

// recommend ranks facilities for a service+window. facilities=[] means all
// facilities with schedules (the common patient case).
func recommend(store Store, service, from, to string, facilities []string) []Recommendation {
	if len(facilities) == 0 {
		facilities = store.FacilityIDs()
	}
	out := []Recommendation{}
	for _, fid := range facilities {
		slots := store.Slots(fid, service, from, to)
		if len(slots) == 0 {
			continue
		}
		var cap, booked, free int
		nextFree := ""
		for _, sl := range slots {
			cap += sl.Capacity
			booked += sl.Booked
			if sl.Booked < sl.Capacity {
				free++
				if nextFree == "" || sl.Start < nextFree {
					nextFree = sl.Start
				}
			}
		}
		if free == 0 {
			continue // a facility with nothing free is not a recommendation
		}
		load := 0.0
		if cap > 0 {
			load = float64(booked) / float64(cap)
		}
		out = append(out, Recommendation{FacilityID: fid, NextFree: nextFree, FreeSlots: free, Load: load})
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].NextFree != out[j].NextFree {
			return out[i].NextFree < out[j].NextFree // earliest care first
		}
		return out[i].Load < out[j].Load // then the quieter facility
	})
	return out
}

func (s server) recommendSlots(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	service := q.Get("service")
	if service == "" {
		http.Error(w, `{"error":"service required"}`, http.StatusBadRequest)
		return
	}
	from, to := q.Get("from"), q.Get("to")
	if from == "" || to == "" {
		http.Error(w, `{"error":"from and to (RFC3339 or YYYY-MM-DD) required"}`, http.StatusBadRequest)
		return
	}
	var facilities []string
	if f := q.Get("facilities"); f != "" {
		for _, p := range strings.Split(f, ",") {
			if p = strings.TrimSpace(p); p != "" {
				facilities = append(facilities, p)
			}
		}
	}
	writeJSON(w, 200, map[string]any{
		"service":         service,
		"recommendations": recommend(s.store, service, from, to, facilities),
	})
}
