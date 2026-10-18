package main

import (
	"bytes"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/munisp/openhealthOS/shared/sealx"
)

// Sender uploads one packed batch. Returns nil on acknowledged delivery.
type Sender interface {
	Send(payload []byte) error
}

// HTTPSender POSTs the batch to the state plane ingest endpoint.
type HTTPSender struct {
	URL    string
	Client *http.Client
}

func (s HTTPSender) Send(payload []byte) error {
	c := s.Client
	if c == nil {
		c = &http.Client{Timeout: 30 * time.Second}
	}
	resp, err := c.Post(s.URL, "application/gzip", bytes.NewReader(payload))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("ingest returned %s", resp.Status)
	}
	return nil
}

// FlushOnce packs and sends batches until the outbox is drained or a send
// fails. Acknowledged events are removed from the outbox; anything unacked
// stays for the next pass. At-least-once delivery, no silent loss.
func FlushOnce(outbox string, sender Sender) (int, error) {
	return FlushOnceS(outbox, sender, nil)
}

// FlushOnceS is FlushOnce with a sealer for at-rest-encrypted outboxes.
func FlushOnceS(outbox string, sender Sender, sealer *sealx.Sealer) (int, error) {
	sent := 0
	for {
		events, err := ScanOutbox(outbox)
		if err != nil {
			return sent, err
		}
		if len(events) == 0 {
			return sent, nil
		}
		payload, included, err := PackBatchS(events, MaxBatchBytes, sealer)
		if err != nil {
			return sent, err
		}
		if len(included) == 0 {
			return sent, nil
		}
		if err := sender.Send(payload); err != nil {
			return sent, err // stop: outbox keeps everything unacked
		}
		for _, ev := range included {
			if err := os.Remove(ev.Path); err != nil {
				return sent, fmt.Errorf("acked but could not clear %s: %w", ev.Path, err)
			}
			sent++
		}
	}
}

func main() {
	outbox := envOr("OUTBOX", "./facility.outbox")
	ingest := envOr("INGEST_URL", "http://127.0.0.1:8443/ingest")
	interval := 60 * time.Second
	if err := os.MkdirAll(filepath.Clean(outbox), 0o755); err != nil {
		log.Fatal(err)
	}
	sender := HTTPSender{URL: ingest}
	// Optional CRDT replica flush (entity state sync, alongside events).
	replica := os.Getenv("REPLICA_FILE")
	var poster ReplicaPoster
	if replica != "" {
		merge := envOr("MERGE_URL", "http://127.0.0.1:8443/sync/merge")
		poster = HTTPReplicaPoster{URL: merge}
		log.Printf("sync-agent: replica=%s merge=%s", replica, merge)
	}
	// At-rest encryption (ADR-0013): SEALX_KEY/SEALX_KEY_FILE seals the
	// outbox on disk. Unset = plaintext dev mode, logged loudly.
	sealer, serr := sealx.FromEnv(os.Getenv)
	if serr != nil {
		log.Fatalf("sealx: %v", serr)
	}
	if sealer == nil {
		log.Print("sealx: no key provisioned — outbox is PLAINTEXT (set SEALX_KEY for production)")
	} else {
		log.Print("sealx: outbox encrypted at rest")
	}
	log.Printf("sync-agent: outbox=%s ingest=%s interval=%s cap=%d bytes", outbox, ingest, interval, MaxBatchBytes)
	for {
		if n, err := SealOutbox(outbox, sealer); err != nil {
			log.Printf("seal pass: sealed=%d err=%v", n, err)
		} else if n > 0 {
			log.Printf("seal pass: sealed=%d event files", n)
		}
		n, err := FlushOnceS(outbox, sender, sealer)
		if err != nil {
			log.Printf("sync pass: sent=%d err=%v (offline or ingest down — outbox retained)", n, err)
		} else if n > 0 {
			log.Printf("sync pass: sent=%d events", n)
		}
		if poster != nil {
			up, rerr := FlushReplica(replica, replica+".ack", poster)
			if rerr != nil {
				log.Printf("replica flush: %v (retained for retry)", rerr)
			} else if up {
				log.Print("replica flush: uploaded changed state")
			}
		}
		time.Sleep(interval)
	}
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
