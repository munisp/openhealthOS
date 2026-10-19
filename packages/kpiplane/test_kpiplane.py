"""Tests for the KPI plane job (ADR-0033) — stdlib unittest, tmp dirs."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kpiplane as K  # noqa: E402


def _write(path, rows, jsonl=True):
    with open(path, "w") as fh:
        if jsonl:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        else:
            json.dump(rows, fh)


ENCOUNTERS = [
    {"code": "CONS-GEN", "enrollee_id": "E1",
     "staff": [{"minutes": 10, "kobo_per_minute": 100}],
     "consumables": [{"kobo": 5000}]},
    {"code": "CONS-GEN", "enrollee_id": "E2",
     "staff": [{"minutes": 20, "kobo_per_minute": 100}],
     "consumables": [{"kobo": 8000}]},
    {"code": "MAL-RDT", "enrollee_id": None,
     "staff": [{"minutes": 5, "kobo_per_minute": 100}],
     "consumables": [{"kobo": 20000}]},
]

ENROLLEES = [
    {"id": "E1", "age": 2},
    {"id": "E2", "age": 34},
    {"id": "E3", "age": 70},
    {"id": "E4", "age": 27, "pregnant": True},
    {"id": "E5", "age": 55, "chronic": True},
]

CLAIMS = [
    {"hmo": "HYGEIA", "status": "approved", "amount_kobo": 100000},
    {"hmo": "HYGEIA", "status": "denied", "denial_reason": "missing-referral",
     "amount_kobo": 40000},
    {"hmo": "RELIANCE", "status": "denied", "denial_reason": "not-covered",
     "amount_kobo": 60000},
]

FACILITIES = [
    {"id": "PHC-1", "name": "Kawo PHC", "latitude": 10.599, "longitude": 7.434},
    {"id": "PHC-2", "name": "Umuahia PHC", "latitude": 5.532, "longitude": 7.486},
]

MEMBERS = [
    {"id": "M1", "latitude": 10.61, "longitude": 7.45},   # ~2km from PHC-1
    {"id": "M2", "latitude": 10.70, "longitude": 7.50},   # nearer PHC-1
    {"id": "M3", "latitude": 5.54, "longitude": 7.49},    # ~1km from PHC-2
    {"id": "M4"},                                          # no coords
]


class Fixtures:
    def __init__(self, tmp):
        self.dir = tmp
        for name, rows in (("encounters", ENCOUNTERS), ("enrollees", ENROLLEES),
                           ("claims", CLAIMS), ("members", MEMBERS)):
            _write(os.path.join(tmp, name + ".jsonl"), rows)
        _write(os.path.join(tmp, "facilities.json"), FACILITIES, jsonl=False)


class TestSections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fx = Fixtures(self.tmp)
        p = lambda n: os.path.join(self.tmp, n)

        self.plane = K.build_plane(
            lakehouse_dir=p("lakehouse"),
            facilities_path=p("facilities.json"),
            members_path=p("members.jsonl"),
            enrollees_path=p("enrollees.jsonl"),
            encounters_path=p("encounters.jsonl"),
            claims_path=p("claims.jsonl"),
            registry_dir=p("no-registry"))

    def test_margins_worst_first(self):
        econ = self.plane["sections"]["facility_economics"]
        self.assertEqual(econ["status"], "ok")
        lines = econ["margins"]["lines"]
        self.assertEqual(len(lines), 2)
        margins = [l["margin_kobo"] for l in lines]
        self.assertEqual(margins, sorted(margins))
        self.assertEqual(econ["margins"]["encounters"], 3)
        self.assertEqual(econ["margin_worst"], lines[0])

    def test_panel_and_risk_bands(self):
        econ = self.plane["sections"]["facility_economics"]
        self.assertEqual(econ["panel"]["panel_size"], 5)
        bands = {b["band"] for b in econ["panel"]["bands"]}
        self.assertIn("maternal", bands)
        self.assertIn("walk-in", bands)  # MAL-RDT has no enrollee_id
        risk = self.plane["sections"]["panel_risk"]
        self.assertEqual(risk["status"], "ok")
        self.assertEqual(risk["bands"]["under5"], 1)
        self.assertEqual(risk["bands"]["elderly"], 1)
        self.assertAlmostEqual(sum(risk["band_share_pct"].values()), 100, places=0)

    def test_denials(self):
        d = self.plane["sections"]["facility_economics"]["denials"]
        self.assertEqual(d["denied"], 2)
        self.assertEqual(d["preventable_denied_kobo"], 40000)
        self.assertGreater(d["preventable_share_pct"], 0)
        self.assertEqual(d["by_hmo"]["HYGEIA"]["denial_rate_pct"], 50.0)

    def test_catchments(self):
        c = self.plane["sections"]["catchments"]
        self.assertEqual(c["status"], "ok")
        self.assertEqual(c["unassigned"], 1)
        rows = {r["facility"]: r for r in c["facilities"]}
        self.assertEqual(rows["PHC-1"]["assigned"], 2)
        self.assertEqual(rows["PHC-2"]["assigned"], 1)
        self.assertLess(rows["PHC-2"]["mean_km"], 5)

    def test_haversine_known_pair(self):
        # Kaduna -> Lagos is roughly 630km as the crow flies.
        d = K._haversine_km(10.52, 7.44, 6.52, 3.37)
        self.assertGreater(d, 550)
        self.assertLess(d, 700)

    def test_ml_health_no_registry(self):
        mh = self.plane["sections"]["ml_health"]
        self.assertEqual(mh["status"], "no-data")
        self.assertEqual(mh["drift"]["verdict"], "no-data")

    def test_ml_health_with_registry_and_drift(self):
        tmp = tempfile.mkdtemp()
        reg = os.path.join(tmp, "registry")
        vdir = os.path.join(reg, "fraud-mlp", "v1")
        os.makedirs(vdir)
        with open(os.path.join(reg, "fraud-mlp", "champion"), "w") as fh:
            fh.write("v1")
        _write(os.path.join(vdir, "metrics.json"),
               {"auc": 0.91, "precision_at_k": 0.4}, jsonl=False)
        _write(os.path.join(vdir, "card.json"), {"data_hash": "abc"}, jsonl=False)

        # lakehouse claims schema — what features.build_features reads
        ref = [{"amount_ngn": 1000, "tariff_ngn": 1000, "qty": 1, "hour": 10,
                "day": 1, "facility_id": "F1", "member_id": "M1", "code": "CONS-GEN"}]
        cur = [{"amount_ngn": 9000, "tariff_ngn": 1000, "qty": 9, "hour": 2,
                "day": 6, "facility_id": "F2", "member_id": "M2", "code": "CS-EMG"}]
        _write(os.path.join(tmp, "ref.jsonl"), ref)
        _write(os.path.join(tmp, "cur.jsonl"), cur)

        mh = K.ml_health(reg, os.path.join(tmp, "ref.jsonl"),
                         os.path.join(tmp, "cur.jsonl"))
        self.assertEqual(mh["status"], "ok")
        self.assertEqual(mh["models"][0]["champion"], "v1")
        self.assertEqual(mh["models"][0]["metrics"]["auc"], 0.91)
        self.assertIn(mh["drift"]["verdict"], ("stable", "moderate", "significant"))

    def test_degrades_without_inputs(self):
        plane = K.build_plane()
        for sec in plane["sections"].values():
            self.assertIn(sec["status"], ("no-data", "ok"))
        self.assertEqual(plane["sections"]["catchments"]["status"], "no-data")
        self.assertEqual(plane["sections"]["panel_risk"]["status"], "no-data")

    def test_cli_writes_gold(self):
        tmp = tempfile.mkdtemp()
        Fixtures(tmp)
        lake = os.path.join(tmp, "lakehouse")
        os.makedirs(os.path.join(lake, "gold"))
        _write(os.path.join(lake, "gold", "aggregates.json"),
               {"events_by_type": {"encounter": 7}}, jsonl=False)
        out = os.path.join(tmp, "plane.json")
        rc = K.main(["--lakehouse", lake, "--out", out,
                     "--facilities", os.path.join(tmp, "facilities.json"),
                     "--members", os.path.join(tmp, "members.jsonl"),
                     "--enrollees", os.path.join(tmp, "enrollees.jsonl"),
                     "--encounters", os.path.join(tmp, "encounters.jsonl"),
                     "--claims", os.path.join(tmp, "claims.jsonl"),
                     "--registry", os.path.join(tmp, "nope")])
        self.assertEqual(rc, 0)
        plane = json.load(open(out))
        self.assertEqual(plane["lakehouse_aggregates"]["events_by_type"]["encounter"], 7)
        self.assertEqual(plane["sections"]["panel_risk"]["panel_size"], 5)


if __name__ == "__main__":
    unittest.main()
