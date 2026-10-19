"""Tests for population health (ADR-0034) — stdlib unittest."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pophealth as P  # noqa: E402

TODAY = date.today()
DAYS = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731

BUNDLE = [
    {"resourceType": "Patient", "id": "p-dia", "birthDate": "1976-03-01",
     "gender": "male"},
    {"resourceType": "Patient", "id": "p-anc", "birthDate": "1998-07-01",
     "gender": "female"},
    {"resourceType": "Patient", "id": "p-kid", "birthDate": str(TODAY.year - 2) + "01-15",
     "gender": "female"},
    {"resourceType": "Patient", "id": "p-young", "birthDate": "2000-01-01",
     "gender": "female"},

    {"resourceType": "Condition", "id": "c1",
     "subject": {"reference": "Patient/p-dia"},
     "code": {"coding": [{"code": "E11.9"}]}, "onsetDateTime": DAYS(400)},
    {"resourceType": "Condition", "id": "c2",
     "subject": {"reference": "Patient/p-dia"},
     "code": {"coding": [{"code": "I10"}]}, "onsetDateTime": DAYS(400)},
    {"resourceType": "Condition", "id": "c3",
     "subject": {"reference": "Patient/p-anc"},
     "code": {"coding": [{"code": "Z34.0"}]}, "onsetDateTime": DAYS(60)},

    # p-dia: BP fresh (10d ago), HbA1c stale (8 months)
    {"resourceType": "Observation", "id": "o1",
     "subject": {"reference": "Patient/p-dia"},
     "code": {"coding": [{"code": "8480-6"}]},
     "valueQuantity": {"value": 152, "unit": "mmHg"},
     "effectiveDateTime": DAYS(10)},
    {"resourceType": "Observation", "id": "o2",
     "subject": {"reference": "Patient/p-dia"},
     "code": {"coding": [{"code": "4548-4"}]},
     "valueQuantity": {"value": 8.1, "unit": "%"},
     "effectiveDateTime": DAYS(240)},
    {"resourceType": "Observation", "id": "o3",
     "subject": {"reference": "Patient/p-dia"},
     "code": {"coding": [{"code": "55423-8"}]},
     "valueQuantity": {"value": 2300, "unit": "steps"},
     "effectiveDateTime": DAYS(2)},

    # p-anc: one ANC visit 3 weeks ago (within the month)
    {"resourceType": "Encounter", "id": "e1",
     "subject": {"reference": "Patient/p-anc"}, "class": "AMB",
     "period": {"start": DAYS(21)}},

    # p-kid: immunization 9 months ago -> due (every 6m)
    {"resourceType": "Observation", "id": "o4",
     "subject": {"reference": "Patient/p-kid"},
     "code": {"coding": [{"code": "OH-IMMUNIZATION"}]},
     "valueString": "Penta-3", "effectiveDateTime": DAYS(270)},

    # p-young: cervical screening 40 months ago -> due (every 36m)
    {"resourceType": "Observation", "id": "o5",
     "subject": {"reference": "Patient/p-young"},
     "code": {"coding": [{"code": "OH-CERVICAL-SCREEN"}]},
     "valueString": "VIA negative", "effectiveDateTime": DAYS(1200)},

    {"resourceType": "MedicationRequest", "id": "m1",
     "subject": {"reference": "Patient/p-dia"},
     "medicationCodeableConcept": {"coding": [{"code": "METFORMIN"}]},
     "authoredOn": DAYS(30)},
]


class TestOmop(unittest.TestCase):
    def setUp(self):
        self.tables = P.to_omop(BUNDLE)

    def test_persons(self):
        self.assertEqual(len(self.tables["person"]), 4)
        dia = next(p for p in self.tables["person"] if p["person_id"] == "p-dia")
        self.assertEqual(dia["year_of_birth"], 1976)
        self.assertEqual(dia["gender_concept_id"], 8507)

    def test_condition_mapping(self):
        codes = {c["condition_source_value"]
                 for c in self.tables["condition_occurrence"]}
        self.assertIn("E11.9", codes)
        e11 = next(c for c in self.tables["condition_occurrence"]
                   if c["condition_source_value"] == "E11.9")
        self.assertEqual(e11["condition_concept_id"], 0)  # E11.9 != E11 exact — honest 0

    def test_measurement_vs_observation(self):
        self.assertTrue(all(isinstance(m["value_as_number"], (int, float))
                            for m in self.tables["measurement"]))
        self.assertEqual(len(self.tables["measurement"]), 3)  # BP, HbA1c, steps
        self.assertEqual(len(self.tables["observation"]), 2)

    def test_visit_and_drug_and_period(self):
        self.assertEqual(len(self.tables["visit_occurrence"]), 1)
        self.assertEqual(len(self.tables["drug_exposure"]), 1)
        periods = {p["person_id"]: p for p in self.tables["observation_period"]}
        self.assertLess(periods["p-dia"]["observation_period_start_date"],
                        periods["p-dia"]["observation_period_end_date"])

    def test_write_omop(self):
        out = tempfile.mkdtemp()
        counts = P.write_omop(self.tables, out)
        self.assertEqual(counts["person"], 4)
        with open(os.path.join(out, "person.jsonl")) as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(len(rows), 4)
        self.assertTrue(os.path.exists(os.path.join(out, "concepts.json")))


class TestCohorts(unittest.TestCase):
    def setUp(self):
        self.tables = P.to_omop(BUNDLE)
        self.cohorts = P.build_cohorts(self.tables)

    def test_cohorts(self):
        self.assertEqual(self.cohorts["diabetes"], ["p-dia"])
        self.assertEqual(self.cohorts["hypertension"], ["p-dia"])
        self.assertEqual(self.cohorts["anc"], ["p-anc"])
        self.assertEqual(self.cohorts["under5"], ["p-kid"])
        self.assertIn("p-dia", self.cohorts["cvd_watch"])


class TestCareGaps(unittest.TestCase):
    def setUp(self):
        self.tables = P.to_omop(BUNDLE)
        self.cohorts = P.build_cohorts(self.tables)
        self.gaps = P.care_gaps(self.tables, self.cohorts, today=TODAY)

    def gap_ids(self, pid):
        return {g["gap"] for g in self.gaps.get(pid, [])}

    def test_diabetic_hba1c_overdue_bp_fresh(self):
        self.assertIn("hba1c", self.gap_ids("p-dia"))
        self.assertNotIn("bp-check", self.gap_ids("p-dia"))

    def test_anc_fresh_visit_closes_gap(self):
        self.assertNotIn("anc-visit", self.gap_ids("p-anc"))

    def test_immunization_due(self):
        self.assertIn("immunization", self.gap_ids("p-kid"))

    def test_cervical_screening_due(self):
        self.assertIn("cervical-screen", self.gap_ids("p-young"))

    def test_never_done_is_due(self):
        # p-anc is 27: bp-check never done -> due
        self.assertIn("bp-check", self.gap_ids("p-anc"))


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.tables = P.to_omop(BUNDLE)
        self.cohorts = P.build_cohorts(self.tables)
        self.risks = P.risk_scores(self.tables, self.cohorts)

    def test_honest_label(self):
        for r in self.risks.values():
            self.assertEqual(r["model"], "heuristic-1")

    def test_diabetic_hypertensive_scores_highest(self):
        cvd = {pid: r["cvd_risk"] for pid, r in self.risks.items()}
        top = max(cvd, key=cvd.get)
        self.assertEqual(top, "p-dia")
        self.assertEqual(self.risks["p-dia"]["cvd_band"], "high")

    def test_bands_within_range(self):
        for r in self.risks.values():
            self.assertTrue(0 <= r["cvd_risk"] <= 1)
            self.assertIn(r["diabetes_band"], ("low", "moderate", "high"))


class TestPlan(unittest.TestCase):
    def test_plan_combines_gaps_risk_wellness(self):
        tables = P.to_omop(BUNDLE)
        cohorts = P.build_cohorts(tables)
        gaps = P.care_gaps(tables, cohorts, today=TODAY)
        risks = P.risk_scores(tables, cohorts)
        plan = P.prevention_plan(tables, cohorts, gaps, risks, "p-dia")
        self.assertEqual(plan["avg_daily_steps_14d"], 2300)
        self.assertTrue(any("HbA1c" in r for r in plan["recommendations"]))
        self.assertTrue(any("5,000 steps" in r for r in plan["recommendations"]))
        self.assertTrue(any(g["gap"] == "hba1c" for g in plan["overdue"]))


class TestCli(unittest.TestCase):
    def test_all_and_plan(self):
        tmp = tempfile.mkdtemp()
        bundle = {"resourceType": "Bundle", "type": "searchset",
                  "entry": [{"resource": r} for r in BUNDLE]}
        bp = os.path.join(tmp, "bundle.json")
        with open(bp, "w") as fh:
            json.dump(bundle, fh)
        out = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "pophealth.py"),
             "all", "--bundle", bp, "--out", os.path.join(tmp, "omop")],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        doc = json.loads(out.stdout)
        self.assertEqual(doc["cohorts"]["diabetes"], 1)
        self.assertTrue(os.path.exists(os.path.join(tmp, "omop", "person.jsonl")))
        out2 = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "pophealth.py"),
             "plan", "--bundle", bp, "--patient", "p-dia"],
            capture_output=True, text=True)
        plan = json.loads(out2.stdout)
        self.assertEqual(plan["patient_id"], "p-dia")
        self.assertIn("recommendations", plan)


if __name__ == "__main__":
    unittest.main()
