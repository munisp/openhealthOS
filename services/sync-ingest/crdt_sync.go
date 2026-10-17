// CRDT entity replication (state-plane side) — complements the event log.
//
// Events (the /ingest path) are an immutable audit trail; docs (this path)
// are the *current state* of replicated entities — patients, encounters,
// tasks. Edge nodes POST crdt.Batch payloads; each doc is merged field-wise
// against the stored copy (LWW registers + add-only clinical sets +
// tombstones), so months of divergent offline work converge with no central
// arbitration and no lost facts (ADR-0001, ADR-0009).
package main

import (
	"encoding/json"
	"net/http"
	"sort"
	"sync"

	"github.com/munisp/openhealthOS/shared/crdt"
)

// DocStore is the replicated-entity persistence seam.
type DocStore interface {
	LoadDoc(id string) (*crdt.Doc, bool)
	StoreDoc(d *crdt.Doc)
	ListDocs() []*crdt.Doc
}

// MemoryDocStore is the default (dev/tests); production binds Postgres.
type MemoryDocStore struct {
	mu   sync.Mutex
	docs map[string]*crdt.Doc
}

func NewMemoryDocStore() *MemoryDocStore {
	return &MemoryDocStore{docs: map[string]*crdt.Doc{}}
}

func (m *MemoryDocStore) LoadDoc(id string) (*crdt.Doc, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	d, ok := m.docs[id]
	return d, ok
}

func (m *MemoryDocStore) StoreDoc(d *crdt.Doc) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.docs[d.ID] = d
}

func (m *MemoryDocStore) ListDocs() []*crdt.Doc {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]*crdt.Doc, 0, len(m.docs))
	for _, d := range m.docs {
		out = append(out, d)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// txMerger is implemented by the Postgres store: whole-batch merge in one
// transaction with row locks, so concurrent edge uploads serialise.
type txMerger interface {
	MergeBatchTx(b *crdt.Batch) (int, error)
}

// MergeBatch folds one incoming batch into the store. Merge is idempotent
// and commutative — redelivery after a crash is a no-op, never corruption.
func MergeBatch(store DocStore, b *crdt.Batch) (int, error) {
	if tx, ok := store.(txMerger); ok {
		return tx.MergeBatchTx(b)
	}
	merged := 0
	for _, remote := range b.Docs {
		if remote == nil || remote.ID == "" {
			continue
		}
		local, _ := store.LoadDoc(remote.ID)
		store.StoreDoc(crdt.MergeDocs(local, remote))
		merged++
	}
	return merged, nil
}

// crdtRoutes adds the doc-replication endpoints to mux.
func crdtRoutes(mux *http.ServeMux, docs DocStore) {
	mux.HandleFunc("/sync/merge", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var b crdt.Batch
		if err := json.NewDecoder(r.Body).Decode(&b); err != nil {
			http.Error(w, "bad batch json: "+err.Error(), http.StatusBadRequest)
			return
		}
		n, err := MergeBatch(docs, &b)
		if err != nil {
			http.Error(w, "merge failed: "+err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"merged": n, "from": b.From})
	})
	// /sync/docs is the read seam for the analytics reporter and debugging.
	mux.HandleFunc("/sync/docs", func(w http.ResponseWriter, _ *http.Request) {
		out := docs.ListDocs()
		mat := make([]map[string]any, 0, len(out))
		for _, d := range out {
			mat = append(mat, d.Materialize())
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(mat)
	})
}
