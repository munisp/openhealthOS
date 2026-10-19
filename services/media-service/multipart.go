// Multipart upload sessions (ADR-0028): clinical video and imaging exports
// far exceed MaxBlobBytes. Chunks land one at a time — often over days on a
// facility link — each part hashed on arrival; assembly re-hashes the whole
// object before it becomes a blob. Sessions and parts live on disk under
// <dir>/uploads/ so a restart never loses an in-flight upload.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	MaxUploadBytes = 2 << 30        // 2 GiB — surgical video, CT series zips
	MaxUploadParts = 4096           // sane ceiling; default part ≈ 512 KiB+
	UploadTTL      = 72 * time.Hour // abandoned sessions are reaped by complete/abort paths
)

var (
	ErrNoSession   = errors.New("unknown upload session")
	ErrBadPart     = errors.New("invalid part number")
	ErrSizeOver    = errors.New("declared size exceeds 2 GiB")
	ErrPartMissing = errors.New("upload incomplete: missing parts")
)

type UploadSession struct {
	ID          string         `json:"id"`
	ContentType string         `json:"content_type"`
	Size        int64          `json:"size"`
	Actor       string         `json:"actor"`
	CreatedAt   time.Time      `json:"created_at"`
	Parts       map[int]string `json:"parts"` // part number -> sha256 hex
}

// UploadManager owns in-flight sessions for a store directory.
type UploadManager struct {
	dir      string // <store dir>/uploads
	mu       sync.Mutex
	seq      int
	sessions map[string]*UploadSession
}

func NewUploadManager(storeDir string) (*UploadManager, error) {
	dir := filepath.Join(storeDir, "uploads")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return nil, err
	}
	u := &UploadManager{dir: dir, sessions: map[string]*UploadSession{}}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".session.json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var s UploadSession
		if json.Unmarshal(raw, &s) == nil && s.ID != "" {
			u.sessions[s.ID] = &s
			var n int
			if _, err := fmt.Sscanf(s.ID, "UPL-%08d", &n); err == nil && n > u.seq {
				u.seq = n
			}
		}
	}
	return u, nil
}

func (u *UploadManager) save(s *UploadSession) error {
	raw, err := json.Marshal(s)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(u.dir, s.ID+".session.json"), raw, 0o640)
}

func (u *UploadManager) partPath(s *UploadSession, n int) string {
	return filepath.Join(u.dir, fmt.Sprintf("%s.part-%06d", s.ID, n))
}

// Initiate opens a session. Size is declared up front so abuse fails fast.
func (u *UploadManager) Initiate(contentType string, size int64, actor string) (UploadSession, error) {
	if actor == "" {
		return UploadSession{}, errors.New("uploads name an actor")
	}
	if size <= 0 || size > MaxUploadBytes {
		return UploadSession{}, ErrSizeOver
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	u.mu.Lock()
	u.seq++
	s := &UploadSession{
		ID:          fmt.Sprintf("UPL-%08d", u.seq),
		ContentType: contentType,
		Size:        size,
		Actor:       actor,
		CreatedAt:   time.Now(),
		Parts:       map[int]string{},
	}
	u.sessions[s.ID] = s
	u.mu.Unlock()
	if err := u.save(s); err != nil {
		return UploadSession{}, err
	}
	return *s, nil
}

// PutPart stores one chunk and records its hash. Part numbers are 1-based.
func (u *UploadManager) PutPart(id string, n int, data []byte) (string, error) {
	u.mu.Lock()
	s, ok := u.sessions[id]
	u.mu.Unlock()
	if !ok {
		return "", ErrNoSession
	}
	if n < 1 || n > MaxUploadParts {
		return "", ErrBadPart
	}
	sum := sha256.Sum256(data)
	hexsum := hex.EncodeToString(sum[:])
	if err := os.WriteFile(u.partPath(s, n), data, 0o640); err != nil {
		return "", err
	}
	u.mu.Lock()
	s.Parts[n] = hexsum
	u.mu.Unlock()
	if err := u.save(s); err != nil {
		return "", err
	}
	return hexsum, nil
}

// Assemble streams parts in order into dst, verifying each part hash and the
// declared total size; returns the whole-object SHA-256. dst stays a plain
// file — the caller hands it to PutLarge.
func (u *UploadManager) Assemble(id string, dst *os.File) (string, int64, error) {
	u.mu.Lock()
	s, ok := u.sessions[id]
	u.mu.Unlock()
	if !ok {
		return "", 0, ErrNoSession
	}
	nums := make([]int, 0, len(s.Parts))
	for n := range s.Parts {
		nums = append(nums, n)
	}
	sort.Ints(nums)
	if len(nums) == 0 || nums[0] != 1 || nums[len(nums)-1] != len(nums) {
		return "", 0, ErrPartMissing
	}
	h := sha256.New()
	var total int64
	for _, n := range nums {
		f, err := os.Open(u.partPath(s, n))
		if err != nil {
			return "", 0, err
		}
		ph := sha256.New()
		w := io.MultiWriter(dst, h, ph)
		if _, err := io.Copy(w, f); err != nil {
			f.Close()
			return "", 0, err
		}
		fi, _ := f.Stat()
		f.Close()
		total += fi.Size()
		if hex.EncodeToString(ph.Sum(nil)) != s.Parts[n] {
			return "", 0, fmt.Errorf("integrity failure on part %d of %s", n, id)
		}
	}
	if total != s.Size {
		return "", 0, fmt.Errorf("assembled %d bytes, session declared %d", total, s.Size)
	}
	return hex.EncodeToString(h.Sum(nil)), total, nil
}

// Abort drops the session and every part on disk.
func (u *UploadManager) Abort(id string) error {
	u.mu.Lock()
	s, ok := u.sessions[id]
	if ok {
		delete(u.sessions, id)
	}
	u.mu.Unlock()
	if !ok {
		return ErrNoSession
	}
	for n := range s.Parts {
		os.Remove(u.partPath(s, n))
	}
	os.Remove(filepath.Join(u.dir, s.ID+".session.json"))
	return nil
}

// CleanupAfterCommit removes parts once the blob exists.
func (u *UploadManager) CleanupAfterCommit(id string) { _ = u.Abort(id) }

// ParsePartNumber extracts and validates the {n} path segment.
func ParsePartNumber(s string) (int, error) {
	n, err := strconv.Atoi(s)
	if err != nil || n < 1 || n > MaxUploadParts {
		return 0, ErrBadPart
	}
	return n, nil
}
