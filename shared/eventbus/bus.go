// Package eventbus — the platform's event backbone seam (Kafka-shaped,
// stdlib-only). Topics are append-only JSON-line logs with fsync durability;
// consumers track offsets per group. In-memory for tests/edge, file-backed
// for the state plane; production can swap in Kafka behind this interface
// without touching domain code (ADR-0005 reuse-vs-reimplement).
//
// Delivery semantics: at-least-once. An offset commits only after the
// handler succeeds — a crash mid-handler redelivers, never loses.
package eventbus

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Event is one published fact.
type Event struct {
	Topic   string          `json:"topic"`
	Seq     int64           `json:"seq"` // offset within the topic
	ID      string          `json:"id"`  // topic-seq — globally unique, dedup-friendly
	TS      time.Time       `json:"ts"`
	Payload json.RawMessage `json:"payload"`
}

// Bus is the publish/consume seam.
type Bus interface {
	Publish(topic string, payload any) (Event, error)
	Poll(group, topic string, limit int) ([]Event, error)
	Commit(group, topic string, seq int64) error
	Offset(group, topic string) int64
}

// ---------- in-memory ----------

// MemoryBus for tests and edge processes.
type MemoryBus struct {
	mu      sync.Mutex
	topics  map[string][]Event
	offsets map[string]int64 // group|topic -> committed seq
}

func NewMemoryBus() *MemoryBus {
	return &MemoryBus{topics: map[string][]Event{}, offsets: map[string]int64{}}
}

func (m *MemoryBus) Publish(topic string, payload any) (Event, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return Event{}, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	seq := int64(len(m.topics[topic])) + 1
	ev := Event{Topic: topic, Seq: seq, ID: topic + "-" + strconv.FormatInt(seq, 10),
		TS: time.Now().UTC(), Payload: raw}
	m.topics[topic] = append(m.topics[topic], ev)
	return ev, nil
}

func (m *MemoryBus) Poll(group, topic string, limit int) ([]Event, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	off := m.offsets[group+"|"+topic]
	var out []Event
	for _, ev := range m.topics[topic] {
		if ev.Seq > off {
			out = append(out, ev)
			if len(out) >= limit {
				break
			}
		}
	}
	return out, nil
}

func (m *MemoryBus) Commit(group, topic string, seq int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.offsets[group+"|"+topic] = seq
	return nil
}

func (m *MemoryBus) Offset(group, topic string) int64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.offsets[group+"|"+topic]
}

// ---------- file-backed ----------

// FileBus persists topics as JSON-lines with fsync per publish, and group
// offsets as small files — the same durability discipline as the ledger.
type FileBus struct {
	dir string
	mu  sync.Mutex
}

// NewFileBus opens (creating if needed) a bus rooted at dir.
func NewFileBus(dir string) (*FileBus, error) {
	for _, sub := range []string{"topics", "offsets"} {
		if err := os.MkdirAll(filepath.Join(dir, sub), 0o700); err != nil {
			return nil, err
		}
	}
	return &FileBus{dir: dir}, nil
}

func safeName(s string) (string, error) {
	if s == "" || strings.ContainsAny(s, "/\\..") {
		return "", fmt.Errorf("eventbus: unsafe name %q", s)
	}
	return s, nil
}

func (f *FileBus) topicPath(topic string) (string, error) {
	t, err := safeName(topic)
	if err != nil {
		return "", err
	}
	return filepath.Join(f.dir, "topics", t+".jsonl"), nil
}

func (f *FileBus) Publish(topic string, payload any) (Event, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return Event{}, err
	}
	path, err := f.topicPath(topic)
	if err != nil {
		return Event{}, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	seq := int64(1)
	if fh, err := os.Open(path); err == nil {
		sc := bufio.NewScanner(fh)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		for sc.Scan() {
			seq++
		}
		fh.Close()
	}
	ev := Event{Topic: topic, Seq: seq, ID: topic + "-" + strconv.FormatInt(seq, 10),
		TS: time.Now().UTC(), Payload: raw}
	line, _ := json.Marshal(ev)
	fh, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return Event{}, err
	}
	defer fh.Close()
	if _, err := fh.Write(append(line, '\n')); err != nil {
		return Event{}, err
	}
	if err := fh.Sync(); err != nil { // durable before visible
		return Event{}, err
	}
	return ev, nil
}

func (f *FileBus) Poll(group, topic string, limit int) ([]Event, error) {
	path, err := f.topicPath(topic)
	if err != nil {
		return nil, err
	}
	off := f.Offset(group, topic)
	fh, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer fh.Close()
	var out []Event
	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || !strings.HasSuffix(line, "}") {
			continue // torn tail after crash — everything before it is intact
		}
		var ev Event
		if json.Unmarshal([]byte(line), &ev) != nil {
			continue
		}
		if ev.Seq > off {
			out = append(out, ev)
			if len(out) >= limit {
				break
			}
		}
	}
	return out, sc.Err()
}

func (f *FileBus) Commit(group, topic string, seq int64) error {
	g, err := safeName(group)
	if err != nil {
		return err
	}
	t, err := safeName(topic)
	if err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	path := filepath.Join(f.dir, "offsets", g+"."+t)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(strconv.FormatInt(seq, 10)), 0o600); err != nil {
		return err
	}
	if fh, err := os.Open(tmp); err == nil { // fsync before rename
		fh.Sync()
		fh.Close()
	}
	return os.Rename(tmp, path)
}

func (f *FileBus) Offset(group, topic string) int64 {
	raw, err := os.ReadFile(filepath.Join(f.dir, "offsets", group+"."+topic))
	if err != nil {
		return 0
	}
	n, _ := strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
	return n
}

// Consume polls and handles events, committing each offset only after the
// handler succeeds. Returns events handled. At-least-once: a handler error
// leaves the offset behind, so the next Consume redelivers.
func Consume(bus Bus, group, topic string, limit int, handle func(Event) error) (int, error) {
	events, err := bus.Poll(group, topic, limit)
	if err != nil {
		return 0, err
	}
	n := 0
	for _, ev := range events {
		if err := handle(ev); err != nil {
			return n, fmt.Errorf("handler failed at %s (offset not committed — will redeliver): %w", ev.ID, err)
		}
		if err := bus.Commit(group, topic, ev.Seq); err != nil {
			return n, err
		}
		n++
	}
	return n, nil
}
