
import unittest
from trialmatch import TrialRegistry, match, screen, TrialError


def _reg():
    r = TrialRegistry()
    r.register("MAL-2026-01", protocol_version="3.1", site="LUTH",
               ratified_by="irb-chair",
               inclusion=[{"field": "age", "op": "gte", "value": 18},
                          {"field": "diagnosis", "op": "eq",
                           "value": "uncomplicated-malaria"},
                          {"field": "parasite_density", "op": "gte",
                           "value": 1000}],
               exclusion=[{"field": "pregnant", "op": "eq", "value": True},
                          {"field": "severe_comorbidity", "op": "eq",
                           "value": True}])
    return r


ELIGIBLE = {"age": 30, "diagnosis": "uncomplicated-malaria",
            "parasite_density": 5000, "pregnant": False,
            "severe_comorbidity": False}


class TestTrialMatch(unittest.TestCase):
    def test_eligible_patient(self):
        v, d = match(_reg().trial("MAL-2026-01"), ELIGIBLE)
        self.assertEqual(v, "eligible")
        self.assertTrue(all(i["result"] for i in d["inclusion"]))

    def test_exclusion_beats_inclusion(self):
        pheno = dict(ELIGIBLE, pregnant=True)
        v, d = match(_reg().trial("MAL-2026-01"), pheno)
        self.assertEqual(v, "excluded")
        self.assertIn("hit_exclusion", d)

    def test_missing_data_is_unknown_not_eligible(self):
        pheno = {"age": 30, "diagnosis": "uncomplicated-malaria",
                 "parasite_density": 5000}  # exclusion fields absent
        v, d = match(_reg().trial("MAL-2026-01"), pheno)
        self.assertEqual(v, "unknown")

    def test_failed_inclusion_excludes(self):
        v, d = match(_reg().trial("MAL-2026-01"), dict(ELIGIBLE, age=12))
        self.assertEqual(v, "excluded")

    def test_screen_orders_eligible_first(self):
        out = screen(_reg(), "MAL-2026-01", {
            "p1": dict(ELIGIBLE, pregnant=True),
            "p2": ELIGIBLE,
            "p3": {"age": 40},
        })
        self.assertEqual([o["verdict"] for o in out],
                         ["eligible", "unknown", "excluded"])

    def test_protocol_version_on_every_row(self):
        out = screen(_reg(), "MAL-2026-01", {"p1": ELIGIBLE})
        self.assertEqual(out[0]["protocol_version"], "3.1")

    def test_no_protocol_version_refused(self):
        r = TrialRegistry()
        with self.assertRaises(TrialError):
            r.register("X", protocol_version="", inclusion=[],
                       exclusion=[], site="s", ratified_by="y")

    def test_code_in_rules_refused(self):
        r = TrialRegistry()
        with self.assertRaises(TrialError):
            r.register("X", protocol_version="1", site="s",
                       ratified_by="y",
                       inclusion=[{"field": "age", "op": "eval",
                                   "value": "__import__('os')"}],
                       exclusion=[])

    def test_advisory_note_present(self):
        out = screen(_reg(), "MAL-2026-01", {"p1": ELIGIBLE})
        self.assertIn("consent", out[0]["note"])


if __name__ == "__main__":
    unittest.main()
