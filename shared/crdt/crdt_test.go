package crdt

import (
	"encoding/json"
	"testing"
	"time"
)

func TestHLCTotalOrder(t *testing.T) {
	a := HLC{Wall: 100, Logic: 0, Node: "a"}
	b := HLC{Wall: 100, Logic: 1, Node: "a"}
	c := HLC{Wall: 100, Logic: 1, Node: "b"}
	d := HLC{Wall: 101, Logic: 0, Node: "a"}
	if !b.After(a) || !c.After(b) || !d.After(c) {
		t.Fatal("hlc ordering broken")
	}
	if a.After(a) {
		t.Fatal("hlc must be strictly ordered")
	}
}

func TestHLCParseRoundTrip(t *testing.T) {
	h := HLC{Wall: 1729000000000, Logic: 7, Node: "edge-kd-04"}
	got, err := ParseHLC(h.String())
	if err != nil || got != h {
		t.Fatalf("round trip: %v %v", got, err)
	}
	if _, err := ParseHLC("garbage"); err == nil {
		t.Fatal("expected parse error")
	}
}

func TestClockMonotonicAndCausal(t *testing.T) {
	c := NewClock("n1")
	c.nowFunc = func() int64 { return 1000 }
	s1 := c.Tick()
	s2 := c.Tick()
	if !s2.After(s1) {
		t.Fatal("same-wall ticks must advance logically")
	}
	remote := HLC{Wall: 5000, Logic: 3, Node: "n9"}
	s3 := c.Observe(remote)
	if s3.Wall != 5000 || s3.Logic != 4 {
		t.Fatalf("observe must move past remote, got %s", s3)
	}
	_ = time.Now // keep import if unused in future edits
}

func TestLWWMergeLatestWins(t *testing.T) {
	edge := NewReplica("edge-1")
	edge.Clock.nowFunc = func() int64 { return 100 }
	edge.Set("p1", "Patient", "name", "Aisha")

	state := NewReplica("state-1")
	state.Clock.nowFunc = func() int64 { return 200 }
	state.Set("p1", "Patient", "name", "Aisha Bello")

	edge.ApplyBatch(state.Export())
	name, ok := edge.Docs["p1"].GetString("name")
	if !ok || name != "Aisha Bello" {
		t.Fatalf("later write must win, got %q", name)
	}
	// And merging the stale copy back must not regress.
	state.ApplyBatch(edge.Export())
	name, _ = state.Docs["p1"].GetString("name")
	if name != "Aisha Bello" {
		t.Fatalf("stale write regressed state: %q", name)
	}
}

func TestConcurrentWritesDeterministic(t *testing.T) {
	mk := func(node string, wall int64) *Replica {
		r := NewReplica(node)
		r.Clock.nowFunc = func() int64 { return wall }
		r.Set("p1", "Patient", "phone", "from-"+node)
		return r
	}
	a, b := mk("a-node", 100), mk("b-node", 100) // same wall: node id decides
	a.ApplyBatch(b.Export())
	b.ApplyBatch(a.Export())
	va, _ := a.Docs["p1"].GetString("phone")
	vb, _ := b.Docs["p1"].GetString("phone")
	if va != vb {
		t.Fatalf("divergence after concurrent merge: %q vs %q", va, vb)
	}
}

func TestAddOnlySetUnionSurvivesPartition(t *testing.T) {
	edge1 := NewReplica("e1")
	edge1.Add("p1", "Patient", "immunizations", "BCG", "OPV0")
	edge2 := NewReplica("e2")
	edge2.Add("p1", "Patient", "immunizations", "Penta1")

	// partition heals in both directions
	edge1.ApplyBatch(edge2.Export())
	edge2.ApplyBatch(edge1.Export())

	want := []string{"BCG", "OPV0", "Penta1"}
	for i, r := range []*Replica{edge1, edge2} {
		got := r.Docs["p1"].SetMembers("immunizations")
		if len(got) != len(want) {
			t.Fatalf("replica %d set = %v", i, got)
		}
		for j := range want {
			if got[j] != want[j] {
				t.Fatalf("replica %d set = %v", i, got)
			}
		}
	}
}

func TestTombstoneHidesButMergeKeeps(t *testing.T) {
	a := NewReplica("a")
	a.Clock.nowFunc = func() int64 { return 1 }
	a.Set("p1", "Patient", "nickname", "Ada")
	a.Delete("p1", "Patient", "nickname")
	if _, ok := a.Docs["p1"].Get("nickname"); ok {
		t.Fatal("tombstoned field must read absent")
	}

	b := NewReplica("b")
	b.Clock.nowFunc = func() int64 { return 0 } // older writer
	b.Set("p1", "Patient", "nickname", "Ada-old")
	b.ApplyBatch(a.Export())
	if _, ok := b.Docs["p1"].Get("nickname"); ok {
		t.Fatal("older write must not resurrect tombstone")
	}

	// add-only sets refuse deletion outright
	a.Add("p1", "Patient", "allergies", "penicillin")
	a.Delete("p1", "Patient", "allergies")
	if got := a.Docs["p1"].SetMembers("allergies"); len(got) != 1 {
		t.Fatalf("add-only set erased: %v", got)
	}
}

func TestExportDeterministicAndMaterialize(t *testing.T) {
	r := NewReplica("n")
	r.Set("p2", "Patient", "name", "B")
	r.Set("p1", "Patient", "name", "A")
	r.Add("p1", "Patient", "immunizations", "BCG")
	b := r.Export()
	if b.Docs[0].ID != "p1" || b.Docs[1].ID != "p2" {
		t.Fatal("export must be id-sorted for byte-stable payloads")
	}
	raw, _ := json.Marshal(b)
	var b2 Batch
	if json.Unmarshal(raw, &b2) != nil {
		t.Fatal("batch must round-trip JSON")
	}
	m := r.Docs["p1"].Materialize()
	if m["name"] != "A" || m["id"] != "p1" {
		t.Fatalf("materialize: %v", m)
	}
}

func TestMergeIsIdempotentAndCommutative(t *testing.T) {
	a, b := NewReplica("a"), NewReplica("b")
	a.Clock.nowFunc = func() int64 { return 10 }
	b.Clock.nowFunc = func() int64 { return 20 }
	a.Set("d", "Encounter", "note", "edge-note")
	a.Add("d", "Encounter", "diagnoses", "malaria")
	b.Set("d", "Encounter", "status", "closed")
	b.Add("d", "Encounter", "diagnoses", "anaemia")

	ba, bb := a.Export(), b.Export()
	x, y := NewReplica("x"), NewReplica("y")
	x.ApplyBatch(ba)
	x.ApplyBatch(bb)
	x.ApplyBatch(ba) // duplicate delivery (store-and-forward retries)
	y.ApplyBatch(bb)
	y.ApplyBatch(ba)

	mx, my := x.Docs["d"].Materialize(), y.Docs["d"].Materialize()
	sx, _ := json.Marshal(mx)
	sy, _ := json.Marshal(my)
	if string(sx) != string(sy) {
		t.Fatalf("not commutative/idempotent:\n%s\n%s", sx, sy)
	}
	if mx["note"] != "edge-note" || mx["status"] != "closed" {
		t.Fatalf("lost fields: %v", mx)
	}
}
