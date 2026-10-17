// CRDT replica flush — the state-sync counterpart of the event outbox.
//
// The local data plane (PWA, edge services) keeps a crdt.Replica file on
// disk (REPLICA_FILE). The agent hashes it, and when the content changed
// since the last acknowledged upload, POSTs the export batch to the state
// plane /sync/merge. Uploads are idempotent on the server (merge is a
// no-op on redelivery), so at-least-once is safe here too.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/munisp/openhealthOS/shared/crdt"
)

// ReplicaPoster uploads one serialised batch.
type ReplicaPoster interface {
	Post(batch []byte) error
}

// HTTPReplicaPoster POSTs to the state-plane merge endpoint.
type HTTPReplicaPoster struct {
	URL    string
	Client *http.Client
}

func (p HTTPReplicaPoster) Post(batch []byte) error {
	c := p.Client
	if c == nil {
		c = &http.Client{Timeout: 30 * time.Second}
	}
	resp, err := c.Post(p.URL, "application/json", bytes.NewReader(batch))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("merge endpoint returned %s", resp.Status)
	}
	return nil
}

// LoadReplica reads a replica file (a serialised crdt.Batch as exported by
// the local data plane). A missing file is not an error — the edge may
// legitimately have nothing yet.
func LoadReplica(path string) (*crdt.Batch, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var b crdt.Batch
	if err := json.Unmarshal(raw, &b); err != nil {
		return nil, fmt.Errorf("replica file corrupt: %w", err)
	}
	return &b, nil
}

// FlushReplica uploads the replica when it changed since the last ack.
// ackFile persists the last acknowledged content hash across restarts.
// Returns true when an upload happened.
func FlushReplica(path, ackFile string, poster ReplicaPoster) (bool, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	// validate before shipping — never push corrupt state upstream
	var b crdt.Batch
	if err := json.Unmarshal(raw, &b); err != nil {
		return false, fmt.Errorf("replica file corrupt: %w", err)
	}
	sum := sha256.Sum256(raw)
	hash := hex.EncodeToString(sum[:])
	if ack, err := os.ReadFile(ackFile); err == nil && string(ack) == hash {
		return false, nil // unchanged since last ack
	}
	if err := poster.Post(raw); err != nil {
		return false, err
	}
	if err := os.WriteFile(ackFile, []byte(hash), 0o600); err != nil {
		return true, fmt.Errorf("acked but could not persist ack cursor: %w", err)
	}
	return true, nil
}
