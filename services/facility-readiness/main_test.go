package main

import "testing"

func add(s *Store, facility, kind string, vals ...float64) {
	for _, v := range vals {
		s.Add(Reading{FacilityID: facility, Kind: kind, Value: v})
	}
}

func TestReadinessWeightedScore(t *testing.T) {
	s := NewStore()
	// Cold chain: 3 of 4 in range → 75.
	add(s, "PHC-1", KindColdchain, 4, 5, 6, 12)
	// Power: 100% up → 100.
	add(s, "PHC-1", KindPower, 1, 1, 1)
	// Water: 50% → 50.
	add(s, "PHC-1", KindWater, 1, 0)
	r := s.Readiness("PHC-1")
	want := (75.0*wCold + 100.0*wPow + 50.0*wWat) / (wCold + wPow + wWat)
	if r.Score != want {
		t.Fatalf("score: got %v want %v", r.Score, want)
	}
	if r.Components[KindColdchain].Good != 3 {
		t.Fatalf("coldchain good: %+v", r.Components[KindColdchain])
	}
	if r.Grade != "DEGRADED" && r.Grade != "READY" {
		t.Fatalf("unexpected grade %s", r.Grade)
	}
}

func TestReadinessUnknownWhenNoData(t *testing.T) {
	s := NewStore()
	r := s.Readiness("GHOST")
	if r.Score != -1 || r.Grade != "UNKNOWN" {
		t.Fatalf("no-data facility must be UNKNOWN, got %+v", r)
	}
}

func TestMissingComponentRedistributesWeight(t *testing.T) {
	s := NewStore()
	add(s, "PHC-2", KindPower, 1, 1) // only power, 100%
	r := s.Readiness("PHC-2")
	if r.Score != 100 || r.Grade != "READY" {
		t.Fatalf("power-only facility should score 100 on redistributed weight: %+v", r)
	}
	if r.Components[KindWater].Readings != 0 || r.Components[KindWater].Score != -1 {
		t.Fatalf("missing water sensor must be reported as unknown, not 0: %+v", r.Components[KindWater])
	}
}

func TestWindowBoundsMemory(t *testing.T) {
	s := NewStore()
	vals := make([]float64, windowSize+100)
	for i := range vals {
		vals[i] = 4
	}
	add(s, "PHC-3", KindColdchain, vals...)
	s.Add(Reading{FacilityID: "PHC-3", Kind: KindColdchain, Value: 99}) // one bad
	r := s.Readiness("PHC-3")
	if r.Components[KindColdchain].Readings != windowSize {
		t.Fatalf("window must cap at %d, got %d", windowSize, r.Components[KindColdchain].Readings)
	}
}
