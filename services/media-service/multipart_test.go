package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// multipartRoundTrip drives initiate → parts → complete → download → range.
func TestMultipartRoundTrip(t *testing.T) {
	s := newTestServer(t)
	payload := strings.Repeat("clinical-video-segment-", 4000) // ~92 KiB
	half := len(payload) / 2

	rec := rawReq(t, s, "POST", "/uploads",
		fmt.Sprintf(`{"content_type":"video/mp4","size":%d}`, len(payload)), "application/json", "tech-3")
	if rec.Code != 201 {
		t.Fatalf("initiate: %d %s", rec.Code, rec.Body)
	}
	var sess UploadSession
	json.Unmarshal(rec.Body.Bytes(), &sess)
	if sess.ID == "" || sess.Actor != "tech-3" {
		t.Fatalf("bad session: %+v", sess)
	}

	for i, chunk := range []string{payload[:half], payload[half:]} {
		rec := rawReq(t, s, "PUT", fmt.Sprintf("/uploads/%s/parts/%d", sess.ID, i+1), chunk, "application/octet-stream", "tech-3")
		if rec.Code != 200 {
			t.Fatalf("part %d: %d %s", i+1, rec.Code, rec.Body)
		}
	}

	rec = rawReq(t, s, "POST", fmt.Sprintf("/uploads/%s/complete", sess.ID), "", "", "tech-3")
	if rec.Code != 201 {
		t.Fatalf("complete: %d %s", rec.Code, rec.Body)
	}
	var out map[string]any
	json.Unmarshal(rec.Body.Bytes(), &out)
	meta := out["meta"].(map[string]any)
	if meta["size"].(float64) != float64(len(payload)) {
		t.Fatalf("assembled size %v != %d", meta["size"], len(payload))
	}
	id := strings.TrimPrefix(out["ref"].(string), "blob://media/")

	// whole download matches byte-for-byte
	rec = rawReq(t, s, "GET", "/blobs/"+id, "", "", "tech-3")
	if rec.Code != 200 || rec.Body.String() != payload {
		t.Fatalf("download mismatch: %d", rec.Code)
	}
	if rec.Header().Get("Accept-Ranges") != "bytes" {
		t.Fatal("missing Accept-Ranges")
	}

	// range request returns exactly the slice
	req := httptest.NewRequest("GET", "/blobs/"+id, nil)
	req.Header.Set("Range", "bytes=10-29")
	rr := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rr, req)
	if rr.Code != 206 {
		t.Fatalf("range: %d %s", rr.Code, rr.Body)
	}
	if rr.Body.String() != payload[10:30] {
		t.Fatalf("range body %q != %q", rr.Body.String(), payload[10:30])
	}
	if rr.Header().Get("Content-Range") != fmt.Sprintf("bytes 10-29/%d", len(payload)) {
		t.Fatalf("bad Content-Range: %s", rr.Header().Get("Content-Range"))
	}
}

func TestMultipartRejectsTamperedPart(t *testing.T) {
	s := newTestServer(t)
	payload := strings.Repeat("x", 5000)
	rec := rawReq(t, s, "POST", "/uploads",
		fmt.Sprintf(`{"content_type":"video/mp4","size":%d}`, len(payload)), "application/json", "tech-3")
	var sess UploadSession
	json.Unmarshal(rec.Body.Bytes(), &sess)

	rawReq(t, s, "PUT", fmt.Sprintf("/uploads/%s/parts/1", sess.ID), payload[:2500], "application/octet-stream", "tech-3")
	rawReq(t, s, "PUT", fmt.Sprintf("/uploads/%s/parts/2", sess.ID), payload[2500:], "application/octet-stream", "tech-3")

	// corrupt part 2 on disk after it was accepted
	u := s.uploads
	sessP := u.sessions[sess.ID]
	p := u.partPath(sessP, 2)
	b, _ := os.ReadFile(p)
	b[7] ^= 0xFF
	os.WriteFile(p, b, 0o640)

	rec = rawReq(t, s, "POST", fmt.Sprintf("/uploads/%s/complete", sess.ID), "", "", "tech-3")
	if rec.Code != 422 || !strings.Contains(rec.Body.String(), "integrity failure") {
		t.Fatalf("tampered part must fail assembly: %d %s", rec.Code, rec.Body)
	}
}

func TestMultipartGuards(t *testing.T) {
	s := newTestServer(t)
	// no actor
	if rec := rawReq(t, s, "POST", "/uploads", `{"size":100}`, "application/json", ""); rec.Code != 422 {
		t.Fatalf("actor required: %d", rec.Code)
	}
	// oversize declaration
	rec := rawReq(t, s, "POST", "/uploads",
		fmt.Sprintf(`{"size":%d}`, MaxUploadBytes+1), "application/json", "tech-3")
	if rec.Code != 422 {
		t.Fatalf("size guard: %d", rec.Code)
	}
	// missing parts
	rec = rawReq(t, s, "POST", "/uploads", `{"size":4}`, "application/json", "tech-3")
	var sess UploadSession
	json.Unmarshal(rec.Body.Bytes(), &sess)
	if rec := rawReq(t, s, "POST", fmt.Sprintf("/uploads/%s/complete", sess.ID), "", "", "tech-3"); rec.Code != 422 {
		t.Fatalf("empty assembly must fail: %d", rec.Code)
	}
	// abort
	if rec := rawReq(t, s, "POST", fmt.Sprintf("/uploads/%s/abort", sess.ID), "", "", "tech-3"); rec.Code != 200 {
		t.Fatalf("abort: %d", rec.Code)
	}
}

func TestUploadSessionSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	u1, err := NewUploadManager(dir)
	if err != nil {
		t.Fatal(err)
	}
	sess, err := u1.Initiate("video/mp4", 10, "tech-3")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := u1.PutPart(sess.ID, 1, []byte("0123456789")); err != nil {
		t.Fatal(err)
	}
	u2, err := NewUploadManager(dir) // simulate restart
	if err != nil {
		t.Fatal(err)
	}
	got, ok := u2.sessions[sess.ID]
	if !ok || got.Parts[1] == "" {
		t.Fatalf("session did not survive restart: %+v", u2.sessions)
	}
}

func TestParseByteRange(t *testing.T) {
	cases := []struct {
		in         string
		start, end int64
		ok         bool
	}{
		{"bytes=0-99", 0, 99, true},
		{"bytes=100-", 100, -1, true},
		{"bytes=-500", -500, -1, true},
		{"bytes=99-0", 0, 0, false},
		{"items=0-9", 0, 0, false},
		{"bytes=0-1,3-4", 0, 0, false},
	}
	for _, c := range cases {
		st, en, ok := parseByteRange(c.in)
		if ok != c.ok || (ok && (st != c.start || en != c.end)) {
			t.Errorf("%s -> %d,%d,%v want %d,%d,%v", c.in, st, en, ok, c.start, c.end, c.ok)
		}
	}
}
