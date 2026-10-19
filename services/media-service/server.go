// HTTP surface for the media blob store (ADR-0027).
package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
)

type server struct {
	store   BlobStore
	uploads *UploadManager
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
	mux.HandleFunc("POST /blobs", s.upload)
	mux.HandleFunc("GET /blobs/{id}", s.download)
	mux.HandleFunc("HEAD /blobs/{id}", s.stat)
	mux.HandleFunc("GET /blobs/{id}/meta", s.meta)
	mux.HandleFunc("POST /blobs/{id}/delete", s.tombstone)
	// Multipart sessions (ADR-0028): chunk uploads up to 2 GiB.
	mux.HandleFunc("POST /uploads", s.initiateUpload)
	mux.HandleFunc("PUT /uploads/{id}/parts/{n}", s.putPart)
	mux.HandleFunc("POST /uploads/{id}/complete", s.completeUpload)
	mux.HandleFunc("POST /uploads/{id}/abort", s.abortUpload)
}

// initiateUpload: {"content_type": "...", "size": bytes} + X-Actor.
func (s server) initiateUpload(w http.ResponseWriter, r *http.Request) {
	actor := r.Header.Get("X-Actor")
	var req struct {
		ContentType string `json:"content_type"`
		Size        int64  `json:"size"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	sess, err := s.uploads.Initiate(req.ContentType, req.Size, actor)
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 201, sess)
}

// putPart: raw chunk body; returns the part's SHA-256 for the client log.
func (s server) putPart(w http.ResponseWriter, r *http.Request) {
	n, err := ParsePartNumber(r.PathValue("n"))
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, MaxUploadBytes/MaxUploadParts+1<<20)
	data, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, 413, map[string]string{"error": "part too large"})
		return
	}
	hexsum, err := s.uploads.PutPart(r.PathValue("id"), n, data)
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"sha256": hexsum})
}

// completeUpload assembles parts, adopts the object, and cleans up.
func (s server) completeUpload(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	sess, ok := s.uploads.sessions[id]
	if !ok {
		writeJSON(w, 404, map[string]string{"error": ErrNoSession.Error()})
		return
	}
	tmp, err := os.CreateTemp(s.uploads.dir, id+".assembled-*")
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	hexsum, size, err := s.uploads.Assemble(id, tmp)
	tmp.Close()
	if err != nil {
		os.Remove(tmp.Name())
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	m, err := s.store.PutLarge(sess.ContentType, tmp.Name(), size, hexsum, sess.Actor)
	os.Remove(tmp.Name()) // local backend renamed it; s3 streamed it — gone either way
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	s.uploads.CleanupAfterCommit(id)
	writeJSON(w, 201, map[string]any{"ref": m.Ref(), "meta": m})
}

func (s server) abortUpload(w http.ResponseWriter, r *http.Request) {
	if err := s.uploads.Abort(r.PathValue("id")); err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"status": "aborted"})
}

// upload: raw body bytes, content type from the header, actor required.
// Multipart parsing is a proxy concern (nginx/MinIO browser); the service
// keeps one honest binary path.
func (s server) upload(w http.ResponseWriter, r *http.Request) {
	actor := r.Header.Get("X-Actor")
	if actor == "" {
		writeJSON(w, 400, map[string]string{"error": "X-Actor header required"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, MaxBlobBytes+1<<10)
	data, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, 413, map[string]string{"error": ErrTooLarge.Error()})
		return
	}
	m, err := s.store.Put(r.Header.Get("Content-Type"), data, actor)
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 201, map[string]any{"ref": m.Ref(), "meta": m})
}

// download honours a single "bytes=start-end" Range (206) so video players
// can seek without fetching whole objects (ADR-0028).
func (s server) download(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	w.Header().Set("Accept-Ranges", "bytes")
	if rng := r.Header.Get("Range"); rng != "" {
		start, end, ok := parseByteRange(rng)
		if !ok {
			writeJSON(w, 416, map[string]string{"error": "unsupported range"})
			return
		}
		// Resolve open-ended and suffix forms against the object size.
		head, err := s.store.Stat(id)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": err.Error()})
			return
		}
		if start < 0 { // suffix: last -start bytes
			start = head.Size + start
			end = head.Size - 1
		}
		if end < 0 {
			end = head.Size - 1
		}
		if start < 0 || end >= head.Size || start > end {
			writeJSON(w, 416, map[string]string{"error": "range not satisfiable"})
			return
		}
		m, data, err := s.store.GetRange(id, start, end)
		if err != nil {
			writeJSON(w, 416, map[string]string{"error": err.Error()})
			return
		}
		w.Header().Set("Content-Type", m.ContentType)
		w.Header().Set("Content-Range", "bytes "+strconv.FormatInt(start, 10)+"-"+strconv.FormatInt(end, 10)+"/"+strconv.FormatInt(m.Size, 10))
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.WriteHeader(206)
		w.Write(data)
		return
	}
	m, data, err := s.store.Get(id)
	if err != nil {
		code := 404
		if errors.Is(err, ErrDeleted) {
			code = 410
		}
		writeJSON(w, code, map[string]string{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", m.ContentType)
	w.Header().Set("ETag", `"`+m.SHA256+`"`)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(200)
	w.Write(data)
}

// parseByteRange accepts "bytes=a-b" and "bytes=a-" (open-ended resolves
// later against object size; here it stays -1 and backends reject it) and
// "bytes=-n" suffix form. Syntactically valid only.
func parseByteRange(v string) (int64, int64, bool) {
	rest, ok := strings.CutPrefix(v, "bytes=")
	if !ok || strings.Contains(rest, ",") {
		return 0, 0, false
	}
	a, b, found := strings.Cut(rest, "-")
	if !found {
		return 0, 0, false
	}
	if a == "" { // suffix form: last n bytes
		n, err := strconv.ParseInt(b, 10, 64)
		if err != nil || n <= 0 {
			return 0, 0, false
		}
		return -n, -1, true // caller resolves against size; sentinel handled below
	}
	start, err := strconv.ParseInt(a, 10, 64)
	if err != nil || start < 0 {
		return 0, 0, false
	}
	if b == "" {
		return start, -1, true
	}
	end, err := strconv.ParseInt(b, 10, 64)
	if err != nil || end < start {
		return 0, 0, false
	}
	return start, end, true
}

func (s server) stat(w http.ResponseWriter, r *http.Request) {
	m, err := s.store.Stat(r.PathValue("id"))
	if err != nil {
		w.WriteHeader(404)
		return
	}
	w.Header().Set("Content-Type", m.ContentType)
	w.Header().Set("X-Blob-Size", strconv.FormatInt(m.Size, 10))
	w.Header().Set("ETag", `"`+m.SHA256+`"`)
	w.WriteHeader(200)
}

func (s server) meta(w http.ResponseWriter, r *http.Request) {
	m, err := s.store.Stat(r.PathValue("id"))
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, m)
}

func (s server) tombstone(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Actor  string `json:"actor"`
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "bad json"})
		return
	}
	m, err := s.store.Tombstone(r.PathValue("id"), req.Actor, req.Reason)
	if err != nil {
		writeJSON(w, 422, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, m)
}

func main() {
	listen := os.Getenv("LISTEN")
	if listen == "" {
		listen = ":8095"
	}
	dir := os.Getenv("MEDIA_DIR")
	if dir == "" {
		dir = "./media-data"
	}
	var store BlobStore
	var err error
	if os.Getenv("S3_BACKEND") == "s3" {
		// State/national shape: bytes in MinIO-class object storage,
		// meta sidecars stay local. Composed over the S3 wire protocol,
		// never embedded (ADR-0027).
		store, err = NewS3Store(os.Getenv("S3_ENDPOINT"), os.Getenv("S3_BUCKET"),
			os.Getenv("S3_ACCESS_KEY"), os.Getenv("S3_SECRET_KEY"), dir)
	} else {
		store, err = NewLocalStore(dir)
	}
	if err != nil {
		panic(err)
	}
	uploads, err := NewUploadManager(dir)
	if err != nil {
		panic(err)
	}
	s := server{store: store, uploads: uploads}
	mux := http.NewServeMux()
	s.routes(mux)
	if err := http.ListenAndServe(listen, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		panic(err)
	}
}
