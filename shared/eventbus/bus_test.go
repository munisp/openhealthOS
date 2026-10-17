package eventbus

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestMemoryBusPubSub(t *testing.T) {
	b := NewMemoryBus()
	ev, err := b.Publish("claims", map[string]string{"decision": "APPROVE"})
	if err != nil || ev.Seq != 1 || ev.ID != "claims-1" {
		t.Fatalf("publish: %v %v", ev, err)
	}
	got, _ := b.Poll("analytics", "claims", 10)
	if len(got) != 1 {
		t.Fatalf("poll: %v", got)
	}
	var payload map[string]string
	json.Unmarshal(got[0].Payload, &payload)
	if payload["decision"] != "APPROVE" {
		t.Fatalf("payload: %v", payload)
	}
	// before commit, redelivery
	got, _ = b.Poll("analytics", "claims", 10)
	if len(got) != 1 {
		t.Fatal("uncommitted offset must redeliver")
	}
	b.Commit("analytics", "claims", 1)
	got, _ = b.Poll("analytics", "claims", 10)
	if len(got) != 0 {
		t.Fatal("committed offset must not redeliver")
	}
	// a second group has its own offset
	got, _ = b.Poll("billing", "claims", 10)
	if len(got) != 1 {
		t.Fatal("consumer groups are independent")
	}
}

func TestFileBusSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	b1, err := NewFileBus(dir)
	if err != nil {
		t.Fatal(err)
	}
	b1.Publish("telemetry", map[string]any{"temp": 4.2})
	b1.Publish("telemetry", map[string]any{"temp": 9.8})
	b1.Commit("coldchain", "telemetry", 1)

	// simulate process restart: new bus over same dir
	b2, _ := NewFileBus(dir)
	if off := b2.Offset("coldchain", "telemetry"); off != 1 {
		t.Fatalf("offset lost across restart: %d", off)
	}
	got, _ := b2.Poll("coldchain", "telemetry", 10)
	if len(got) != 1 || got[0].Seq != 2 {
		t.Fatalf("events lost or offset wrong after restart: %v", got)
	}
}

func TestFileBusTornTailSkipped(t *testing.T) {
	dir := t.TempDir()
	b, _ := NewFileBus(dir)
	b.Publish("t", map[string]string{"a": "1"})
	// simulate crash mid-append: garbage tail without closing brace
	path := filepath.Join(dir, "topics", "t.jsonl")
	fh, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	fh.Write([]byte(`{"topic":"t","seq":2,"payload":{"broken`))
	fh.Close()
	got, err := b.Poll("g", "t", 10)
	if err != nil || len(got) != 1 {
		t.Fatalf("torn tail must be skipped, intact prefix kept: %v %v", got, err)
	}
}

func TestConsumeCommitsOnlyAfterHandlerSuccess(t *testing.T) {
	b := NewMemoryBus()
	b.Publish("jobs", "one")
	b.Publish("jobs", "two")

	failOn := map[string]bool{"two": true}
	n, err := Consume(b, "w", "jobs", 10, func(ev Event) error {
		var s string
		json.Unmarshal(ev.Payload, &s)
		if failOn[s] {
			return errors.New("boom")
		}
		return nil
	})
	if n != 1 || err == nil {
		t.Fatalf("handler failure must stop before commit: n=%d err=%v", n, err)
	}
	if off := b.Offset("w", "jobs"); off != 1 {
		t.Fatalf("offset committed past failing event: %d", off)
	}
	// retry: redelivers the failed event, then succeeds
	delete(failOn, "two")
	n, err = Consume(b, "w", "jobs", 10, func(ev Event) error { return nil })
	if n != 1 || err != nil || b.Offset("w", "jobs") != 2 {
		t.Fatalf("redelivery broken: n=%d off=%d err=%v", n, b.Offset("w", "jobs"), err)
	}
}

func TestUnsafeNamesRejected(t *testing.T) {
	b, _ := NewFileBus(t.TempDir())
	if _, err := b.Publish("../etc", nil); err == nil {
		t.Fatal("path traversal topic accepted")
	}
	if err := b.Commit("g", "..", 1); err == nil {
		t.Fatal("path traversal offset accepted")
	}
}
