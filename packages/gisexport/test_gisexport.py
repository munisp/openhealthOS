import unittest

import gisexport as gx


FACS = [
    {"id": "PHC-1", "name": "Ikeja PHC", "kind": "phc", "lat": 6.60, "lng": 3.35,
     "beds": 12, "services": ["immunization", "anc"]},
    {"id": "GH-1", "name": "Lagos GH", "kind": "general", "lat": 6.45, "lng": 3.40,
     "beds": 120, "services": ["emergency", "surgery"]},
]


def households(n, lat=6.55, lng=3.37, members=4, jitter=0.0):
    return [{"lat": lat + i * jitter, "lng": lng + i * jitter, "members": members}
            for i in range(n)]


class TestGeoJSON(unittest.TestCase):
    def test_facilities_layer_valid_geojson(self):
        fc = gx.facilities_layer(FACS)
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertEqual(len(fc["features"]), 2)
        f = fc["features"][0]
        self.assertEqual(f["geometry"]["coordinates"], [3.35, 6.60])  # lng, lat
        self.assertEqual(f["properties"]["services"], "immunization;anc")

    def test_coordinate_validation(self):
        with self.assertRaises(gx.Error):
            gx.facilities_layer([{"id": "X", "name": "x", "kind": "phc",
                                  "lat": 95.0, "lng": 3.0}])

    def test_household_density_k_anonymity(self):
        dense = households(6)                      # one cell, 6 households -> kept
        sparse = [{"lat": 7.50, "lng": 4.50, "members": 3}]  # lone -> suppressed
        fc = gx.households_density_layer(dense + sparse)
        self.assertEqual(len(fc["features"]), 1)
        self.assertEqual(fc["features"][0]["properties"]["households"], 6)
        self.assertNotIn("7.5", str(fc))  # suppressed point leaks nothing

    def test_no_raw_points_in_density_layer(self):
        fc = gx.households_density_layer(households(10, jitter=0.001))
        for f in fc["features"]:
            self.assertIn("households", f["properties"])
            self.assertNotIn("id", f["properties"])

    def test_response_time_layer(self):
        incs = [{"lat": 6.55 + i * 0.001, "lng": 3.37, "response_min": t}
                for i, t in enumerate([10, 20, 30, 40, 50])]
        fc = gx.response_time_layer(incs)
        self.assertEqual(len(fc["features"]), 1)
        self.assertEqual(fc["features"][0]["properties"]["mean_response_min"], 30.0)
        self.assertEqual(fc["features"][0]["properties"]["worst_response_min"], 50)
        # fewer than k incidents -> suppressed
        self.assertEqual(gx.response_time_layer(incs[:3])["features"], [])

    def test_coverage_gap_layer(self):
        far = households(6, lat=7.0, lng=4.0)   # >5km from every facility
        near = households(3, lat=6.60, lng=3.35)
        fc = gx.coverage_gap_layer(FACS, far + near, max_km=5.0)
        self.assertEqual(len(fc["features"]), 1)
        self.assertEqual(fc["features"][0]["properties"]["unserved_households"], 6)

    def test_coverage_gap_empty_when_all_served(self):
        fc = gx.coverage_gap_layer(FACS, households(6, lat=6.60, lng=3.35))
        self.assertEqual(fc["features"], [])


if __name__ == "__main__":
    unittest.main()
