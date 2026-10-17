// File-backed hub store — the default for a real LGA hub: a mini-PC
// with power cuts and no Postgres. Every accepted event is appended to a
// JSONL spill file (fsync) before the caller is acked; on boot the file
// is replayed to rebuild the index. Forwarded entries are compacted out
// when the file grows past compactAfter entries.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

const compactAfter = 10000

type FileStore struct {
	mu      sync.Mutex
	path    string
	f       *os.File
	seen    map[string]bool
	entries []entry // includes forwarded ones until compaction
	dirty   int     // forwarded-but-not-compacted count
}

func NewFileStore(path string) (*FileStore, error) {
	s := &FileStore{path: path, seen: map[string]bool{}}
	// replay: data lines carry ev, ack lines carry {"ack": id}
	if f, err := os.Open(path); err == nil {
		acks := map[string]bool{}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1024*1024), 1024*1024)
		for sc.Scan() {
			var probe struct {
				Ack string `json:"ack"`
			}
			if json.Unmarshal(sc.Bytes(), &probe) == nil && probe.Ack != "" {
				acks[probe.Ack] = true
				continue
			}
			var e entry
			if json.Unmarshal(sc.Bytes(), &e) == nil && e.Ev.ID != "" {
				if !s.seen[e.Ev.ID] {
					s.seen[e.Ev.ID] = true
					s.entries = append(s.entries, e)
				}
			}
		}
		f.Close()
		for i, e := range s.entries {
			if acks[e.Ev.ID] && !e.Forwarded {
				s.entries[i].Forwarded = true
				s.dirty++
			}
		}
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, fmt.Errorf("spill file: %w", err)
	}
	s.f = f
	return s, nil
}

func (s *FileStore) Add(ev Event) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen[ev.ID] {
		return false, nil
	}
	e := entry{Ev: ev, Class: Classify(ev), At: time.Now().UTC().Format(time.RFC3339)}
	line, err := json.Marshal(e)
	if err != nil {
		return false, err
	}
	if _, err := s.f.Write(append(line, '\n')); err != nil {
		return false, err
	}
	if err := s.f.Sync(); err != nil { // power cuts are the norm here
		return false, err
	}
	s.seen[ev.ID] = true
	s.entries = append(s.entries, e)
	return true, nil
}

func (s *FileStore) Pending(class string, limit int) []entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []entry
	for _, e := range s.entries {
		if e.Class == class && !e.Forwarded {
			out = append(out, e)
			if len(out) >= limit {
				break
			}
		}
	}
	return out
}

func (s *FileStore) MarkForwarded(ids []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	ack := make(map[string]bool, len(ids))
	for _, id := range ids {
		ack[id] = true
	}
	// Persist the acks first — a restart must not resurrect forwarded
	// events (the upstream dedupes, but the queue would lie).
	var buf []byte
	for i, e := range s.entries {
		if !e.Forwarded && ack[e.Ev.ID] {
			s.entries[i].Forwarded = true
			s.dirty++
			line, _ := json.Marshal(map[string]string{"ack": e.Ev.ID})
			buf = append(buf, line...)
			buf = append(buf, '\n')
		}
	}
	if len(buf) > 0 {
		if _, err := s.f.Write(buf); err != nil {
			return err
		}
		if err := s.f.Sync(); err != nil {
			return err
		}
	}
	if s.dirty > compactAfter {
		return s.compactLocked()
	}
	return nil
}

// compactLocked rewrites the spill file with unforwarded entries only.
func (s *FileStore) compactLocked() error {
	tmp := s.path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	var kept []entry
	for _, e := range s.entries {
		if e.Forwarded {
			continue
		}
		line, _ := json.Marshal(e)
		if _, err := f.Write(append(line, '\n')); err != nil {
			f.Close()
			return err
		}
		kept = append(kept, e)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	f.Close()
	if err := os.Rename(tmp, s.path); err != nil {
		return err
	}
	s.f.Close()
	nf, err := os.OpenFile(s.path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	s.f = nf
	s.entries = kept
	s.dirty = 0
	return nil
}

func (s *FileStore) PendingCounts() (c, st, b int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, e := range s.entries {
		if e.Forwarded {
			continue
		}
		switch e.Class {
		case Critical:
			c++
		case Standard:
			st++
		default:
			b++
		}
	}
	return
}

func (s *FileStore) Events() []entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]entry, len(s.entries))
	copy(out, s.entries)
	return out
}

func (s *FileStore) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.f.Close()
}

var _ Store = (*FileStore)(nil)
var _ Store = (*MemoryStore)(nil)
