"""Charting tests — templates are entries, signed notes are frozen,
order sets are four-eyes and safety-gated."""

import unittest

from charting import (CHARTING_VERSION, NOTE_TEMPLATES, ChartNote,
                      ChartingError, OrderSetRegistry, register_template)


class TestTemplates(unittest.TestCase):
    def test_specialty_packs_are_entries(self):
        self.assertIn("soap-general@1.0.0", NOTE_TEMPLATES)
        self.assertIn("anc-visit@1.0.0", NOTE_TEMPLATES)
        self.assertIn("mental-health@1.0.0", NOTE_TEMPLATES)
        # adding a specialty is adding an entry
        tid = "dermatology-review@1.0.0"
        register_template(tid, label="Dermatology review",
                          specialty="dermatology",
                          sections={"lesion-description":
                                    {"required": True, "kind": "text"},
                                    "plan": {"required": True,
                                             "kind": "list"}})
        self.assertIn(tid, NOTE_TEMPLATES)
        del NOTE_TEMPLATES[tid]      # test hygiene: leave no trace

    def test_unknown_kind_refuses(self):
        with self.assertRaises(ChartingError):
            register_template("bad@1.0.0", label="Bad",
                              specialty="x",
                              sections={"s": {"required": True,
                                              "kind": "hieroglyph"}})

    def test_duplicate_refuses(self):
        with self.assertRaises(ChartingError):
            register_template("soap-general@1.0.0", label="dup",
                              specialty="x",
                              sections={"s": {"required": True,
                                              "kind": "text"}})


def _soap():
    n = ChartNote("N-1", patient_ref="Pax-0001", author="dr-ayo",
                  template_id="soap-general@1.0.0", day=0)
    return n


class TestNotes(unittest.TestCase):
    def test_sign_refuses_empty_required(self):
        n = _soap()
        n.set_section("subjective", "headache 3 days")
        with self.assertRaises(ChartingError) as cm:
            n.sign(by="dr-ayo", day=0)
        self.assertIn("objective", str(cm.exception))

    def test_author_signs_own_note(self):
        n = _soap()
        for s in ("subjective", "objective", "assessment"):
            n.set_section(s, "x")
        n.set_section("plan", ["rest"])
        with self.assertRaises(ChartingError):
            n.sign(by="dr-other", day=0)
        n.sign(by="dr-ayo", day=1)
        self.assertEqual(n.signed_by, "dr-ayo")

    def test_signed_note_is_frozen(self):
        n = _soap()
        for s in ("subjective", "objective", "assessment"):
            n.set_section(s, "x")
        n.set_section("plan", ["rest"])
        n.sign(by="dr-ayo", day=1)
        with self.assertRaises(ChartingError):
            n.set_section("plan", ["changed"])
        # corrections are addenda that cite, never edits
        n.add_addendum(by="dr-senior", text="amended plan per review",
                       day=2)
        self.assertEqual(n.addenda[0]["amends"], "N-1")

    def test_addendum_requires_signed(self):
        n = _soap()
        with self.assertRaises(ChartingError):
            n.add_addendum(by="dr-ayo", text="too soon", day=0)

    def test_unknown_section_refuses(self):
        n = _soap()
        with self.assertRaises(ChartingError):
            n.set_section("improvised", "not in the template")

    def test_list_section_requires_list(self):
        n = _soap()
        with self.assertRaises(ChartingError):
            n.set_section("plan", "just text")

    def test_fingerprint_stable(self):
        n = _soap()
        for s in ("subjective", "objective", "assessment"):
            n.set_section(s, "x")
        n.set_section("plan", ["rest"])
        n.sign(by="dr-ayo", day=1)
        self.assertEqual(len(n.as_dict()["sha256"]), 64)

    def test_mental_health_marked_sensitive(self):
        n = ChartNote("N-2", patient_ref="Pax-0002", author="dr-psych",
                      template_id="mental-health@1.0.0", day=0)
        self.assertTrue(n.as_dict()["sensitive"])


class TestOrderSets(unittest.TestCase):
    def _registry(self):
        r = OrderSetRegistry()
        r.propose("uti-adult", version="1.0.0", label="Adult UTI",
                  items=[{"kind": "medication",
                          "label": "nitrofurantoin 100mg bd 5d",
                          "allergy_class": "nitrofurans"},
                         {"kind": "investigation",
                          "label": "urine mcs", "age_min_years": 2},
                         {"kind": "advice", "label": "hydration"}],
                  by="dr-ayo", day=0)
        return r

    def test_four_eyes(self):
        r = self._registry()
        with self.assertRaises(ChartingError):
            r.ratify("uti-adult", version="1.0.0", by="dr-ayo", day=0)

    def test_unratified_refuses_to_apply(self):
        r = self._registry()
        with self.assertRaises(ChartingError):
            r.apply("uti-adult", version="1.0.0",
                    patient={"age_years": 30, "allergies": []}, day=0)

    def test_allergy_conflict_refused_with_reason(self):
        r = OrderSetRegistry()
        r.propose("uti-adult", version="1.0.0", label="Adult UTI",
                  items=[{"kind": "medication", "label": "nitro 100mg",
                          "allergy_class": "nitrofurans"}],
                  by="dr-ayo", day=0)
        r.ratify("uti-adult", version="1.0.0", by="dr-bola", day=0)
        out = r.apply("uti-adult", version="1.0.0",
                      patient={"age_years": 30,
                               "allergies": ["nitrofurans"]}, day=1)
        self.assertEqual(out["applied"], [])
        self.assertIn("allergy conflict", out["refused"][0]["reason"])

    def test_age_bounds_gate(self):
        r = self._registry()
        r.ratify("uti-adult", version="1.0.0", by="dr-bola", day=0)
        out = r.apply("uti-adult", version="1.0.0",
                      patient={"age_years": 1, "allergies": []}, day=1)
        labels = [i["label"] for i in out["refused"]]
        self.assertTrue(any("urine mcs" in l for l in labels))
        # unknown age refuses age-bounded items rather than guessing
        out2 = r.apply("uti-adult", version="1.0.0",
                       patient={"allergies": []}, day=1)
        self.assertTrue(any("age unknown" in i["reason"]
                            for i in out2["refused"]))

    def test_version_bump_required(self):
        r = self._registry()
        with self.assertRaises(ChartingError):
            r.propose("uti-adult", version="1.0.0", label="dup",
                      items=[{"kind": "advice", "label": "x"}],
                      by="dr-c", day=1)

    def test_report(self):
        r = self._registry()
        r.ratify("uti-adult", version="1.0.0", by="dr-bola", day=0)
        rep = r.report()
        self.assertEqual(rep["version"], CHARTING_VERSION)
        self.assertEqual(rep["ratified"], 1)


if __name__ == "__main__":
    unittest.main()
