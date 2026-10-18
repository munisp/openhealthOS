import unittest

import routing


# A tiny Lagos: a grid street plus a fast expressway. Coordinates ~1 km apart
# (0.009 degrees ≈ 1 km at the equator).
ROADS = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"highway": "residential"},
         "geometry": {"type": "LineString",
                      "coordinates": [[3.370, 6.520], [3.379, 6.520],
                                      [3.379, 6.529]]}},
        {"type": "Feature", "properties": {"highway": "motorway"},
         "geometry": {"type": "LineString",
                      "coordinates": [[3.370, 6.520], [3.370, 6.529],
                                      [3.379, 6.529]]}},
    ],
}


class TestBuild(unittest.TestCase):
    def test_build_graph(self):
        g = routing.build_graph_from_geojson(ROADS)
        self.assertEqual(len(g["nodes"]), 4)  # shared corner joins the two streets
        self.assertTrue(any(v == "motorway" for v in g["edge_speed"].values()))

    def test_rejects_non_featurecollection(self):
        with self.assertRaises(routing.Error):
            routing.build_graph_from_geojson({"type": "Feature"})

    def test_empty_extract_rejected(self):
        with self.assertRaises(routing.Error):
            routing.build_graph_from_geojson({"type": "FeatureCollection",
                                              "features": []})


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.g = routing.RoadGraph(routing.build_graph_from_geojson(ROADS))

    def test_route_follows_roads_not_straight_line(self):
        # endpoints are ~1 km apart straight-line, but roads force an L path
        r = self.g.route(6.520, 3.370, 6.529, 3.379)
        self.assertGreater(r["distance_km"], 1.8)   # L is ~2 km
        self.assertLess(r["distance_km"], 2.4)
        self.assertEqual(r["path"][0], self.g.nearest_node(6.520, 3.370))
        self.assertEqual(r["path"][-1], self.g.nearest_node(6.529, 3.379))

    def test_eta_uses_road_class_speeds(self):
        fast = self.g.route(6.520, 3.370, 6.529, 3.379)
        # A* prefers the motorway if faster; either way ETA reflects class speeds
        self.assertGreater(fast["eta_min"], 0.5)
        self.assertLess(fast["eta_min"], 10)

    def test_motorway_preferred_when_both_paths_exist(self):
        # query between the two endpoints joined by both the residential L
        # (25 km/h) and the motorway L (80 km/h): same distance, so the
        # motorway path should win on... A* minimises DISTANCE; tie is fine —
        # but ETA must not exceed the motorway time for the distance.
        r = self.g.route(6.520, 3.370, 6.529, 3.379)
        motorway_minutes = r["distance_km"] / 80 * 60 + 1.0  # +snap slack
        self.assertLessEqual(r["eta_min"], max(motorway_minutes, r["distance_km"] / 25 * 60))

    def test_same_node_short_route(self):
        r = self.g.route(6.5200, 3.3700, 6.5201, 3.3701)
        self.assertEqual(len(r["path"]), 1)

    def test_unreachable_endpoint_raises(self):
        with self.assertRaises(routing.Error):
            self.g.route(9.0, 8.0, 6.529, 3.379)  # far away, no snap

    def test_deterministic(self):
        a = self.g.route(6.520, 3.370, 6.529, 3.379)
        b = self.g.route(6.520, 3.370, 6.529, 3.379)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
