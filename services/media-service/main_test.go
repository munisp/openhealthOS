package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func newTestServer(t *testing.T) server {
	t.Helper()
	dir := t.TempDir()
	st, err := NewLocalStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	u, err := NewUploadManager(dir)
	if err != nil {
		t.Fatal(err)
	}
	return server{store: st, uploads: u}
}

func rawReq(t *testing.T, s server, method, path, body, ctype, actor string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", ctype)
	req.Header.Set("X-Actor", actor)
	rec := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rec, req)
	return rec
}

func upload(t *testing.T, s server, body string) string {
	t.Helper()
	rec := rawReq(t, s, "POST", "/blobs", body, "image/jpeg", "nurse-7")
	if rec.Code != 201 {
		t.Fatalf("upload: %d %s", rec.Code, rec.Body)
	}
	var out map[string]any
	json.Unmarshal(rec.Body.Bytes(), &out)
	return out["meta"].(map[string]any)["id"].(string)
}

func TestUploadDownloadRoundTrip(t *testing.T) {
	s := newTestServer(t)
	id := upload(t, s, "\xff\xd8\xff fake jpeg bytes")
	rec := rawReq(t, s, "GET", "/blobs/"+id, "", "", "")
	if rec.Code != 200 || rec.Body.String() != "\xff\xd8\xff fake jpeg bytes" {
		t.Fatalf("round trip: %d", rec.Code)
	}
	if rec.Header().Get("Content-Type") != "image/jpeg" {
		t.Fatalf("content type preserved: %v", rec.Header())
	}
	if !strings.HasPrefix(rec.Header().Get("ETag"), `"`) {
		t.Fatal("etag carries the checksum")
	}
}

func TestActorRequired(t *testing.T) {
	s := newTestServer(t)
	rec := rawReq(t, s, "POST", "/blobs", "x", "image/jpeg", "")
	if rec.Code != 400 {
		t.Fatalf("anonymous upload rejected: %d", rec.Code)
	}
}

func TestIntegrityFailureDetected(t *testing.T) {
	s := newTestServer(t)
	id := upload(t, s, "original bytes")
	// corrupt the file on disk behind the store's back
	ls := s.store.(*LocalStore)
	if err := writeFileCorrupt(ls.path(id)); err != nil {
		t.Fatal(err)
	}
	rec := rawReq(t, s, "GET", "/blobs/"+id, "", "", "")
	if rec.Code == 200 {
		t.Fatal("corrupted blob must not be served")
	}
}

func writeFileCorrupt(path string) error {
	return os.WriteFile(path, []byte("tampered"), 0o640)
}

func TestTombstoneIsSoft(t *testing.T) {
	s := newTestServer(t)
	id := upload(t, s, "evidence")
	rec := rawReq(t, s, "POST", "/blobs/"+id+"/delete",
		`{"actor":"compliance-1","reason":"duplicate upload"}`, "application/json", "")
	if rec.Code != 200 {
		t.Fatalf("tombstone: %d", rec.Code)
	}
	rec = rawReq(t, s, "GET", "/blobs/"+id, "", "", "")
	if rec.Code != 410 {
		t.Fatalf("tombstoned blob is gone-but-recorded: %d", rec.Code)
	}
	// metadata retains the audit
	var m Meta
	ls := s.store.(*LocalStore)
	for _, mm := range ls.List() {
		if mm.ID == id {
			m = mm
		}
	}
	if !m.Deleted || m.DeletedBy != "compliance-1" || m.DeleteWhy == "" {
		t.Fatalf("audit retained: %+v", m)
	}
}

func TestDeleteNeedsWhoAndWhy(t *testing.T) {
	s := newTestServer(t)
	id := upload(t, s, "x")
	rec := rawReq(t, s, "POST", "/blobs/"+id+"/delete",
		`{"actor":"","reason":""}`, "application/json", "")
	if rec.Code != 422 {
		t.Fatalf("anonymous delete rejected: %d", rec.Code)
	}
}

func TestRefParsing(t *testing.T) {
	id, err := ParseRef("blob://media/BLOB-00000001")
	if err != nil || id != "BLOB-00000001" {
		t.Fatalf("ref: %v %v", id, err)
	}
	for _, bad := range []string{"http://x/1", "blob://media/", "blob://media/../etc", ""} {
		if _, err := ParseRef(bad); err == nil {
			t.Fatalf("bad ref accepted: %q", bad)
		}
	}
}

func TestTooLargeRejected(t *testing.T) {
	s := newTestServer(t)
	big := make([]byte, MaxBlobBytes+1)
	rec := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/blobs", strings.NewReader(string(big)))
		req.Header.Set("X-Actor", "a")
		rec := httptest.NewRecorder()
		mux := http.NewServeMux()
		s.routes(mux)
		mux.ServeHTTP(rec, req)
		return rec
	}()
	if rec.Code != 413 && rec.Code != 422 {
		t.Fatalf("oversize rejected: %d", rec.Code)
	}
}
