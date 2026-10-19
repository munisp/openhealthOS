// HTTP surface for the imaging service (ADR-0028). DICOMweb requests proxy
// to the composed Orthanc PACS; worklist, export planning and video jobs are
// owned here.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

type server struct {
	pacs     *PACSClient
	worklist *Worklist
	videos   *VideoQueue
	now      func() time.Time
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok"})
	})
	// DICOMweb facade → Orthanc (composed, GPLv3, never linked).
	mux.HandleFunc("GET /dicomweb/studies", s.qidoStudies)
	mux.HandleFunc("GET /dicomweb/studies/{uid}/series", s.qidoSeries)
	mux.HandleFunc("GET /dicomweb/studies/{uid}", s.wadoStudy)
	mux.HandleFunc("POST /dicomweb/studies", s.stowStudy)
	// Worklist + triage.
	mux.HandleFunc("GET /worklist", s.listWorklist)
	mux.HandleFunc("POST /worklist/register", s.registerStudy) // PACS webhook
	mux.HandleFunc("POST /worklist/{uid}/priority", s.prioritize)
	mux.HandleFunc("POST /worklist/{uid}/sign", s.sign)
	// De-identified export planning.
	mux.HandleFunc("POST /studies/{uid}/export-plan", s.exportPlan)
	// Video transcode jobs → rsmpeg worker.
	mux.HandleFunc("POST /videos", s.enqueueVideo)
	mux.HandleFunc("GET /videos/{id}", s.videoStatus)
	mux.HandleFunc("POST /videos/{id}/update", s.updateVideo) // worker callback
}

// --------------------------------------------------------------- PACS seam

// PACSClient is the narrow DICOMweb waist to Orthanc. Tests substitute a
// fake; production points at the composed container (ORTHANC_URL).
type PACSClient struct {
	base   string
	client *http.Client
}

func NewPACSClient(base string) *PACSClient {
	return &PACSClient{base: strings.TrimSuffix(base, "/"), client: &http.Client{Timeout: 30 * time.Second}}
}

// proxy forwards method+path+body to the PACS and streams the response back.
func (p *PACSClient) proxy(w http.ResponseWriter, r *http.Request, path string) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad body"})
		return
	}
	req, err := http.NewRequest(r.Method, p.base+path, bytes.NewReader(body))
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	if ct := r.Header.Get("Content-Type"); ct != "" {
		req.Header.Set("Content-Type", ct)
	}
	if accept := r.Header.Get("Accept"); accept != "" {
		req.Header.Set("Accept", accept)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "PACS unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	for _, h := range []string{"Content-Type"} {
		if v := resp.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)
}

func (s server) qidoStudies(w http.ResponseWriter, r *http.Request) {
	q := r.URL.RawQuery
	if q != "" {
		q = "?" + q
	}
	s.pacs.proxy(w, r, "/dicom-web/studies"+q)
}

func (s server) qidoSeries(w http.ResponseWriter, r *http.Request) {
	s.pacs.proxy(w, r, "/dicom-web/studies/"+r.PathValue("uid")+"/series")
}

// wadoStudy streams rendered instances for the Cornerstone viewer.
func (s server) wadoStudy(w http.ResponseWriter, r *http.Request) {
	s.pacs.proxy(w, r, "/dicom-web/studies/"+r.PathValue("uid"))
}

// stowStudy ingests DICOM (STOW-RS) then registers the study on the
// worklist when the client supplies metadata headers — the PACS webhook is
// the primary path; this header fallback keeps single-node pilots simple.
func (s server) stowStudy(w http.ResponseWriter, r *http.Request) {
	modality := r.Header.Get("X-Modality")
	desc := r.Header.Get("X-Study-Description")
	s.pacs.proxy(w, r, "/dicom-web/studies")
	if modality != "" {
		s.worklist.Register(Study{
			StudyUID:    r.Header.Get("X-Study-UID"),
			PatientID:   r.Header.Get("X-Patient-ID"),
			Modality:    modality,
			Description: desc,
			StudyDate:   r.Header.Get("X-Study-Date"),
		})
	}
}

// -------------------------------------------------------------- worklist

func (s server) listWorklist(w http.ResponseWriter, r *http.Request) {
	overdue := r.URL.Query().Get("overdue") == "true"
	writeJSON(w, 200, map[string]any{"studies": s.worklist.List(overdue, s.now())})
}

// registerStudy is called by the PACS on new-study events.
func (s server) registerStudy(w http.ResponseWriter, r *http.Request) {
	var st Study
	if err := json.NewDecoder(r.Body).Decode(&st); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if st.StudyUID == "" || st.Modality == "" {
		writeJSON(w, 422, map[string]string{"error": "study_uid and modality are required"})
		return
	}
	s.worklist.Register(st)
	got, _ := s.findStudy(st.StudyUID)
	writeJSON(w, 201, got)
}

func (s server) findStudy(uid string) (Study, bool) {
	for _, st := range s.worklist.List(false, s.now()) {
		if st.StudyUID == uid {
			return st, true
		}
	}
	return Study{}, false
}

func (s server) prioritize(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Level  string `json:"level"`
		Reason string `json:"reason"`
		Actor  string `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if err := s.worklist.Prioritize(r.PathValue("uid"), req.Level, req.Reason, req.Actor); err != nil {
		code := 422
		if errors.Is(err, ErrStudyNotFound) {
			code = 404
		}
		writeJSON(w, code, map[string]string{"error": err.Error()})
		return
	}
	got, _ := s.findStudy(r.PathValue("uid"))
	writeJSON(w, 200, got)
}

func (s server) sign(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Radiologist string `json:"radiologist"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if err := s.worklist.Sign(r.PathValue("uid"), req.Radiologist); err != nil {
		code := 422
		if errors.Is(err, ErrStudyNotFound) {
			code = 404
		}
		writeJSON(w, code, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"status": "read"})
}

// ----------------------------------------------------------- de-id export

func (s server) exportPlan(w http.ResponseWriter, r *http.Request) {
	var req struct {
		CohortSize int    `json:"cohort_size"`
		Pseudonym  string `json:"pseudonym"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	st, ok := s.findStudy(r.PathValue("uid"))
	if !ok {
		writeJSON(w, 404, map[string]string{"error": ErrStudyNotFound.Error()})
		return
	}
	plan, err := PlanDeID(st, req.CohortSize, req.Pseudonym)
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, plan)
}

// ------------------------------------------------------------ video jobs

func (s server) enqueueVideo(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SourceRef string `json:"source_ref"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	j, err := s.videos.Enqueue(req.SourceRef, r.Header.Get("X-Actor"))
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 201, j)
}

func (s server) videoStatus(w http.ResponseWriter, r *http.Request) {
	j, err := s.videos.Get(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, j)
}

// updateVideo: the rsmpeg worker reports status and outputs here.
func (s server) updateVideo(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Status      string   `json:"status"`
		Error       string   `json:"error"`
		ManifestRef string   `json:"manifest_ref"`
		SegmentRefs []string `json:"segment_refs"`
		Renditions  []int    `json:"renditions"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	if err := s.videos.Update(r.PathValue("id"), req.Status, req.Error, req.ManifestRef, req.SegmentRefs, req.Renditions); err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	j, _ := s.videos.Get(r.PathValue("id"))
	writeJSON(w, 200, j)
}

func main() {
	listen := os.Getenv("LISTEN")
	if listen == "" {
		listen = ":8096"
	}
	orthanc := os.Getenv("ORTHANC_URL")
	if orthanc == "" {
		orthanc = "http://orthanc:8042"
	}
	s := server{
		pacs:     NewPACSClient(orthanc),
		worklist: NewWorklist(),
		videos:   NewVideoQueue(nil), // queue-dir sink wired in deploy
		now:      time.Now,
	}
	mux := http.NewServeMux()
	s.routes(mux)
	if err := http.ListenAndServe(listen, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		panic(err)
	}
}
