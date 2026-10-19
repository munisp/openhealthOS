package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeS3 is a minimal in-memory S3 endpoint: path-style PUT/GET, records
// whether every request carried a SigV4 Authorization header.
type fakeS3 struct {
	mu      sync.Mutex
	objects map[string][]byte
	signed  bool
}

func newFakeS3(t *testing.T) (*fakeS3, *httptest.Server) {
	t.Helper()
	f := &fakeS3{objects: map[string][]byte{}, signed: true}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 Credential=") {
			f.mu.Lock()
			f.signed = false
			f.mu.Unlock()
		}
		key := strings.TrimPrefix(r.URL.Path, "/test-bucket/")
		f.mu.Lock()
		defer f.mu.Unlock()
		switch r.Method {
		case "PUT":
			body, _ := io.ReadAll(r.Body)
			f.objects[key] = body
			w.WriteHeader(200)
		case "GET":
			if b, ok := f.objects[key]; ok {
				w.Write(b)
			} else {
				w.WriteHeader(404)
			}
		default:
			w.WriteHeader(405)
		}
	}))
	t.Cleanup(srv.Close)
	return f, srv
}

func TestS3RoundTripSignedAndVerified(t *testing.T) {
	f, srv := newFakeS3(t)
	store, err := NewS3Store(srv.URL, "test-bucket", "ak", "sk", t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	m, err := store.Put("image/jpeg", []byte("wound photo bytes"), "nurse-7")
	if err != nil {
		t.Fatal(err)
	}
	if !f.signed {
		t.Fatal("every S3 request must be SigV4-signed")
	}
	got, data, err := store.Get(m.ID)
	if err != nil || string(data) != "wound photo bytes" {
		t.Fatalf("round trip: %v", err)
	}
	if got.SHA256 != m.SHA256 {
		t.Fatal("checksum stable across backends")
	}
}

func TestS3IntegrityFailure(t *testing.T) {
	f, srv := newFakeS3(t)
	store, _ := NewS3Store(srv.URL, "test-bucket", "ak", "sk", t.TempDir())
	m, _ := store.Put("image/jpeg", []byte("original"), "nurse-7")
	f.objects[m.ID+".bin"] = []byte("tampered")
	if _, _, err := store.Get(m.ID); err == nil {
		t.Fatal("tampered object must fail integrity")
	}
}

func TestS3TombstoneAndMetaPersist(t *testing.T) {
	_, srv := newFakeS3(t)
	dir := t.TempDir()
	store, _ := NewS3Store(srv.URL, "test-bucket", "ak", "sk", dir)
	m, _ := store.Put("text/plain", []byte("x"), "nurse-7")
	if _, err := store.Tombstone(m.ID, "compliance-1", "duplicate"); err != nil {
		t.Fatal(err)
	}
	// new store over the same meta dir: tombstone survives the restart
	store2, err := NewS3Store(srv.URL, "test-bucket", "ak", "sk", dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := store2.Get(m.ID); err == nil {
		t.Fatal("tombstone survives restart")
	}
	if store2.seq != store.seq {
		t.Fatalf("sequence continues after restart: %d vs %d", store2.seq, store.seq)
	}
}

func TestLocalStoreRestartPersistence(t *testing.T) {
	dir := t.TempDir()
	a, _ := NewLocalStore(dir)
	m, err := a.Put("image/jpeg", []byte("bytes"), "nurse-7")
	if err != nil {
		t.Fatal(err)
	}
	b, err := NewLocalStore(dir) // simulated restart
	if err != nil {
		t.Fatal(err)
	}
	got, data, err := b.Get(m.ID)
	if err != nil || string(data) != "bytes" || got.UploadedBy != "nurse-7" {
		t.Fatalf("local store survives restart: %v", err)
	}
	n, err := b.Put("image/jpeg", []byte("y"), "nurse-7")
	if err != nil || n.ID <= m.ID {
		t.Fatalf("sequence continues after restart: %v %v", n.ID, err)
	}
}

func TestS3NeedsCredentials(t *testing.T) {
	if _, err := NewS3Store("http://x", "b", "", "sk", t.TempDir()); err == nil {
		t.Fatal("credentials required")
	}
}
