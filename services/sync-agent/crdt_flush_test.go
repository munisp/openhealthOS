package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/munisp/openhealthOS/shared/crdt"
)

type fakePoster struct {
	calls int
	body  []byte
	err   error
}

func (f *fakePoster) Post(b []byte) error { f.calls++; f.body = b; return f.err }

func writeReplica(t *testing.T, dir string, mutate func(r *crdt.Replica)) string {
	t.Helper()
	r := crdt.NewReplica("edge-test")
	mutate(r)
	raw, err := json.Marshal(r.Export())
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, "replica.json")
	if err := os.WriteFile(p, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestFlushReplicaUploadsOncePerChange(t *testing.T) {
	dir := t.TempDir()
	rp := writeReplica(t, dir, func(r *crdt.Replica) { r.Set("p1", "Patient", "name", "Aisha") })
	ack := filepath.Join(dir, "replica.ack")
	fp := &fakePoster{}

	up, err := FlushReplica(rp, ack, fp)
	if err != nil || !up || fp.calls != 1 {
		t.Fatalf("first flush: up=%v calls=%d err=%v", up, fp.calls, err)
	}
	// unchanged content: no re-upload
	up, _ = FlushReplica(rp, ack, fp)
	if up || fp.calls != 1 {
		t.Fatalf("unchanged replica must not re-upload: up=%v calls=%d", up, fp.calls)
	}
	// change content: uploads again
	writeReplica(t, dir, func(r *crdt.Replica) {
		r.Set("p1", "Patient", "name", "Aisha")
		r.Add("p1", "Patient", "immunizations", "BCG")
	})
	up, _ = FlushReplica(rp, ack, fp)
	if !up || fp.calls != 2 {
		t.Fatalf("changed replica must upload: up=%v calls=%d", up, fp.calls)
	}
}

func TestFlushReplicaFailureKeepsUnacked(t *testing.T) {
	dir := t.TempDir()
	rp := writeReplica(t, dir, func(r *crdt.Replica) { r.Set("p1", "Patient", "name", "X") })
	ack := filepath.Join(dir, "replica.ack")
	fp := &fakePoster{err: errors.New("no connectivity")}

	up, err := FlushReplica(rp, ack, fp)
	if err == nil || up {
		t.Fatal("offline must surface error, not pretend success")
	}
	// ack cursor must NOT be written — next pass retries
	if _, statErr := os.Stat(ack); !os.IsNotExist(statErr) {
		t.Fatal("ack written despite failed upload")
	}
	fp.err = nil
	up, err = FlushReplica(rp, ack, fp)
	if !up || err != nil {
		t.Fatalf("retry after connectivity must upload: %v", err)
	}
}

func TestFlushReplicaRefusesCorruptFile(t *testing.T) {
	dir := t.TempDir()
	rp := filepath.Join(dir, "replica.json")
	os.WriteFile(rp, []byte("{not json"), 0o600)
	fp := &fakePoster{}
	if _, err := FlushReplica(rp, filepath.Join(dir, "a.ack"), fp); err == nil || fp.calls != 0 {
		t.Fatal("corrupt replica must never reach the wire")
	}
}

func TestFlushReplicaMissingFileIsNotError(t *testing.T) {
	dir := t.TempDir()
	fp := &fakePoster{}
	up, err := FlushReplica(filepath.Join(dir, "nope.json"), filepath.Join(dir, "a.ack"), fp)
	if err != nil || up || fp.calls != 0 {
		t.Fatalf("missing replica must be a no-op: %v", err)
	}
}
