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


import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "geolock"))

import geolock  # noqa: E402


def _registry():
    reg = geolock.CRSRegistry()
    reg.register("EPSG:32631", kind="projected", projection="utm",
                 name="UTM 31N", params={"zone": 31, "hemisphere": "N"})
    return reg


class TestGeolockContract(unittest.TestCase):
    """ADR-0074: every export states its CRS contract; governed exports
    fail closed against the registry."""

    def test_ungoverned_export_states_the_default(self):
        fc = gx.facilities_layer(FACS)
        member = fc["openhealth:geolock"]
        self.assertEqual(member["crs"], "EPSG:4326")
        self.assertEqual(member["axis_order"], "lng,lat")
        self.assertFalse(member["governed"])
        self.assertEqual(member["basis"], "rfc-7946-default-stated")
        self.assertEqual(len(member["features_sha256"]), 64)

    def test_governed_export_validates_against_registry(self):
        fc = gx.facilities_layer(FACS, registry=_registry())
        member = fc["openhealth:geolock"]
        self.assertTrue(member["governed"])
        self.assertEqual(member["basis"], "registry-governed")

    def test_governed_export_refuses_unregistered_crs(self):
        with self.assertRaises(geolock.GeolockError):
            gx.facilities_layer(FACS,
                                registry=geolock.CRSRegistry(seeded=False))

    def test_governed_export_refuses_projected_export_crs(self):
        reg = geolock.CRSRegistry()
        reg.systems["EPSG:4326"] = {"code": "EPSG:4326", "kind": "projected",
                                    "projection": "web-mercator",
                                    "name": "tampered", "params": {}}
        with self.assertRaises(geolock.GeolockError):
            gx.facilities_layer(FACS, registry=reg)

    def test_legacy_centroid_is_stated_degree_average(self):
        fc = gx.households_density_layer(households(6, jitter=0.001))
        props = fc["features"][0]["properties"]
        self.assertEqual(props["centroid_space"], "degree-average")
        self.assertNotIn("centroid_crs", props)

    def test_governed_centroid_is_metre_space(self):
        fc = gx.households_density_layer(households(6, jitter=0.001),
                                         registry=_registry())
        props = fc["features"][0]["properties"]
        self.assertEqual(props["centroid_space"], "utm-metres")
        self.assertEqual(props["centroid_crs"], "EPSG:32631")

    def test_metre_centroid_stays_near_the_cluster(self):
        fc = gx.households_density_layer(households(8, jitter=0.0005),
                                         registry=_registry())
        lng, lat = fc["features"][0]["geometry"]["coordinates"]
        self.assertAlmostEqual(lat, 6.55175, places=3)
        self.assertAlmostEqual(lng, 3.37175, places=3)

    def test_governed_density_refuses_unregistered_zone(self):
        reg = geolock.CRSRegistry()  # no UTM zones registered
        with self.assertRaises(geolock.GeolockError):
            gx.households_density_layer(households(6), registry=reg)

    def test_k_anonymity_binds_under_governance(self):
        dense = households(6)
        sparse = [{"lat": 7.50, "lng": 4.50, "members": 3}]
        fc = gx.households_density_layer(dense + sparse,
                                         registry=_registry())
        self.assertEqual(len(fc["features"]), 1)
        self.assertNotIn("7.5", str(fc))


def _pt(lng, lat):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {}}


class TestCoordinateGate(unittest.TestCase):
    """ADR-0076: collection() range-validates every position BEFORE the
    stamp, over a closed geometry vocabulary, with the offender named.
    The checksum is the contract — it presumes bytes that deserve it."""

    def test_valid_collection_passes_and_counts(self):
        feats = [_pt(3.37, 6.52),
                 {"type": "Feature",
                  "geometry": {"type": "LineString",
                               "coordinates": [[3.37, 6.52],
                                               [3.38, 6.53]]},
                  "properties": {}},
                 {"type": "Feature",
                  "geometry": {"type": "Polygon",
                               "coordinates": [[[3.0, 6.0], [3.1, 6.0],
                                                [3.1, 6.1], [3.0, 6.0]]]},
                  "properties": {}}]
        fc = gx.collection(feats, registry=_registry())
        self.assertEqual(
            fc["openhealth:geolock"]["coordinates_checked"], 7)

    def test_metre_space_point_refused_named(self):
        with self.assertRaises(gx.Error) as ctx:
            gx.collection([_pt(376170.8, 727867.6)])
        self.assertIn("376170.8", str(ctx.exception))
        self.assertIn("feature 0", str(ctx.exception))

    def test_interior_linestring_vertex_refused(self):
        bad = {"type": "Feature",
               "geometry": {"type": "LineString",
                            "coordinates": [[3.37, 6.52],
                                            [376170.8, 727867.6]]},
               "properties": {}}
        with self.assertRaises(gx.Error) as ctx:
            gx.collection([_pt(3.37, 6.52), bad])
        self.assertIn("feature 1", str(ctx.exception))

    def test_polygon_ring_refused(self):
        bad = {"type": "Feature",
               "geometry": {"type": "Polygon",
                            "coordinates": [[[3.0, 6.0], [3.1, 691.0],
                                             [3.1, 6.1], [3.0, 6.0]]]},
               "properties": {}}
        with self.assertRaises(gx.Error) as ctx:
            gx.collection([bad])
        self.assertIn("691", str(ctx.exception))

    def test_unknown_geometry_type_refused(self):
        bad = {"type": "Feature",
               "geometry": {"type": "GeometryCollection",
                            "geometries": []},
               "properties": {}}
        with self.assertRaises(gx.Error) as ctx:
            gx.collection([bad])
        self.assertIn("GeometryCollection", str(ctx.exception))

    def test_null_geometry_is_legal_and_stated(self):
        feat = {"type": "Feature", "geometry": None,
                "properties": {"note": "ungelocated"}}
        fc = gx.collection([feat])
        self.assertEqual(
            fc["openhealth:geolock"]["coordinates_checked"], 0)

    def test_bool_is_not_a_coordinate(self):
        bad = {"type": "Feature",
               "geometry": {"type": "Point", "coordinates": [True, 6.5]},
               "properties": {}}
        with self.assertRaises(gx.Error):
            gx.collection([bad])


if __name__ == "__main__":
    unittest.main()
