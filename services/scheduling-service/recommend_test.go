package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func seedTwoFacilities(t *testing.T) *MemoryStore {
	t.Helper()
	st := NewMemoryStore()
	now := time.Date(2026, 10, 20, 6, 0, 0, 0, time.UTC) // Tuesday
	mk := func(facility string, bookedEvery int) {
		sch, err := st.PutSchedule(Schedule{
			FacilityID: facility, Service: "anc", ResourceID: "MW-1", ResourceName: "midwife", ResourceType: "provider",
			Weekday: int(now.Weekday()), StartMin: 8 * 60, EndMin: 10 * 60,
			SlotMinutes: 30, Capacity: 1,
		})
		if err != nil {
			t.Fatal(err)
		}
		slots, err := st.Generate(sch.ID, now.Format("2006-01-02"), now)
		if err != nil {
			t.Fatal(err)
		}
		for i, sl := range slots {
			if bookedEvery > 0 && i%bookedEvery == 0 {
				if _, err := st.Book(sl.ID, "PT-x", "", nil, now); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	mk("F-BUSY", 1) // everything booked... wait, that leaves zero free — use 2 (half)
	mk("F-QUIET", 0)
	return st
}

func TestRecommendRanksEarliestThenQuietest(t *testing.T) {
	st := seedTwoFacilities(t)
	recs := recommend(st, "anc", "2026-10-20", "2026-10-21", nil)
	if len(recs) == 0 {
		t.Fatal("no recommendations")
	}
	if recs[0].FacilityID != "F-QUIET" {
		t.Fatalf("quiet facility with earliest free slot should win: %+v", recs)
	}
	for _, r := range recs {
		if r.NextFree == "" || r.FreeSlots == 0 {
			t.Fatalf("bad recommendation: %+v", r)
		}
	}
}

func TestRecommendSkipsFullyBooked(t *testing.T) {
	st := NewMemoryStore()
	now := time.Date(2026, 10, 20, 6, 0, 0, 0, time.UTC) // Tuesday
	sch, _ := st.PutSchedule(Schedule{
		FacilityID: "F-FULL", Service: "anc", ResourceID: "MW-2", ResourceName: "mw", ResourceType: "provider",
		Weekday: int(now.Weekday()), StartMin: 480, EndMin: 540, SlotMinutes: 30, Capacity: 1,
	})
	slots, _ := st.Generate(sch.ID, "2026-10-20", now)
	for _, sl := range slots {
		st.Book(sl.ID, "PT-x", "", nil, now)
	}
	recs := recommend(st, "anc", "2026-10-20", "2026-10-21", nil)
	if len(recs) != 0 {
		t.Fatalf("fully booked facility must not be recommended: %+v", recs)
	}
}

func TestRecommendEndpoint(t *testing.T) {
	st := seedTwoFacilities(t)
	srv := server{store: st}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /slots/recommend", srv.recommendSlots)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/slots/recommend?service=anc&from=2026-10-20&to=2026-10-21", nil))
	if rec.Code != 200 {
		t.Fatalf("code=%d %s", rec.Code, rec.Body)
	}
	var out struct {
		Recommendations []Recommendation `json:"recommendations"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if len(out.Recommendations) == 0 {
		t.Fatal("empty recommendations from seeded store")
	}
}

func TestRecommendValidatesParams(t *testing.T) {
	srv := server{store: NewMemoryStore()}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /slots/recommend", srv.recommendSlots)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/slots/recommend", nil))
	if rec.Code != 400 {
		t.Fatalf("missing params must 400, got %d", rec.Code)
	}
}
