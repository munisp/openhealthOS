// HTTP surface for the media blob store (ADR-0027).
package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strconv"
)

type server struct {
	store BlobStore
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

func (s server) download(w http.ResponseWriter, r *http.Request) {
	m, data, err := s.store.Get(r.PathValue("id"))
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
	s := server{store: store}
	mux := http.NewServeMux()
	s.routes(mux)
	if err := http.ListenAndServe(listen, mux); err != nil && !errors.Is(err, http.ErrServerClosed) {
		panic(err)
	}
}
