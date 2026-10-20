package main

// dosagetext bridge (ADR-0049): render a FHIR MedicationRequest's
// dosageInstruction into a patient-language line via the stdlib Python
// package. Same subprocess doctrine as the claims fraud gate
// (SubprocessFraudScorer): JSON over stdin, bounded timeout, and
// FAIL-SOFT — a missing renderer must never break the notification
// rail; the generic nudge is the fallback.
//
// DOSAGETEXT=off disables; DOSAGETEXT overrides the script path;
// DOSAGE_LANG picks the patient language (default en).

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func dosageScriptPath() string {
	if v := os.Getenv("DOSAGETEXT"); v != "" {
		return v
	}
	// Repo-relative default, same resolution as claims-orchestrator's
	// resolveToolPath: walk up from CWD looking for the package.
	for dir, _ := os.Getwd(); dir != "/" && dir != "."; dir = filepath.Dir(dir) {
		cand := filepath.Join(dir, "packages/dosagetext/dosagetext.py")
		if _, err := os.Stat(cand); err == nil {
			return cand
		}
	}
	return "../../packages/dosagetext/dosagetext.py"
}

// dosageText renders the first dosageInstruction of a MedicationRequest.
// "" on any failure (missing instruction, renderer absent, timeout).
func dosageText(medReq map[string]any) string {
	if os.Getenv("DOSAGETEXT") == "off" {
		return ""
	}
	inst, ok := medReq["dosageInstruction"].([]any)
	if !ok || len(inst) == 0 {
		return ""
	}
	body, err := json.Marshal(inst[0])
	if err != nil {
		return ""
	}
	lang := os.Getenv("DOSAGE_LANG")
	if lang == "" {
		lang = "en"
	}
	python := os.Getenv("PYTHON")
	if python == "" {
		python = "python3"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, dosageScriptPath(), "render", "-", "--lang", lang)
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.Output()
	if err != nil {
		log.Printf("dosagetext render failed (fail-soft, generic nudge stands): %v", err)
		return ""
	}
	return strings.TrimSpace(string(out))
}
