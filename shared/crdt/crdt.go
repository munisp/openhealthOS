package crdt

import (
	"encoding/json"
	"sort"
)

// FieldKind selects the merge law for a field.
type FieldKind string

const (
	// LWW is a last-writer-wins register: the field with the dominating
	// HLC wins outright. Default for scalar facts (name, status).
	LWW FieldKind = "lww"
	// AddOnly is a grow-only set: union on merge, nothing is ever removed.
	// Used for clinical facts where deletion is clinically wrong —
	// administered immunisations, recorded allergies, danger signs.
	AddOnly FieldKind = "set"
)

// Field is one replicated value with its provenance.
type Field struct {
	Kind  FieldKind       `json:"k"`
	Value json.RawMessage `json:"v"`           // scalar (lww) or JSON array (set)
	Stamp HLC             `json:"s"`           // last write
	Tomb  bool            `json:"t,omitempty"` // deleted (lww only)
}

// Doc is one replicated entity (patient, encounter, task…).
type Doc struct {
	ID     string            `json:"id"`
	Type   string            `json:"type"` // e.g. "Patient", "Encounter"
	Fields map[string]*Field `json:"fields"`
}

// Replica is a node's full document set plus its clock.
type Replica struct {
	Clock *Clock
	Docs  map[string]*Doc
}

// NewReplica returns an empty replica for node.
func NewReplica(node string) *Replica {
	return &Replica{Clock: NewClock(node), Docs: map[string]*Doc{}}
}

func (r *Replica) doc(id, typ string) *Doc {
	d, ok := r.Docs[id]
	if !ok {
		d = &Doc{ID: id, Type: typ, Fields: map[string]*Field{}}
		r.Docs[id] = d
	}
	return d
}

// Set writes a scalar field (LWW).
func (r *Replica) Set(id, typ, field string, value any) {
	raw, _ := json.Marshal(value)
	d := r.doc(id, typ)
	d.Fields[field] = &Field{Kind: LWW, Value: raw, Stamp: r.Clock.Tick()}
}

// Add appends members to an add-only set field. Duplicates collapse on
// merge; locally we keep the set canonical too.
func (r *Replica) Add(id, typ, field string, members ...string) {
	d := r.doc(id, typ)
	f, ok := d.Fields[field]
	if !ok {
		f = &Field{Kind: AddOnly, Value: json.RawMessage("[]")}
		d.Fields[field] = f
	}
	set := f.setMembers()
	for _, m := range members {
		set[m] = struct{}{}
	}
	f.Value = marshalSet(set)
	f.Stamp = r.Clock.Tick()
}

// Delete tombstones an LWW field. Add-only sets cannot be deleted —
// clinical facts are retracted by recording a new fact, never erased.
func (r *Replica) Delete(id, typ, field string) {
	d := r.doc(id, typ)
	f, ok := d.Fields[field]
	if !ok {
		f = &Field{Kind: LWW, Value: json.RawMessage("null")}
		d.Fields[field] = f
	}
	if f.Kind == AddOnly {
		return // refuse: add-only sets are never erased
	}
	f.Tomb = true
	f.Stamp = r.Clock.Tick()
}

// Merge folds other into r, field by field, and observes every remote
// stamp so subsequent local writes dominate causally.
func (r *Replica) Merge(other *Doc) {
	d := r.doc(other.ID, other.Type)
	if d.Type == "" {
		d.Type = other.Type
	}
	for name, remote := range other.Fields {
		r.Clock.Observe(remote.Stamp)
		local, ok := d.Fields[name]
		if !ok || remote.Stamp.After(local.Stamp) && remote.Kind != AddOnly {
			cp := *remote
			d.Fields[name] = &cp
			continue
		}
		if local.Kind == AddOnly || remote.Kind == AddOnly {
			// Union regardless of stamp: a fact recorded anywhere counts.
			set := local.setMembers()
			for m := range remote.setMembers() {
				set[m] = struct{}{}
			}
			local.Kind = AddOnly
			local.Value = marshalSet(set)
			if remote.Stamp.After(local.Stamp) {
				local.Stamp = remote.Stamp
			}
			continue
		}
		// local dominates: keep
	}
}

// MergeDocs merges remote into local (either may be nil) and returns the
// merged doc — a pure function for store-side merge (sync-ingest), where a
// full Replica is not held. Stamp observation is unnecessary here because
// the result is persisted, not used to issue new writes.
func MergeDocs(local, remote *Doc) *Doc {
	if local == nil {
		cp := *remote
		return &cp
	}
	if remote == nil {
		cp := *local
		return &cp
	}
	r := &Replica{Clock: NewClock("merge"), Docs: map[string]*Doc{local.ID: local}}
	r.Merge(remote)
	return r.Docs[local.ID]
}

// Batch is the wire unit between edge and state plane.
type Batch struct {
	From string `json:"from"`
	Docs []*Doc `json:"docs"`
}

// Export packages all docs for shipment. (Delta sync can filter by
// vector clock later; full-state export stays correct under merge, and
// payloads compress well below the 1 MB/facility-day budget.)
func (r *Replica) Export() *Batch {
	b := &Batch{From: r.Clock.node}
	ids := make([]string, 0, len(r.Docs))
	for id := range r.Docs {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		b.Docs = append(b.Docs, r.Docs[id])
	}
	return b
}

// ApplyBatch merges a received batch. Returns the number of docs touched.
func (r *Replica) ApplyBatch(b *Batch) int {
	for _, d := range b.Docs {
		r.Merge(d)
	}
	return len(b.Docs)
}

// Get reads a scalar field; ok=false if absent or tombstoned.
func (d *Doc) Get(field string) (json.RawMessage, bool) {
	f, ok := d.Fields[field]
	if !ok || f.Tomb {
		return nil, false
	}
	return f.Value, true
}

// GetString is the convenience scalar accessor.
func (d *Doc) GetString(field string) (string, bool) {
	raw, ok := d.Get(field)
	if !ok {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return "", false
	}
	return s, true
}

// SetMembers lists an add-only set field, sorted for determinism.
func (d *Doc) SetMembers(field string) []string {
	f, ok := d.Fields[field]
	if !ok {
		return nil
	}
	set := f.setMembers()
	out := make([]string, 0, len(set))
	for m := range set {
		out = append(out, m)
	}
	sort.Strings(out)
	return out
}

func (f *Field) setMembers() map[string]struct{} {
	set := map[string]struct{}{}
	var arr []string
	if json.Unmarshal(f.Value, &arr) == nil {
		for _, m := range arr {
			set[m] = struct{}{}
		}
	}
	return set
}

func marshalSet(set map[string]struct{}) json.RawMessage {
	arr := make([]string, 0, len(set))
	for m := range set {
		arr = append(arr, m)
	}
	sort.Strings(arr)
	raw, _ := json.Marshal(arr)
	return raw
}

// Materialize flattens a doc into plain JSON for FHIR mapping / UI.
func (d *Doc) Materialize() map[string]any {
	out := map[string]any{"id": d.ID, "type": d.Type}
	names := make([]string, 0, len(d.Fields))
	for n := range d.Fields {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		f := d.Fields[n]
		if f.Tomb {
			continue
		}
		var v any
		if json.Unmarshal(f.Value, &v) == nil {
			out[n] = v
		}
	}
	return out
}
