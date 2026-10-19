// Media blob store (ADR-0027): the bytes behind blob:// references.
//
// Consult threads (ADR-0026), referral letters, and wound photos carry
// blob://media/{id} pointers; this service owns the bytes. Two backends:
//
//   - local (default): stdlib filesystem store, offline-first, zero deps —
//     right for facility servers and pilots;
//   - s3: any S3-compatible object store (MinIO-class, AGPL — composed over
//     the S3 wire protocol, never embedded) for state/national scale.
//
// Integrity: every upload returns a SHA-256 checksum; reads can require the
// checksum to match (tamper-evident media, same doctrine as the audit lake).
// Uploads name an actor; deletes are soft (tombstones) with actor + reason —
// medical media is evidence, not clutter.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const MaxBlobBytes = 25 << 20 // 25 MiB — clinical photos/videos, not archives

var (
	ErrNotFound = errors.New("unknown blob")
	ErrTooLarge = errors.New("blob exceeds 25 MiB")
	ErrDeleted  = errors.New("blob is tombstoned")
	ErrBadRef   = errors.New("invalid blob reference")
)

type Meta struct {
	ID          string    `json:"id"`
	ContentType string    `json:"content_type"`
	Size        int64     `json:"size"`
	SHA256      string    `json:"sha256"`
	UploadedBy  string    `json:"uploaded_by"`
	UploadedAt  time.Time `json:"uploaded_at"`
	Deleted     bool      `json:"deleted,omitempty"`
	DeletedBy   string    `json:"deleted_by,omitempty"`
	DeleteWhy   string    `json:"delete_reason,omitempty"`
}

// RefFor renders the pointer threads store.
func (m Meta) Ref() string { return "blob://media/" + m.ID }

// BlobStore is the backend contract — local today, S3 at scale.
type BlobStore interface {
	Put(contentType string, data []byte, actor string) (Meta, error)
	Get(id string) (Meta, []byte, error)
	Stat(id string) (Meta, error)
	Tombstone(id, actor, reason string) (Meta, error)
	List() []Meta
}

// ---------------------------------------------------------- local backend

type LocalStore struct {
	dir  string
	mu   sync.Mutex
	seq  int
	meta map[string]Meta
}

// ---- sidecar metadata (shared by both backends): one JSON per blob next
// to the bytes (local) or in a meta dir (s3). A restart rebuilds the index
// from sidecars — the audit trail and checksums are never in-memory only.

func writeSidecarTo(dir string, m Meta) error {
	raw, err := json.Marshal(m)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, m.ID+".json"), raw, 0o640)
}

func scanSidecars(dir string, seq *int, meta map[string]Meta) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var m Meta
		if json.Unmarshal(raw, &m) == nil && m.ID != "" {
			meta[m.ID] = m
			var n int
			if _, err := fmt.Sscanf(m.ID, "BLOB-%08d", &n); err == nil && n > *seq {
				*seq = n
			}
		}
	}
	return nil
}

func NewLocalStore(dir string) (*LocalStore, error) {
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return nil, err
	}
	l := &LocalStore{dir: dir, meta: map[string]Meta{}}
	if err := scanSidecars(dir, &l.seq, l.meta); err != nil {
		return nil, err
	}
	return l, nil
}

func (l *LocalStore) writeSidecar(m Meta) error {
	return writeSidecarTo(l.dir, m)
}

func (l *LocalStore) path(id string) string { return filepath.Join(l.dir, id+".bin") }

func (l *LocalStore) Put(contentType string, data []byte, actor string) (Meta, error) {
	if actor == "" {
		return Meta{}, errors.New("uploads name an actor")
	}
	if len(data) > MaxBlobBytes {
		return Meta{}, ErrTooLarge
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	sum := sha256.Sum256(data)
	l.mu.Lock()
	l.seq++
	m := Meta{
		ID:          fmt.Sprintf("BLOB-%08d", l.seq),
		ContentType: contentType,
		Size:        int64(len(data)),
		SHA256:      hex.EncodeToString(sum[:]),
		UploadedBy:  actor,
		UploadedAt:  time.Now(),
	}
	l.meta[m.ID] = m
	l.mu.Unlock()
	if err := os.WriteFile(l.path(m.ID), data, 0o640); err != nil {
		return Meta{}, err
	}
	if err := l.writeSidecar(m); err != nil {
		return Meta{}, err
	}
	return m, nil
}

func (l *LocalStore) Get(id string) (Meta, []byte, error) {
	m, err := l.Stat(id)
	if err != nil {
		return Meta{}, nil, err
	}
	data, err := os.ReadFile(l.path(id))
	if err != nil {
		return Meta{}, nil, err
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != m.SHA256 {
		return Meta{}, nil, fmt.Errorf("integrity failure on %s", id)
	}
	return m, data, nil
}

func (l *LocalStore) Stat(id string) (Meta, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	m, ok := l.meta[id]
	if !ok {
		return Meta{}, ErrNotFound
	}
	if m.Deleted {
		return m, ErrDeleted
	}
	return m, nil
}

func (l *LocalStore) Tombstone(id, actor, reason string) (Meta, error) {
	if actor == "" || reason == "" {
		return Meta{}, errors.New("deletes name who and why")
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	m, ok := l.meta[id]
	if !ok {
		return Meta{}, ErrNotFound
	}
	m.Deleted, m.DeletedBy, m.DeleteWhy = true, actor, reason
	l.meta[id] = m
	return m, nil
}

func (l *LocalStore) List() []Meta {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]Meta, 0, len(l.meta))
	for _, m := range l.meta {
		out = append(out, m)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// ParseRef extracts the id from a blob://media/{id} pointer.
func ParseRef(ref string) (string, error) {
	id, ok := strings.CutPrefix(ref, "blob://media/")
	if !ok || id == "" || strings.ContainsAny(id, "/\\..") {
		return "", ErrBadRef
	}
	return id, nil
}
