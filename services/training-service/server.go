// HTTP surface for the training service. All state-changing endpoints
// that a regulator could dispute take an X-Actor header (audit doctrine).
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
)

// SubprocessLedger drives the Rust ledger seam over stdin/stdout JSON
// lines (claims-orchestrator doctrine: one-shot process per posting).
type SubprocessLedger struct {
	Binary string
}

func (l SubprocessLedger) run(ops string) (string, error) {
	cmd := exec.Command(l.Binary)
	cmd.Stdin = strings.NewReader(ops)
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// The ledger driver speaks OK:/ERR: text lines and refuses debits on
// empty accounts, so Post first opens both accounts (open on an existing
// account is a harmless ERR) with a float on the system side, then posts.
func (l SubprocessLedger) Post(debit, credit, tx string, amount int) (string, error) {
	opening := 0
	if strings.HasPrefix(debit, "system:") {
		opening = 1_000_000_000_000 // issuance float; debits draw it down
	}
	out, err := l.run(fmt.Sprintf(
		`{"op":"open","id":%q,"opening":%d}`+"\n"+
			`{"op":"open","id":%q,"opening":%d}`+"\n"+
			`{"op":"post","debit":%q,"credit":%q,"amount":%d,"tx":%q}`+"\n",
		debit, opening, credit, 0, debit, credit, amount, tx))
	if err != nil {
		return out, err
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	last := lines[len(lines)-1]
	if strings.HasPrefix(last, "ERR") {
		return out, fmt.Errorf("ledger: %s", last)
	}
	return out, nil
}

func (l SubprocessLedger) Balance(id string) (int, error) {
	out, err := l.run(fmt.Sprintf(
		`{"op":"open","id":%q,"opening":%d}`+"\n"+`{"op":"balance","id":%q}`+"\n",
		id, 0, id))
	if err != nil {
		return 0, err
	}
	// Reply line: `OK: <id> = <balance>`
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "OK:") && strings.Contains(line, "=") {
			var bal int
			if _, err := fmt.Sscanf(line[strings.LastIndex(line, "=")+1:], "%d", &bal); err == nil {
				return bal, nil
			}
		}
	}
	return 0, fmt.Errorf("ledger reply: %s", strings.TrimSpace(out))
}

type server struct {
	svc *TrainingService
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, err error) {
	code := http.StatusUnprocessableEntity
	if errors.Is(err, ErrNotFound) {
		code = http.StatusNotFound
	}
	writeJSON(w, code, map[string]string{"error": err.Error()})
}

func actor(r *http.Request) string { return r.Header.Get("X-Actor") }

func (s *server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /courses", s.addCourse)
	mux.HandleFunc("GET /courses/{id}", s.getCourse)
	mux.HandleFunc("POST /enrolments", s.enrol)
	mux.HandleFunc("GET /enrolments/{id}", s.getEnrolment)
	mux.HandleFunc("POST /enrolments/{id}/modules/{mid}/done", s.markDone)
	mux.HandleFunc("POST /enrolments/{id}/modules/{mid}/quiz", s.submitQuiz)
	mux.HandleFunc("POST /enrolments/{id}/modules/{mid}/sign", s.signChecklist)
	mux.HandleFunc("POST /enrolments/{id}/certificate", s.issueCertificate)
	mux.HandleFunc("GET /certificates/{serial}/verify", s.verify)
	mux.HandleFunc("POST /certificates/{serial}/revoke", s.revoke)
	mux.HandleFunc("GET /practitioners/{id}/cpd", s.cpd)
	mux.HandleFunc("POST /guidelines/{course}/bump", s.bumpGuideline)
	mux.HandleFunc("GET /facilities/{id}/attestation-gap", s.attestationGap)
	mux.HandleFunc("GET /ivr/quiz/{enrolment}/{module}", s.ivrQuiz)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func (s *server) addCourse(w http.ResponseWriter, r *http.Request) {
	var c Course
	if err := json.NewDecoder(r.Body).Decode(&c); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	if err := s.svc.AddCourse(c); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 201, map[string]string{"course": c.ID, "version": c.Version})
}

func (s *server) getCourse(w http.ResponseWriter, r *http.Request) {
	c, ok := s.svc.GetCourse(r.PathValue("id"))
	if !ok {
		writeJSON(w, 404, map[string]string{"error": "course not found"})
		return
	}
	writeJSON(w, 200, c)
}

func (s *server) enrol(w http.ResponseWriter, r *http.Request) {
	var req struct {
		CourseID     string `json:"course_id"`
		Practitioner string `json:"practitioner_id"`
		Facility     string `json:"facility_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	e, err := s.svc.Enrol(req.CourseID, req.Practitioner, req.Facility)
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 201, e)
}

func (s *server) getEnrolment(w http.ResponseWriter, r *http.Request) {
	s.svc.mu.Lock()
	e, ok := s.svc.enrols[r.PathValue("id")]
	s.svc.mu.Unlock()
	if !ok {
		writeJSON(w, 404, map[string]string{"error": "enrolment not found"})
		return
	}
	writeJSON(w, 200, e)
}

func (s *server) markDone(w http.ResponseWriter, r *http.Request) {
	if err := s.svc.MarkDone(r.PathValue("id"), r.PathValue("mid")); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, map[string]string{"status": "done"})
}

func (s *server) submitQuiz(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Answers []int `json:"answers"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	score, err := s.svc.SubmitQuiz(r.PathValue("id"), r.PathValue("mid"), req.Answers)
	if err != nil {
		writeJSON(w, 422, map[string]any{"error": err.Error(), "score": score})
		return
	}
	writeJSON(w, 200, map[string]any{"status": "passed", "score": score})
}

func (s *server) signChecklist(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Item string `json:"item"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	if err := s.svc.SignChecklist(r.PathValue("id"), r.PathValue("mid"), req.Item, actor(r)); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, map[string]string{"status": "signed"})
}

func (s *server) issueCertificate(w http.ResponseWriter, r *http.Request) {
	cert, err := s.svc.IssueCertificate(r.PathValue("id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 201, cert)
}

func (s *server) verify(w http.ResponseWriter, r *http.Request) {
	v, err := s.svc.Verify(r.PathValue("serial"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, v)
}

func (s *server) revoke(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Reason string `json:"reason"`
	}
	json.NewDecoder(r.Body).Decode(&req)
	cert, err := s.svc.Revoke(r.PathValue("serial"), actor(r), req.Reason)
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, cert)
}

func (s *server) cpd(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, s.svc.CPDSummary(r.PathValue("id"), r.URL.Query().Get("scheme")))
}

func (s *server) bumpGuideline(w http.ResponseWriter, r *http.Request) {
	var req struct {
		GuidelineVersion string `json:"guideline_version"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.GuidelineVersion == "" {
		writeJSON(w, 400, map[string]string{"error": "guideline_version is required"})
		return
	}
	flagged, err := s.svc.BumpGuideline(r.PathValue("course"), req.GuidelineVersion, actor(r))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{"flagged": flagged})
}

func (s *server) attestationGap(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"gaps": s.svc.AttestationGap(r.PathValue("id"))})
}

func (s *server) ivrQuiz(w http.ResponseWriter, r *http.Request) {
	q, err := s.svc.IVRQuiz(r.PathValue("enrolment"), r.PathValue("module"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, 200, q)
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	ledger := SubprocessLedger{Binary: env("LEDGER_BIN", "../ledger/target/release/oh_ledger")}
	srv := &server{svc: NewTrainingService(ledger)}
	addr := env("LISTEN", ":8097")
	log.Printf("OpenHealth OS training service on %s (ledger=%s)", addr, ledger.Binary)
	log.Fatal(http.ListenAndServe(addr, srv.routes()))
}
