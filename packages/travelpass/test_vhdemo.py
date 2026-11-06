import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)
for _pkg in ("crosslink", "drugkb", "surveillance", "poe_screen", "steward",
             "medrec", "inpatient", "terminology", "mammoprog", "offlinespec",
             "teachfile", "formulary", "earlywarn", "cervixscreen", "usguard",
             "callrecall", "sepsis", "teleicu", "acuity", "telepath",
             "teleread", "reportkit", "pacs", "fertility", "rota", "triggers",
             "vitals", "mdt"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _pkg)))

from vhdemo import SCENES, build_walkthrough, render_markdown
from vhpilot import seed_pilot


class TestDemoWalkthrough(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]
        cls.script = build_walkthrough(cls.narrative)

    def test_scene_order_is_the_story_arc(self):
        self.assertEqual([s["scene"] for s in self.script["scenes"]],
                         list(SCENES))

    def test_every_scene_names_surface_and_question(self):
        for s in self.script["scenes"]:
            self.assertTrue(s["surface"])
            self.assertTrue(s["audience_question"].endswith("?"))
            self.assertGreaterEqual(len(s["talking_points"]), 2)

    def test_numbers_come_from_the_narrative(self):
        n = self.narrative
        all_text = " ".join(tp for s in self.script["scenes"]
                            for tp in s["talking_points"])
        # denominators that must appear verbatim
        self.assertIn(str(n["arrivals"]), all_text)
        self.assertIn(str(n["covers"]), all_text)
        self.assertIn(str(n["claims"]), all_text)
        self.assertIn(str(n["medevacs"]), all_text)
        self.assertIn(str(n["seed"]), all_text)
        self.assertIn(f"{n['loss_ratio']:.4f}", all_text)

    def test_synthetic_means_synthetic_is_on_the_record(self):
        self.assertIn("Pax-NNNN", self.script["disclaimer"])
        self.assertIn("SIM-NNNN", self.script["disclaimer"])
        arrival = self.script["scenes"][0]
        self.assertTrue(any("synthetic" in tp.lower()
                            for tp in arrival["talking_points"]))

    def test_missing_narrative_key_refuses(self):
        broken = dict(self.narrative)
        del broken["loss_ratio"]
        with self.assertRaises(ValueError):
            build_walkthrough(broken)

    def test_unacknowledged_br10_stops_the_demo(self):
        rigged = dict(self.narrative)
        rigged["br10_all_acknowledged"] = False
        script = build_walkthrough(rigged)
        surv = next(s for s in script["scenes"] if s["scene"] == "surveillance")
        self.assertTrue(any("STOP THE DEMO" in tp
                            for tp in surv["talking_points"]))

    def test_currency_symbol_flows_through(self):
        script = build_walkthrough(self.narrative, currency_symbol="₦")
        money = next(s for s in script["scenes"] if s["scene"] == "money")
        self.assertTrue(any("₦" in tp for tp in money["talking_points"]))

    def test_markdown_renders_run_of_show(self):
        md = render_markdown(self.script)
        self.assertIn("# VHC scheme — ministry walkthrough", md)
        self.assertEqual(md.count("## Scene"), len(SCENES))
        self.assertIn("**Stand on:**", md)
        self.assertIn("silently asking", md)

    def test_script_is_deterministic_from_seed(self):
        again = build_walkthrough(seed_pilot(seed=42)["narrative"])
        self.assertEqual(render_markdown(self.script), render_markdown(again))

    def test_different_seed_different_script(self):
        other = build_walkthrough(seed_pilot(seed=7)["narrative"])
        self.assertNotEqual(render_markdown(self.script),
                            render_markdown(other))


class TestDemoCli(unittest.TestCase):
    def test_main_green_pilot_exits_zero(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "walkthrough.md")
            rc = vhdemo.main(["--seed", "42", "--out", out, "--json"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(out))
            self.assertIn("# VHC scheme — ministry walkthrough",
                          open(out).read())

    def test_main_writes_run_of_show(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "ros.md")
            self.assertEqual(vhdemo.main(["--seed", "42", "--out", out]), 0)
            md = open(out).read()
            self.assertEqual(md.count("## Scene"), 8)


class TestDemoRedFindings(unittest.TestCase):
    """You do not present past a red finding: a narrative with an
    unacknowledged alert or a settlement breach must exit 1. The CLI
    reads the narrative; we exercise the gate via a rigged seed result
    patched into seed_pilot."""

    def _rig(self, **overrides):
        import vhdemo
        import vhpilot
        original = vhpilot.seed_pilot
        narrative = dict(seed_pilot(seed=42)["narrative"])
        narrative.update(overrides)
        vhdemo_seed = lambda **kw: {"narrative": narrative, "registries": {}}
        # patch the name main() resolves at call time
        import sys as _sys
        vhpilot_mod = _sys.modules["vhpilot"]
        vhpilot_mod.seed_pilot = vhdemo_seed
        return original

    def _unrig(self, original):
        import sys as _sys
        _sys.modules["vhpilot"].seed_pilot = original

    def test_unacknowledged_br10_exits_one(self):
        import vhdemo
        original = self._rig(br10_all_acknowledged=False)
        try:
            self.assertEqual(vhdemo.main(["--seed", "42"]), 1)
        finally:
            self._unrig(original)

    def test_settlement_breach_exits_one(self):
        import vhdemo
        original = self._rig(settlement_breaches=2)
        try:
            self.assertEqual(vhdemo.main(["--seed", "42"]), 1)
        finally:
            self._unrig(original)


if __name__ == "__main__":
    unittest.main()
