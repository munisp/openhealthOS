// Dead-letter queues for the event backbone (ADR-0015) — the Kafka row.
//
// Pure at-least-once delivery has one fatal shape: the poison message.
// An event whose handler always fails sits at the head of the group's
// offset forever — every Consume redelivers it, stops there, and every
// event behind it (claims results, payment confirmations) is silently
// stuck. Kafka estates solve this with dead-letter topics; this wrapper
// gives the platform seam the same semantics:
//
//	delivery fails < MaxDeliveries times → offset stays, next Consume
//	    redelivers (transient faults heal themselves)
//	delivery fails the MaxDeliveries-th time → the event is parked on
//	    "<topic>.dead" with its failure history, the offset commits, and
//	    the group moves on — one poisoned message never stalls a topic
//
// Parked events are operator-visible (OnDeadLetter seam + the dead
// topic itself) and replayable after the handler is fixed (Replay).
// Attempt counts persist through an AttemptStore so a process restart
// doesn't reset the poison counter.
package eventbus

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// DeadLetterEvent is the envelope parked on the dead topic.
type DeadLetterEvent struct {
	Original   Event     `json:"original"`
	Group      string    `json:"group"`
	Error      string    `json:"error"`
	Deliveries int       `json:"deliveries"`
	ParkedAt   time.Time `json:"parked_at"`
}

// AttemptStore persists per-(group,topic,seq) delivery attempt counts.
type AttemptStore interface {
	// Inc increments and returns the new count for key.
	Inc(key string) int
	// Clear removes key (on success or parking).
	Clear(key string)
}

// MemoryAttempts is the in-memory AttemptStore (tests, edge processes).
type MemoryAttempts struct {
	mu sync.Mutex
	m  map[string]int
}

func NewMemoryAttempts() *MemoryAttempts { return &MemoryAttempts{m: map[string]int{}} }

func (s *MemoryAttempts) Inc(key string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.m[key]++
	return s.m[key]
}

func (s *MemoryAttempts) Clear(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.m, key)
}

// FileAttempts persists attempt counts as small fsync'd files — the same
// durability discipline as FileBus offsets.
type FileAttempts struct {
	dir string
	mu  sync.Mutex
}

func NewFileAttempts(dir string) (*FileAttempts, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	return &FileAttempts{dir: dir}, nil
}

func attemptName(key string) string {
	// keys are group|topic|seq — every char is filesystem-safe except |
	r := strings.NewReplacer("|", ".", "/", "_", "\\", "_")
	return r.Replace(key)
}

func (s *FileAttempts) Inc(key string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	path := filepath.Join(s.dir, attemptName(key))
	n := 0
	if raw, err := os.ReadFile(path); err == nil {
		n, _ = strconv.Atoi(strings.TrimSpace(string(raw)))
	}
	n++
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(strconv.Itoa(n)), 0o600); err != nil {
		return n
	}
	if fh, err := os.Open(tmp); err == nil {
		fh.Sync()
		fh.Close()
	}
	os.Rename(tmp, path)
	return n
}

func (s *FileAttempts) Clear(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	os.Remove(filepath.Join(s.dir, attemptName(key)))
}

// DeadLetter wraps a Bus with poison-message handling.
type DeadLetter struct {
	Bus           Bus
	MaxDeliveries int // <= 0 defaults to 5
	Attempts      AttemptStore
	// OnDeadLetter fires each time an event is parked — wire to alerting.
	OnDeadLetter func(DeadLetterEvent)
}

// NewDeadLetter wraps bus. attempts may be nil (in-memory store used).
func NewDeadLetter(bus Bus, attempts AttemptStore) *DeadLetter {
	if attempts == nil {
		attempts = NewMemoryAttempts()
	}
	return &DeadLetter{Bus: bus, Attempts: attempts}
}

func (d *DeadLetter) maxDeliveries() int {
	if d.MaxDeliveries <= 0 {
		return 5
	}
	return d.MaxDeliveries
}

// DeadTopic is where poison events for topic are parked.
func DeadTopic(topic string) string { return topic + ".dead" }

// ConsumeResult reports one Consume call.
type ConsumeResult struct {
	Handled int // events whose handler succeeded
	Parked  int // events moved to the dead topic this call
}

// Consume polls and handles events with poison-message semantics. A
// failing handler returns an error and leaves the offset behind — unless
// this was the event's MaxDeliveries-th attempt, in which case the event
// is parked on DeadTopic(topic), its offset commits, and the batch
// continues. Order is preserved: an event failing below the parking
// threshold stops the batch exactly like plain Consume.
func (d *DeadLetter) Consume(group, topic string, limit int, handle func(Event) error) (ConsumeResult, error) {
	var res ConsumeResult
	events, err := d.Bus.Poll(group, topic, limit)
	if err != nil {
		return res, err
	}
	for _, ev := range events {
		key := group + "|" + topic + "|" + strconv.FormatInt(ev.Seq, 10)
		if err := handle(ev); err != nil {
			deliveries := d.Attempts.Inc(key)
			if deliveries < d.maxDeliveries() {
				return res, fmt.Errorf("handler failed at %s (delivery %d/%d — will redeliver): %w",
					ev.ID, deliveries, d.maxDeliveries(), err)
			}
			// Poison: park it, commit past it, keep the topic flowing.
			d.Attempts.Clear(key)
			dead := DeadLetterEvent{
				Original:   ev,
				Group:      group,
				Error:      err.Error(),
				Deliveries: deliveries,
				ParkedAt:   time.Now().UTC(),
			}
			if _, perr := d.Bus.Publish(DeadTopic(topic), dead); perr != nil {
				// Refuse to commit: parking must not become silent loss.
				return res, fmt.Errorf("parking %s failed (offset NOT committed): %w", ev.ID, perr)
			}
			if cerr := d.Bus.Commit(group, topic, ev.Seq); cerr != nil {
				return res, cerr
			}
			res.Parked++
			if d.OnDeadLetter != nil {
				d.OnDeadLetter(dead)
			}
			continue
		}
		d.Attempts.Clear(key)
		if err := d.Bus.Commit(group, topic, ev.Seq); err != nil {
			return res, err
		}
		res.Handled++
	}
	return res, nil
}

// Replay republishes up to limit parked events from deadTopic back to
// their original topic (payload only — the original gets a fresh seq, and
// consumers dedupe on payload IDs if they must). DLQ offsets commit per
// replayed event, so a crash mid-replay resumes safely. Returns the
// number replayed. The handler fix belongs to the operator before this
// runs — replaying onto a still-broken handler just re-poisons.
func (d *DeadLetter) Replay(group, deadTopic string, limit int) (int, error) {
	n, err := Consume(d.Bus, group, deadTopic, limit, func(ev Event) error {
		var dead DeadLetterEvent
		if err := json.Unmarshal(ev.Payload, &dead); err != nil {
			return fmt.Errorf("dead-letter envelope unreadable: %w", err)
		}
		var payload any
		if err := json.Unmarshal(dead.Original.Payload, &payload); err != nil {
			return fmt.Errorf("original payload unreadable: %w", err)
		}
		_, err := d.Bus.Publish(dead.Original.Topic, payload)
		return err
	})
	return n, err
}
