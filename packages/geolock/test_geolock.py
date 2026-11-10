"""Tests for geolock@1.0.0 — coordinate-reference governance."""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geolock  # noqa: E402
import demo_geolock  # noqa: E402


def _reg():
    r = geolock.CRSRegistry()
    r.register("EPSG:32631", kind="projected", projection="utm",
               name="UTM 31N", params={"zone": 31, "hemisphere": "N"})
    r.register("EPSG:32734", kind="projected", projection="utm",
               name="UTM 34S", params={"zone": 34, "hemisphere": "S"})
    r.register("EPSG:32632", kind="projected", projection="utm",
               name="UTM 32N", params={"zone": 32, "hemisphere": "N"})
    r.register("EPSG:32627", kind="projected", projection="utm",
               name="UTM 27N", params={"zone": 27, "hemisphere": "N"})
    return r


class TestCRSRegistry(unittest.TestCase):

    def test_seeded_systems(self):
        r = geolock.CRSRegistry()
        self.assertIn("EPSG:4326", r.systems)
        self.assertIn("EPSG:3857", r.systems)

    def test_duplicate_refused(self):
        with self.assertRaises(geolock.GeolockError):
            _reg().register("EPSG:4326", kind="geographic",
                            projection="longlat", name="dup")

    def test_unknown_projection_refused(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.CRSRegistry().register(
                "LOCAL:1", kind="projected",
                projection="oblique-stereographic", name="x")

    def test_geographic_must_be_longlat(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.CRSRegistry().register(
                "X:1", kind="geographic", projection="utm",
                name="bad",
                params={"zone": 31, "hemisphere": "N"})

    def test_utm_params_validated(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.CRSRegistry().register(
                "EPSG:32661", kind="projected", projection="utm",
                name="bad", params={"zone": 61, "hemisphere": "N"})
        with self.assertRaises(geolock.GeolockError):
            geolock.CRSRegistry().register(
                "EPSG:32631", kind="projected", projection="utm",
                name="bad", params={"zone": 31, "hemisphere": "E"})
        with self.assertRaises(geolock.GeolockError):
            geolock.CRSRegistry().register(
                "EPSG:32631", kind="projected", projection="utm",
                name="bad", params={})

    def test_get_names_absence(self):
        with self.assertRaises(geolock.GeolockError):
            _reg().get("EPSG:99999")


class TestTransforms(unittest.TestCase):

    def test_mercator_known_answer(self):
        x, y = geolock.transform(geolock.CRSRegistry(), "EPSG:4326",
                                 "EPSG:3857", 0.0,
                                 85.05112878)["output"].values()
        self.assertAlmostEqual(y, 20037508.34303882, places=2)

    def test_all_known_answers_pinned(self):
        report = geolock.verify_known_answers(_reg())
        self.assertEqual(report["status"], "pinned",
                         msg=json.dumps(report["vectors"], indent=1))
        self.assertEqual(len(report["vectors"]),
                         len(geolock.KNOWN_ANSWERS))

    def test_roundtrip_pinned(self):
        r = geolock.verify_roundtrip(_reg(), "EPSG:32631",
                                     541924.301, 721189.217)
        self.assertEqual(r["status"], "pinned")
        self.assertLessEqual(r["drift_m"], r["tolerance_m"])

    def test_southern_hemisphere_roundtrip(self):
        r = geolock.verify_roundtrip(_reg(), "EPSG:32734",
                                     261881.599, 6243182.355)
        self.assertEqual(r["status"], "pinned")

    def test_pole_refused_in_mercator(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.transform(geolock.CRSRegistry(), "EPSG:4326",
                              "EPSG:3857", 0.0, 90.0)

    def test_unregistered_crs_refused(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.transform(_reg(), "EPSG:4326", "EPSG:99999",
                              3.3792, 6.5244)

    def test_transform_record_carries_digests(self):
        r = geolock.transform(_reg(), "EPSG:4326", "EPSG:32631",
                              3.3792, 6.5244)
        for key in ("input_sha256", "output_sha256"):
            self.assertEqual(len(r[key]), 64)
            self.assertTrue(all(c in "0123456789abcdef"
                                for c in r[key]))


class TestCoordinatesAndArea(unittest.TestCase):

    def test_point_under_contract(self):
        p = geolock.point(_reg(), "EPSG:4326", 3.3792, 6.5244,
                          subject="synthetic")
        self.assertEqual(p["crs"], "EPSG:4326")
        self.assertEqual(len(p["sha256"]), 64)

    def test_out_of_range_refused(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.point(_reg(), "EPSG:4326", 3.3792, 96.5)

    def test_area_in_degrees_refused(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.area_km2(_reg(), "EPSG:4326",
                             [(3.3, 6.5), (3.4, 6.5), (3.4, 6.6)])

    def test_area_in_projected_units(self):
        km2 = geolock.area_km2(_reg(), "EPSG:32631",
                               [(0, 0), (2000, 0), (2000, 2000),
                                (0, 2000)])
        self.assertAlmostEqual(km2, 4.0, places=6)

    def test_degenerate_polygon_refused(self):
        with self.assertRaises(geolock.GeolockError):
            geolock.area_km2(_reg(), "EPSG:32631", [(0, 0), (1, 1)])

    def test_utm_zone_inference(self):
        self.assertEqual(geolock.utm_zone_for(3.3792), 31)
        self.assertEqual(geolock.utm_zone_for(-23.5133), 27)
        self.assertEqual(geolock.utm_zone_for(179.9), 60)
        with self.assertRaises(geolock.GeolockError):
            geolock.utm_zone_for(181)


class TestAdapters(unittest.TestCase):

    def test_template_refuses_until_ratified(self):
        a = geolock.AdapterRegistry()
        r = a.run("terrio-cli", ["info", "x.tif"],
                  executor=lambda c: {"sha256": "0" * 64})
        self.assertEqual(r["status"], "refused")

    def test_duplicate_approver_refused(self):
        a = geolock.AdapterRegistry()
        a.ratify("terrio-cli", by="p1")
        with self.assertRaises(geolock.GeolockError):
            a.ratify("terrio-cli", by="p1")

    def test_missing_executor_stated_not_faked(self):
        a = geolock.AdapterRegistry()
        a.ratify("gdal-cli", by="p1")
        a.ratify("gdal-cli", by="p2")
        r = a.run("gdal-cli", ["gdalinfo", "x.tif"])
        self.assertEqual(r["status"], "not-run")
        self.assertIn("no executor", r["detail"])

    def test_executor_error_not_run_not_empty(self):
        a = geolock.AdapterRegistry()
        a.ratify("gdal-cli", by="p1")
        a.ratify("gdal-cli", by="p2")

        def boom(c):
            raise OSError("dead")

        r = a.run("gdal-cli", ["gdalinfo", "x.tif"], executor=boom)
        self.assertEqual(r["status"], "not-run")
        self.assertIn("OSError", r["detail"])

    def test_unstamped_result_refused(self):
        a = geolock.AdapterRegistry()
        a.ratify("gdal-cli", by="p1")
        a.ratify("gdal-cli", by="p2")
        r = a.run("gdal-cli", ["gdalinfo", "x.tif"],
                  executor=lambda c: {"rows": 1})
        self.assertEqual(r["status"], "not-run")
        self.assertIn("unstamped", r["detail"])

    def test_ratified_run_ok(self):
        a = geolock.AdapterRegistry()
        a.ratify("terrio-cli", by="p1")
        a.ratify("terrio-cli", by="p2")
        r = a.run("terrio-cli", ["info", "x.tif"],
                  executor=lambda c: {"sha256": "ab" * 32},
                  executed_at="2026-11-11T14:00:00Z")
        self.assertEqual(r["status"], "ok")


class TestDemoGeoproof(unittest.TestCase):

    def test_demo_is_deterministic(self):
        self.assertEqual(demo_geolock.run_demo(),
                         demo_geolock.run_demo())

    def test_stamp_contract(self):
        body = demo_geolock.run_demo()
        stamp = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(stamp, expect)

    def test_payload_shape(self):
        body = demo_geolock.run_demo()
        self.assertEqual(body["surface"], "geoproof")
        self.assertTrue(body["demo"])
        self.assertEqual(len(body["drills"]), 8)
        for d in body["drills"]:
            outcome = d["result"].get("outcome") \
                or d["result"].get("status")
            self.assertIn(outcome, ("refused", "not-run"))
        ctrl = body["control"]
        self.assertEqual(ctrl["known_answers"]["status"], "pinned")
        self.assertEqual(ctrl["roundtrip"]["status"], "pinned")
        self.assertAlmostEqual(ctrl["catchment_area_km2"], 4.0,
                               places=6)
        self.assertEqual(ctrl["zone_inference"]["lagos_lng_3.3792"],
                         31)
        self.assertEqual(ctrl["adapter_call"]["status"], "ok")

    def test_file_emission(self):
        demo_geolock.main()
        with open(demo_geolock.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, demo_geolock.run_demo())


if __name__ == "__main__":
    unittest.main()
