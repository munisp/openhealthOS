// cds-service — CDS Hooks (HL7 spec) alert bus (ADR-0056). Every rules engine
// on the platform (CPOE interactions, immunization defaulters, fraud flags)
// publishes hooks here; UIs call one endpoint per workflow event instead of
// knowing each engine. Doctrine: fail-soft — a failing provider yields an
// empty card list plus a system warning, never a blocked workflow; allergy
// findings are the exception and surface as blocking indicator cards so the
// UI can fail-closed on them.
package main

import (
	"bytes"
	"fmt"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/exec"
	"time"
)

type Card struct {
	Summary    string `json:"summary"`
	Detail     string `json:"detail,omitempty"`
	Indicator  string `json:"indicator"` // info | warning | critical (blocking)
	Source     string `json:"source"`
	Suggestion string `json:"suggestion,omitempty"`
}

type HookResponse struct {
	Cards    []Card   `json:"cards"`
	Warnings []string `json:"warnings,omitempty"`
}

type HookService struct {
	ID          string `json:"id"`
	Hook        string `json:"hook"` // patient-view | order-select
	Title       string `json:"title"`
	Description string `json:"description"`
}

type Provider func(ctx map[string]any) ([]Card, error)

type Server struct {
	providers map[string]Provider // service id -> provider
	timeout   time.Duration
	now       func() time.Time
}

func runJSON(timeout time.Duration, script string, args []string, in any) ([]byte, error) {
	body, _ := json.Marshal(in)
	cmd := exec.Command("python3", append([]string{script}, args...)...)
	cmd.Stdin = bytes.NewReader(body)
	done := make(chan error, 1)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			return nil, err
		}
		return out.Bytes(), nil
	case <-time.After(timeout):
		cmd.Process.Kill()
		return nil, os.ErrDeadlineExceeded
	}
}

// ---- providers ----

// order-select: screen candidate meds via packages/cpoe (allergy => critical
// blocking card; interactions/dose => warning cards).
func (s *Server) cpoeProvider(ctx map[string]any) ([]Card, error) {
	in := map[string]any{
		"code":          ctx["drugCode"],
		"allergies":     ctx["allergies"],
		"active":        ctx["activeMedications"],
		"dose_mg":       ctx["doseMg"],
		"doses_per_day": ctx["dosesPerDay"],
	}
	raw, err := runJSON(s.timeout, "../../packages/cpoe/orders.py", []string{"screen"}, in)
	if err != nil {
		return nil, err
	}
	var out struct {
		OK       bool `json:"ok"`
		Block    string `json:"block"`
		Findings []struct {
			Severity string `json:"severity"`
			Message  string `json:"message"`
		} `json:"findings"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	var cards []Card
	if !out.OK {
		cards = append(cards, Card{Summary: "Prescription blocked: allergy conflict",
			Detail: out.Block, Indicator: "critical", Source: "cpoe/formulary"})
	}
	for _, f := range out.Findings {
		ind := "info"
		if f.Severity == "major" || f.Severity == "contraindicated" {
			ind = "warning"
		}
		cards = append(cards, Card{Summary: f.Message, Indicator: ind,
			Source: "cpoe/formulary", Suggestion: "Override requires a recorded reason"})
	}
	return cards, nil
}

// patient-view: immunization defaulter status via packages/immig. The immig
// CLI takes flags, so history is staged to a temp JSONL file (subprocess
// doctrine: Python stays off the hot path, 2s timeout, fail-soft).
func (s *Server) immigProvider(ctx map[string]any) ([]Card, error) {
	birth, _ := ctx["birthDate"].(string)
	if birth == "" {
		return nil, fmt.Errorf("context.birthDate required")
	}
	args := []string{"defaulters", "--birth-date", birth}
	if hist, ok := ctx["immunizationHistory"].([]any); ok && len(hist) > 0 {
		f, err := os.CreateTemp("", "immig-hist-*.jsonl")
		if err != nil {
			return nil, err
		}
		defer os.Remove(f.Name())
		for _, row := range hist {
			b, _ := json.Marshal(row)
			f.Write(append(b, '\n'))
		}
		f.Close()
		args = append(args, "--history", f.Name())
	}
	raw, err := runJSON(s.timeout, "../../packages/immig/immig.py", args, map[string]any{})
	if err != nil {
		return nil, err
	}
	var out struct {
		Lines []struct {
			Antigen  string `json:"antigen"`
			Status   string `json:"status"`
			Due      string `json:"due"`
			DaysLate int    `json:"days_late"`
		} `json:"lines"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	var cards []Card
	for _, d := range out.Lines {
		if d.Status != "DUE" && d.Status != "OVERDUE" {
			continue
		}
		ind := "info"
		summary := "Immunization due: " + d.Antigen + " (due " + d.Due + ")"
		if d.Status == "OVERDUE" {
			ind = "warning"
			summary = "Immunization OVERDUE: " + d.Antigen
		}
		cards = append(cards, Card{Summary: summary, Indicator: ind, Source: "immig/EPI",
			Suggestion: "Offer at this visit if no contraindication"})
	}
	return cards, nil
}

func (s *Server) services() []HookService {
	return []HookService{
		{ID: "cpoe-order-screen", Hook: "order-select", Title: "Prescription safety screen",
			Description: "Allergy (blocking), interaction and dose-range checks from the open formulary"},
		{ID: "immig-defaulter-view", Hook: "patient-view", Title: "Immunization status",
			Description: "NPHCDA EPI schedule due/overdue cards"},
	}
}

func (s *Server) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /cds-services", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"services": s.services()})
	})
	mux.HandleFunc("POST /cds-services/{id}", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		prov, ok := s.providers[id]
		if !ok {
			http.Error(w, `{"error":"unknown service"}`, http.StatusNotFound)
			return
		}
		var body struct {
			Context map[string]any `json:"context"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, `{"error":"bad request"}`, http.StatusBadRequest)
			return
		}
		cards, err := prov(body.Context)
		resp := HookResponse{Cards: cards}
		if err != nil { // fail-soft: honest warning, empty cards
			resp.Cards = []Card{}
			resp.Warnings = []string{"provider " + id + " unavailable: " + err.Error()}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"ok":true}`))
	})
}

func main() {
	s := &Server{timeout: 2 * time.Second, now: time.Now, providers: map[string]Provider{}}
	s.providers["cpoe-order-screen"] = s.cpoeProvider
	s.providers["immig-defaulter-view"] = s.immigProvider
	mux := http.NewServeMux()
	s.routes(mux)
	port := os.Getenv("PORT")
	if port == "" {
		port = "8110"
	}
	log.Printf("cds-service listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
