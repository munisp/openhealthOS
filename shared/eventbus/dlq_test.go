package eventbus

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
)

// A poison message is retried MaxDeliveries-1 times, then parked on the
// dead topic; the offset commits and the events behind it flow.
func TestPoisonMessageParksAndTopicFlows(t *testing.T) {
	bus := NewMemoryBus()
	dlq := NewDeadLetter(bus, nil)
	dlq.MaxDeliveries = 3
	var parked []DeadLetterEvent
	dlq.OnDeadLetter = func(d DeadLetterEvent) { parked = append(parked, d) }

	bus.Publish("claims", map[string]string{"id": "poison"})
	bus.Publish("claims", map[string]string{"id": "good"})

	handle := func(ev Event) error {
		var p map[string]string
		json.Unmarshal(ev.Payload, &p)
		if p["id"] == "poison" {
			return errors.New("unprocessable")
		}
		return nil
	}

	// Attempts 1 and 2: redelivery, topic blocked behind the poison.
	for want := 1; want <= 2; want++ {
		res, err := dlq.Consume("g", "claims", 10, handle)
		if err == nil || !strings.Contains(err.Error(), "will redeliver") {
			t.Fatalf("attempt %d: want redeliver error, got %v", want, err)
		}
		if res.Handled != 0 || res.Parked != 0 {
			t.Fatalf("attempt %d: %+v", want, res)
		}
	}
	// Attempt 3: parked, offset commits, "good" flows in the same batch.
	res, err := dlq.Consume("g", "claims", 10, handle)
	if err != nil {
		t.Fatalf("parking consume: %v", err)
	}
	if res.Parked != 1 || res.Handled != 1 {
		t.Fatalf("want 1 parked + 1 handled, got %+v", res)
	}
	if len(parked) != 1 {
		t.Fatalf("OnDeadLetter fired %d times", len(parked))
	}
	if parked[0].Deliveries != 3 || parked[0].Group != "g" || !strings.Contains(parked[0].Error, "unprocessable") {
		t.Fatalf("envelope: %+v", parked[0])
	}
	// The dead topic holds the envelope with the original event intact.
	dead, _ := bus.Poll("ops", DeadTopic("claims"), 10)
	if len(dead) != 1 {
		t.Fatalf("dead topic len = %d", len(dead))
	}
	var env DeadLetterEvent
	if err := json.Unmarshal(dead[0].Payload, &env); err != nil {
		t.Fatal(err)
	}
	if env.Original.Topic != "claims" || env.Original.Seq != 1 {
		t.Fatalf("original: %+v", env.Original)
	}
	// Nothing left on the main topic.
	if off := bus.Offset("g", "claims"); off != 2 {
		t.Fatalf("offset = %d, want 2", off)
	}
}

// A transient failure below the threshold heals itself: the handler
// succeeds on a later delivery and no parking happens.
func TestTransientFailureHeals(t *testing.T) {
	bus := NewMemoryBus()
	dlq := NewDeadLetter(bus, nil)
	dlq.MaxDeliveries = 3
	bus.Publish("t", map[string]string{"id": "flaky"})

	calls := 0
	handle := func(ev Event) error {
		calls++
		if calls < 3 {
			return errors.New("downstream hiccup")
		}
		return nil
	}
	if _, err := dlq.Consume("g", "t", 1, handle); err == nil {
		t.Fatal("want error on delivery 1")
	}
	if _, err := dlq.Consume("g", "t", 1, handle); err == nil {
		t.Fatal("want error on delivery 2")
	}
	res, err := dlq.Consume("g", "t", 1, handle)
	if err != nil {
		t.Fatalf("delivery 3 should succeed: %v", err)
	}
	if res.Handled != 1 || res.Parked != 0 {
		t.Fatalf("%+v", res)
	}
	dead, _ := bus.Poll("ops", DeadTopic("t"), 10)
	if len(dead) != 0 {
		t.Fatal("nothing should be parked")
	}
	// Success cleared the attempt counter: a later failure starts at 1.
	bus.Publish("t", map[string]string{"id": "flaky2"})
	if _, err := dlq.Consume("g", "t", 1, func(Event) error { return errors.New("x") }); err == nil ||
		!strings.Contains(err.Error(), "delivery 1/") {
		t.Fatalf("attempt counter not cleared: %v", err)
	}
}

// Replay puts parked events back on their original topic.
func TestReplay(t *testing.T) {
	bus := NewMemoryBus()
	dlq := NewDeadLetter(bus, nil)
	dlq.MaxDeliveries = 1 // park on first failure
	bus.Publish("claims", map[string]string{"id": "c1", "n": "1"})

	if _, err := dlq.Consume("g", "claims", 10, func(Event) error { return errors.New("boom") }); err != nil {
		t.Fatal(err)
	}
	dead, _ := bus.Poll("ops", DeadTopic("claims"), 10)
	if len(dead) != 1 {
		t.Fatalf("dead len = %d", len(dead))
	}
	n, err := dlq.Replay("replayer", DeadTopic("claims"), 10)
	if err != nil || n != 1 {
		t.Fatalf("replay: n=%d err=%v", n, err)
	}
	// Replayed event is back on the main topic under a new seq.
	got, _ := bus.Poll("g2", "claims", 10)
	if len(got) != 2 { // original + replayed
		t.Fatalf("main topic len = %d", len(got))
	}
	var p map[string]string
	json.Unmarshal(got[1].Payload, &p)
	if p["id"] != "c1" {
		t.Fatalf("replayed payload: %v", p)
	}
	// DLQ consumed.
	if off := bus.Offset("replayer", DeadTopic("claims")); off != 1 {
		t.Fatalf("replayer offset = %d", off)
	}
}

// Parking must never become silent loss: if publishing to the dead topic
// fails, the offset stays behind.
type flakyBus struct {
	Bus
	failDeadPublish bool
}

func (f *flakyBus) Publish(topic string, payload any) (Event, error) {
	if f.failDeadPublish && strings.HasSuffix(topic, ".dead") {
		return Event{}, errors.New("disk full")
	}
	return f.Bus.Publish(topic, payload)
}

func TestParkingFailureKeepsOffset(t *testing.T) {
	bus := &flakyBus{Bus: NewMemoryBus(), failDeadPublish: true}
	dlq := NewDeadLetter(bus, nil)
	dlq.MaxDeliveries = 1
	bus.Publish("t", map[string]string{"id": "x"})
	_, err := dlq.Consume("g", "t", 1, func(Event) error { return errors.New("poison") })
	if err == nil || !strings.Contains(err.Error(), "NOT committed") {
		t.Fatalf("want parking failure, got %v", err)
	}
	if off := bus.Offset("g", "t"); off != 0 {
		t.Fatalf("offset moved to %d despite parking failure", off)
	}
}

// FileAttempts persists counts across wrapper instances (restart-safe).
func TestFileAttemptsPersist(t *testing.T) {
	dir := t.TempDir()
	s1, err := NewFileAttempts(dir)
	if err != nil {
		t.Fatal(err)
	}
	if n := s1.Inc("g|t|7"); n != 1 {
		t.Fatalf("inc = %d", n)
	}
	if n := s1.Inc("g|t|7"); n != 2 {
		t.Fatalf("inc = %d", n)
	}
	// New instance over the same dir (process restart): count continues.
	s2, _ := NewFileAttempts(dir)
	if n := s2.Inc("g|t|7"); n != 3 {
		t.Fatalf("after restart inc = %d, want 3", n)
	}
	s2.Clear("g|t|7")
	if n := s2.Inc("g|t|7"); n != 1 {
		t.Fatalf("after clear inc = %d, want 1", n)
	}
	if !strings.HasSuffix(filepath.Join(dir, "g.t.7"), "g.t.7") {
		t.Fatal("naming")
	}
}
