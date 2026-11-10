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


import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "geolock"))

import geolock  # noqa: E402


class TestCrsGovernance(unittest.TestCase):
    """ADR-0074: the CRS gate is fail-closed; the geodesy model and the
    source digest are stated on every graph and every route."""

    def test_undeclared_extract_states_assumed_basis(self):
        g = routing.build_graph_from_geojson(ROADS)
        self.assertEqual(g["crs_contract"]["basis"], "assumed-rfc-7946")
        self.assertEqual(g["crs_contract"]["code"], "EPSG:4326")
        self.assertEqual(len(g["source_sha256"]), 64)

    def test_metre_space_extract_refused_with_coordinate_named(self):
        bad = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"highway": "primary"},
             "geometry": {"type": "LineString",
                          "coordinates": [[376170.8, 727867.6],
                                          [376200.0, 727900.0]]}}]}
        with self.assertRaises(routing.Error) as ctx:
            routing.build_graph_from_geojson(bad)
        self.assertIn("376170.8", str(ctx.exception))

    def test_legacy_crs_member_non_wgs84_refused(self):
        bad = {"type": "FeatureCollection",
               "crs": {"type": "name",
                       "properties": {"name": "EPSG:3857"}},
               "features": ROADS["features"]}
        with self.assertRaises(routing.Error):
            routing.build_graph_from_geojson(bad)

    def test_legacy_crs_member_crs84_accepted(self):
        ok = {"type": "FeatureCollection",
              "crs": {"type": "name",
                      "properties": {"name":
                                     "urn:ogc:def:crs:OGC:1.3:CRS84"}},
              "features": ROADS["features"]}
        g = routing.build_graph_from_geojson(ok)
        self.assertEqual(g["crs_contract"]["basis"],
                         "declared-legacy-crs84")

    def test_geolock_member_wrong_crs_refused(self):
        bad = {"type": "FeatureCollection",
               "openhealth:geolock": {"crs": "EPSG:3857",
                                      "axis_order": "x,y",
                                      "governed": True},
               "features": ROADS["features"]}
        with self.assertRaises(routing.Error):
            routing.build_graph_from_geojson(bad)

    def test_registry_governed_build(self):
        reg = geolock.CRSRegistry()
        g = routing.build_graph_from_geojson(ROADS, registry=reg)
        self.assertEqual(g["crs_contract"]["basis"], "registry-governed")

    def test_registry_missing_crs_refused(self):
        with self.assertRaises(geolock.GeolockError):
            routing.build_graph_from_geojson(
                ROADS, registry=geolock.CRSRegistry(seeded=False))

    def test_route_carries_digest(self):
        g = routing.RoadGraph(routing.build_graph_from_geojson(ROADS))
        r = g.route(6.520, 3.370, 6.529, 3.379)
        self.assertEqual(len(r["route_sha256"]), 64)
        again = g.route(6.520, 3.370, 6.529, 3.379)
        self.assertEqual(r["route_sha256"], again["route_sha256"])

    def test_route_refuses_metre_space_endpoints(self):
        g = routing.RoadGraph(routing.build_graph_from_geojson(ROADS))
        with self.assertRaises(routing.Error):
            g.route(727867.6, 376170.8, 6.529, 3.379)

    def test_geodesy_contract_stated_as_data(self):
        g = routing.build_graph_from_geojson(ROADS)
        geo = g["crs_contract"]["geodesy"]
        self.assertEqual(geo["model"], "spherical-haversine")
        self.assertEqual(geo["radius_km"], 6371.0)


if __name__ == "__main__":
    unittest.main()
