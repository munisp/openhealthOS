// Hub HTTP surface + forwarding loop.
//
//	POST /ingest      — facility sync-agent batches (NDJSON, same as state plane)
//	POST /ingest/raw  — phone/PWA path, plain JSON-lines
//	GET  /events?facility=&type=&pending=1 — LGA-local read (dashboards)
//	GET  /stats       — queue depth per class, last upstream contact, forwards
//	GET  /healthz
//
// Forwarding drains to UPSTREAM_URL (state-plane sync-ingest) in batches,
// critical → standard → bulk, every FORWARD_INTERVAL_SECONDS (default 15).
// When the upstream is down the buffer just grows — nothing is lost, and
// /stats reports the outage age so the LGA dashboard can show it.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"

	"github.com/munisp/openhealthOS/shared/eventschema"
)

type hub struct {
	store    Store
	upstream string // empty = receive-only mode (top of a broken chain)
	hubID    string

	mu              sync.Mutex
	lastUpstreamOK  time.Time
	lastUpstreamErr string
	forwardedTotal  int
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// readNDJSON accepts both plain and gzip-encoded JSON-lines bodies.
func readNDJSON(r *http.Request) ([]Event, error) {
	var reader io.Reader = r.Body
	if strings.Contains(r.Header.Get("Content-Encoding"), "gzip") {
		gz, err := gzipNewReader(r.Body)
		if err != nil {
			return nil, err
		}
		defer gz.Close()
		reader = gz
	}
	var events []Event
	sc := bufio.NewScanner(reader)
	sc.Buffer(make([]byte, 1024*1024), 4*1024*1024)
	for sc.Scan() {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		var ev Event
		if err := json.Unmarshal(line, &ev); err != nil {
			return nil, err
		}
		if ev.ID == "" {
			return nil, errMissingID
		}
		// Contract registry (ADR-0013): malformed known types are rejected
		// at the hub too — the first tier that can stop bad data should.
		// Unregistered types pass (rolling upgrades); the state plane
		// counts them as warnings.
		if _, verr := eventschema.Validate(ev.Type, ev.TS, ev.Payload); verr != nil {
			return nil, fmt.Errorf("event %s: %w", ev.ID, verr)
		}
		events = append(events, ev)
	}
	return events, sc.Err()
}

var errMissingID = errString("event missing id")

type errString string

func (e errString) Error() string { return string(e) }

func (h *hub) ingest(w http.ResponseWriter, r *http.Request) {
	events, err := readNDJSON(r)
	if err != nil {
		http.Error(w, `{"error":"bad batch: `+err.Error()+`"}`, http.StatusBadRequest)
		return
	}
	received, dup := 0, 0
	for _, ev := range events {
		fresh, err := h.store.Add(ev)
		if err != nil {
			http.Error(w, `{"error":"store: `+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		if fresh {
			received++
		} else {
			dup++
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"received": received, "duplicates": dup, "hub": h.hubID,
	})
}

// forwardOnce drains up to batchSize events per class, critical first.
// Returns total forwarded this pass.
func (h *hub) forwardOnce(batchSize int) int {
	if h.upstream == "" {
		return 0
	}
	total := 0
	for _, class := range classOrder {
		pending := h.store.Pending(class, batchSize)
		if len(pending) == 0 {
			continue
		}
		var buf bytes.Buffer
		ids := make([]string, 0, len(pending))
		for _, e := range pending {
			line, _ := json.Marshal(e.Ev)
			buf.Write(line)
			buf.WriteByte('\n')
			ids = append(ids, e.Ev.ID)
		}
		client := &http.Client{Timeout: 10 * time.Second}
		resp, err := client.Post(h.upstream+"/ingest/raw", "application/x-ndjson", &buf)
		if err != nil || resp.StatusCode >= 300 {
			h.mu.Lock()
			h.lastUpstreamErr = time.Now().UTC().Format(time.RFC3339)
			h.mu.Unlock()
			if resp != nil {
				resp.Body.Close()
			}
			return total // upstream down: stop this pass, buffer grows
		}
		resp.Body.Close()
		if err := h.store.MarkForwarded(ids); err != nil {
			log.Printf("lga-hub: mark forwarded: %v", err)
			return total
		}
		total += len(ids)
		h.mu.Lock()
		h.lastUpstreamOK = time.Now()
		h.lastUpstreamErr = ""
		h.forwardedTotal += len(ids)
		h.mu.Unlock()
	}
	return total
}

func (h *hub) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /ingest", h.ingest)
	mux.HandleFunc("POST /ingest/raw", h.ingest)
	mux.HandleFunc("GET /events", func(w http.ResponseWriter, r *http.Request) {
		fac := r.URL.Query().Get("facility")
		typ := r.URL.Query().Get("type")
		pendingOnly := r.URL.Query().Get("pending") == "1"
		var out []entry
		for _, e := range h.store.Events() {
			if fac != "" && e.Ev.Facility != fac {
				continue
			}
			if typ != "" && e.Ev.Type != typ {
				continue
			}
			if pendingOnly && e.Forwarded {
				continue
			}
			out = append(out, e)
		}
		if out == nil {
			out = []entry{}
		}
		writeJSON(w, http.StatusOK, out)
	})
	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, _ *http.Request) {
		c, s, b := h.store.PendingCounts()
		h.mu.Lock()
		ok, errAt, fwd := h.lastUpstreamOK, h.lastUpstreamErr, h.forwardedTotal
		h.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]any{
			"hub":                 h.hubID,
			"pending_critical":    c,
			"pending_standard":    s,
			"pending_bulk":        b,
			"forwarded_total":     fwd,
			"upstream":            h.upstream,
			"last_upstream_ok":    ok.Format(time.RFC3339),
			"last_upstream_error": errAt,
		})
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok"))
	})
	return mux
}

func main() {
	var store Store
	var err error
	if spill := os.Getenv("SPILL_PATH"); spill != "" {
		store, err = NewFileStore(spill)
	} else {
		store = NewMemoryStore()
	}
	if err != nil {
		panic(err)
	}
	hubID := os.Getenv("HUB_ID")
	if hubID == "" {
		hubID = "lga-hub-dev"
	}
	h := &hub{store: store, upstream: strings.TrimRight(os.Getenv("UPSTREAM_URL"), "/"), hubID: hubID}

	interval := 15 * time.Second
	if v := os.Getenv("FORWARD_INTERVAL_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 1 {
			interval = time.Duration(n) * time.Second
		}
	}
	if h.upstream != "" {
		go func() {
			for range time.Tick(interval) {
				if n := h.forwardOnce(500); n > 0 {
					log.Printf("lga-hub: forwarded %d events upstream", n)
				}
			}
		}()
	}

	addr := ":8091"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	var handler http.Handler = h.routes()
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
	}
	mode := "receive-only"
	if h.upstream != "" {
		mode = "forwarding to " + h.upstream
	}
	log.Printf("lga-hub: %s listening on %s (%s)", hubID, addr, mode)
	if err := servex.ListenAndServe(addr, handler, servex.Options{}); err != nil {
		panic(err)
	}
}
