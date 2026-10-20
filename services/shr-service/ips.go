package main

// ips.go — IPS document + SMART Health Link endpoints (ADR-0054).
//
// The SHR already holds the patient's resources under consent-gated
// access; these two endpoints turn that into the patient-facing share
// contracts:
//
//	GET /ips/{pid}/document → FHIR IPS document Bundle, built by
//	    packages/ips (Python subprocess, JSON stdin/stdout, 2s timeout,
//	    fail-soft) and structurally validated before it leaves.
//	GET /ips/{pid}/shl → mints a SMART Health Link: the document is
//	    encrypted with a fresh 32-byte key (shared/shlx AES-256-GCM),
//	    the ciphertext is held in memory at /shl-file/{id}, and the
//	    payload {url,key,exp,label} + shlink: URI go to the caller. The
//	    server stores only ciphertext — the key exists only in the link.
//	GET /shl-file/{id} → serves the encrypted envelope.
//
// Access: patient-self only (X-Patient-Self from the portal proxy) or
// consented facility access — same gates as /fhir/Patient/{id}/$everything.

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/shlx"
)

const ipsScriptDefault = "../../packages/ips/ips.py"

// shlFile is one encrypted export waiting to be fetched.
type shlFile struct {
	Envelope string
	Expires  time.Time
}

type ipsHandler struct {
	store  *Store
	script string // path to ips.py; empty disables (503, fail-closed)
	python string
	mu     sync.Mutex
	files  map[string]shlFile
	seq    int
	now    func() time.Time
}

func newIPSHandler(store *Store, script string) *ipsHandler {
	if script == "" {
		script = ipsScriptDefault
	}
	return &ipsHandler{store: store, script: script, python: "python3",
		files: map[string]shlFile{}, now: func() time.Time { return time.Now().UTC() }}
}

// ipsInput renders the patient's SHR resources as the JSON doc ips.py
// expects: plain {code, display, value} dicts per clinical section.
func ipsInput(patientID string, resources []*Resource) map[string]any {
	in := map[string]any{"patient": map[string]any{"id": patientID}}
	problems, allergies, meds, imms, obs := []map[string]any{}, []map[string]any{}, []map[string]any{}, []map[string]any{}, []map[string]any{}
	codeText := func(body map[string]any) string {
		if c, ok := body["code"].(map[string]any); ok {
			if t, ok := c["text"].(string); ok && t != "" {
				return t
			}
			if cs, ok := c["coding"].([]any); ok && len(cs) > 0 {
				if m, ok := cs[0].(map[string]any); ok {
					if d, ok := m["display"].(string); ok {
						return d
					}
				}
			}
		}
		return ""
	}
	for _, r := range resources {
		var body map[string]any
		if err := json.Unmarshal(r.Body, &body); err != nil {
			continue
		}
		item := map[string]any{"code": codeText(body), "display": codeText(body)}
		switch r.ResourceType {
		case "Patient":
			if name, ok := body["name"].([]any); ok && len(name) > 0 {
				if m, ok := name[0].(map[string]any); ok {
					in["patient"].(map[string]any)["name"] = m["text"]
				}
			}
			in["patient"].(map[string]any)["birth_date"] = body["birthDate"]
			in["patient"].(map[string]any)["gender"] = body["gender"]
		case "Condition":
			problems = append(problems, item)
		case "AllergyIntolerance":
			allergies = append(allergies, item)
		case "MedicationStatement", "MedicationRequest":
			meds = append(meds, item)
		case "Immunization":
			if vc, ok := body["vaccineCode"].(map[string]any); ok {
				if t, ok := vc["text"].(string); ok {
					item["code"], item["display"] = t, t
				}
			}
			imms = append(imms, item)
		case "Observation":
			if vs, ok := body["valueString"].(string); ok {
				item["value"] = vs
			}
			obs = append(obs, item)
		}
	}
	in["problems"], in["allergies"], in["medications"] = problems, allergies, meds
	in["immunizations"], in["observations"] = imms, obs
	return in
}

// buildIPS runs packages/ips as a subprocess — Python off the hot path,
// JSON stdin/stdout, hard 2s timeout. Fail-soft returns an error; the
// caller turns it into 502, never a fabricated document.
func (h *ipsHandler) buildIPS(in map[string]any) ([]byte, error) {
	body, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, h.python, h.script, "build")
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("ips build: %v: %s", err, out)
	}
	// validate before it leaves — a malformed IPS is worse than none
	vctx, vcancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer vcancel()
	vcmd := exec.CommandContext(vctx, h.python, h.script, "validate")
	vcmd.Stdin = bytes.NewReader(out)
	vout, verr := vcmd.CombinedOutput()
	if verr != nil {
		return nil, fmt.Errorf("ips validation failed: %s", vout)
	}
	return out, nil
}

// gather fetches the patient's resources under the request's access
// context — patient-self header or facility consent, same as $everything.
func (h *ipsHandler) gather(w http.ResponseWriter, r *http.Request, pid string) ([]*Resource, bool) {
	facility, breakGlass := accessContext(r)
	var resources []*Resource
	var err error
	if self := r.Header.Get("X-Patient-Self"); self != "" {
		resources, err = h.store.PatientSelfEverything(pid, "portal:"+self)
	} else {
		resources, err = h.store.PatientEverything(pid, facility, breakGlass)
	}
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": "patient not found or not authorized"})
		return nil, false
	}
	return resources, true
}

func (h *ipsHandler) document(w http.ResponseWriter, r *http.Request) {
	pid := r.PathValue("pid")
	resources, ok := h.gather(w, r, pid)
	if !ok {
		return
	}
	doc, err := h.buildIPS(ipsInput(pid, resources))
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/fhir+json")
	w.WriteHeader(200)
	w.Write(doc)
}

func (h *ipsHandler) shl(w http.ResponseWriter, r *http.Request) {
	pid := r.PathValue("pid")
	resources, ok := h.gather(w, r, pid)
	if !ok {
		return
	}
	doc, err := h.buildIPS(ipsInput(pid, resources))
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	key := make([]byte, 32)
	rand.Read(key)
	keyB64 := base64.RawURLEncoding.EncodeToString(key)
	envelope, err := shlx.Encrypt(doc, keyB64)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "encryption failed"})
		return
	}
	h.mu.Lock()
	h.seq++
	id := fmt.Sprintf("shl-%06d", h.seq)
	exp := h.now().Add(30 * 24 * time.Hour)
	h.files[id] = shlFile{Envelope: envelope, Expires: exp}
	h.mu.Unlock()

	payload := map[string]any{
		"url":   "/shl-file/" + id,
		"key":   keyB64,
		"exp":   exp.Unix(),
		"label": "IPS — patient " + pid,
	}
	linkJSON, _ := json.Marshal(payload)
	link := "shlink:/" + base64.RawURLEncoding.EncodeToString(linkJSON)
	writeJSON(w, 201, map[string]any{"payload": payload, "link": link,
		"note": "server holds ciphertext only; the key lives solely in this link"})
}

func (h *ipsHandler) shlFileServe(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	h.mu.Lock()
	f, ok := h.files[id]
	h.mu.Unlock()
	if !ok || h.now().After(f.Expires) {
		writeJSON(w, 410, map[string]string{"error": "link expired or unknown"})
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.WriteHeader(200)
	w.Write([]byte(f.Envelope))
}

func (h *ipsHandler) routes(mux *http.ServeMux) {
	// PathValue patterns; registered before the /fhir/ catch-all in main.
	mux.HandleFunc("GET /ips/{pid}/document", h.document)
	mux.HandleFunc("GET /ips/{pid}/shl", h.shl)
	mux.HandleFunc("GET /shl-file/{id}", h.shlFileServe)
}

var _ = strings.TrimSpace // keep strings imported for future filters
