import unittest

from kpiplane import (assign_catchments_capacity, coverage_gaps,
                      travel_minutes, ROAD_FACTOR, WALK_SPEED_KMH)

FACS = [
    {"id": "PHC-A", "name": "A", "latitude": 10.0, "longitude": 7.0, "beds": 1},
    {"id": "PHC-B", "name": "B", "latitude": 10.02, "longitude": 7.0, "beds": 10},
]


def member(lat, lng, lga="X"):
    return {"latitude": lat, "longitude": lng, "lga": lga}


class TestTravelMinutes(unittest.TestCase):
    def test_walking_under_threshold(self):
        # 2km walk at 4.5km/h ≈ 26.7 min
        self.assertAlmostEqual(travel_minutes(2.0), 2.0 / WALK_SPEED_KMH * 60, places=1)

    def test_motorised_uses_road_factor(self):
        # 10km rural: 14 road-km at 28km/h = 30 min
        self.assertAlmostEqual(travel_minutes(10.0, "rural"), 10 * ROAD_FACTOR / 28 * 60, places=1)

    def test_urban_slower_per_km(self):
        self.assertGreater(travel_minutes(10.0, "urban"), travel_minutes(10.0, "periurban"))


class TestCapacityCatchments(unittest.TestCase):
    def test_overflow_spills_to_next_nearest(self):
        # PHC-A capacity = 1 bed x 30 = 30; 35 members near A → 5 spill to B.
        members = [member(10.0001, 7.0) for _ in range(35)]
        out = assign_catchments_capacity(members, FACS)
        by_id = {r["facility"]: r for r in out["facilities"]}
        self.assertEqual(by_id["PHC-A"]["assigned"], 30)
        self.assertEqual(by_id["PHC-B"]["assigned"], 5)
        self.assertEqual(out["overflow"], 5)

    def test_saturation_reported(self):
        members = [member(10.0001, 7.0) for _ in range(15)]
        out = assign_catchments_capacity(members, FACS)
        by_id = {r["facility"]: r for r in out["facilities"]}
        self.assertEqual(by_id["PHC-A"]["saturation_pct"], 50.0)

    def test_zero_bed_facility_takes_minimum_one_capacity(self):
        facs = [{"id": "Z", "latitude": 10.0, "longitude": 7.0, "beds": 0}]
        out = assign_catchments_capacity([member(10.0, 7.0)] * 5, facs)
        self.assertEqual(out["facilities"][0]["assigned"], 5)  # cap=max(1,0)*30=30

    def test_travel_minutes_in_rows(self):
        out = assign_catchments_capacity([member(10.01, 7.0)], FACS)
        self.assertIsNotNone(out["facilities"][0]["mean_travel_min"])

    def test_all_saturated_still_assigns(self):
        facs = [{"id": "A", "latitude": 10.0, "longitude": 7.0, "beds": 0}]
        members = [member(10.0, 7.0) for _ in range(31)]  # over cap 30
        out = assign_catchments_capacity(members, facs)
        self.assertEqual(out["facilities"][0]["assigned"], 31)
        self.assertEqual(out["overflow"], 1)


class TestCoverageGaps(unittest.TestCase):
    def test_beyond_threshold_flagged(self):
        members = [member(10.0, 7.0), member(12.5, 9.5, lga="Far")]
        out = coverage_gaps(members, FACS)
        self.assertEqual(out["gap_members"], 1)
        self.assertEqual(out["by_lga"], {"Far": 1})
        self.assertAlmostEqual(out["gap_share_pct"], 50.0)

    def test_no_facilities_is_nodata(self):
        out = coverage_gaps([member(1, 1)], [])
        self.assertEqual(out["status"], "no-data")

    def test_points_capped(self):
        facs = [{"id": "A", "latitude": 0.0, "longitude": 0.0}]
        members = [member(30.0 + i * 0.001, 30.0) for i in range(6000)]
        out = coverage_gaps(members, facs)
        self.assertEqual(len(out["points"]), 5000)
        self.assertEqual(out["gap_members"], 6000)


if __name__ == "__main__":
    unittest.main()
