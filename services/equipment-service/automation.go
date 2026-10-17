// Telemetry automation — the infrastructure layer acting on itself
// (module 9): a cold-chain breach or dying battery opens a maintenance
// ticket without waiting for a human to notice. The platform knows which
// PHC is dark, dry, or hot *before* a vaccine dies.
//
// Wiring: the telemetry collector POSTs readings here; thresholds are the
// clinical/operational limits, not preferences. Duplicate suppression: one
// open AUTO ticket per equipment+metric; recovery auto-resolves it.
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

// Reading is one telemetry sample from the edge.
type Reading struct {
	EquipmentID string  `json:"equipment_id"`
	Metric      string  `json:"metric"` // cold_chain_temp | power_soc | water_level
	Value       float64 `json:"value"`
	TS          string  `json:"ts,omitempty"`
}

// ThresholdRule decides breach and ticket shape for one metric.
type ThresholdRule struct {
	Breach   func(v float64) bool
	Normal   func(v float64) bool // recovery condition (hysteresis gap)
	Priority string
	Issue    func(v float64) string
}

// Thresholds are clinical/engineering limits, not tunables:
//   - cold chain: WHO 2–8 °C — above 8 °C vaccines start dying; below 6 °C
//     again we consider the excursion over (2 °C hysteresis, no flapping)
//   - power: solar state-of-charge under 20 % risks the 48 h autonomy spec
//   - water: tank under 10 % means today's clinic runs dry
var Thresholds = map[string]ThresholdRule{
	"cold_chain_temp": {
		Breach:   func(v float64) bool { return v > 8.0 },
		Normal:   func(v float64) bool { return v < 6.0 },
		Priority: "P1", // safety-critical: vaccine potency
		Issue:    func(v float64) string { return fmt.Sprintf("AUTO: cold chain %.1f°C above 8°C limit — vaccines at risk", v) },
	},
	"power_soc": {
		Breach:   func(v float64) bool { return v < 20.0 },
		Normal:   func(v float64) bool { return v > 30.0 },
		Priority: "P2", // care-blocking
		Issue:    func(v float64) string { return fmt.Sprintf("AUTO: battery SOC %.0f%% below 20%% — 48h autonomy at risk", v) },
	},
	"water_level": {
		Breach:   func(v float64) bool { return v < 10.0 },
		Normal:   func(v float64) bool { return v > 20.0 },
		Priority: "P2", // care-blocking
		Issue:    func(v float64) string { return fmt.Sprintf("AUTO: water tank %.0f%% — below 10%% reserve", v) },
	},
}

// AutomationResult reports what a reading triggered.
type AutomationResult struct {
	Action string  `json:"action"` // opened | resolved | none | ignored
	Ticket *Ticket `json:"ticket,omitempty"`
	Reason string  `json:"reason,omitempty"`
}

func isAutoTicket(t Ticket, metric string) bool {
	return t.Status == "open" && strings.HasPrefix(t.Issue, "AUTO:") &&
		strings.Contains(strings.ToLower(t.Issue), metricKeyword(metric))
}

func metricKeyword(metric string) string {
	switch metric {
	case "cold_chain_temp":
		return "cold chain"
	case "power_soc":
		return "battery"
	case "water_level":
		return "water"
	}
	return metric
}

// EvaluateReading applies threshold automation for one reading.
func EvaluateReading(svc EquipmentService, r Reading) (AutomationResult, error) {
	rule, known := Thresholds[r.Metric]
	if _, err := svc.GetEquipment(r.EquipmentID); err != nil {
		return AutomationResult{}, err // ErrNotFound propagates — 404 upstream
	}
	if !known {
		return AutomationResult{Action: "ignored", Reason: "no threshold for metric"}, nil
	}
	if rule.Breach(r.Value) {
		for _, t := range svc.ListTickets("open") {
			if t.EquipmentID == r.EquipmentID && isAutoTicket(t, r.Metric) {
				return AutomationResult{Action: "none", Reason: "auto ticket already open", Ticket: &t}, nil
			}
		}
		t, err := svc.OpenTicket(r.EquipmentID, rule.Issue(r.Value), rule.Priority)
		if err != nil {
			return AutomationResult{}, err
		}
		return AutomationResult{Action: "opened", Ticket: &t}, nil
	}
	if rule.Normal(r.Value) {
		for _, t := range svc.ListTickets("open") {
			if t.EquipmentID == r.EquipmentID && isAutoTicket(t, r.Metric) {
				if err := svc.AutoResolve(t.ID); err != nil {
					return AutomationResult{}, err
				}
				return AutomationResult{Action: "resolved", Ticket: &t}, nil
			}
		}
	}
	return AutomationResult{Action: "none"}, nil
}

// readingRoute adds POST /telemetry/reading to the mux.
func readingRoute(mux *http.ServeMux, svc EquipmentService) {
	mux.HandleFunc("/telemetry/reading", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var rd Reading
		if err := json.NewDecoder(r.Body).Decode(&rd); err != nil || rd.EquipmentID == "" || rd.Metric == "" {
			http.Error(w, `{"error":"equipment_id, metric, value required"}`, http.StatusBadRequest)
			return
		}
		res, err := EvaluateReading(svc, rd)
		if err == ErrNotFound {
			http.Error(w, `{"error":"equipment not found"}`, http.StatusNotFound)
			return
		}
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, res)
	})
}
