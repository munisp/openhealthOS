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

from vhdemo import AUDIENCE_ARCS, SCENES, build_walkthrough, render_markdown
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


class TestAudiences(unittest.TestCase):
    """The audience chooses the arc, never the numbers."""

    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]

    def test_campus_arc_leads_with_care_and_covers_all_scenes(self):
        arc = AUDIENCE_ARCS["campus"]
        self.assertEqual(sorted(arc), sorted(SCENES))      # same scenes
        self.assertEqual(arc[0], "arrival")
        self.assertEqual(arc[1], "care")                   # clinic door first
        self.assertEqual(arc[-1], "proof")                 # always end on proof
        self.assertNotEqual(list(arc), list(SCENES))       # different arc

    def test_campus_scenes_carry_stakes(self):
        script = build_walkthrough(self.narrative, audience="campus")
        self.assertEqual(script["audience"], "campus")
        for s in script["scenes"]:
            self.assertIn("stakes", s)
            self.assertTrue(s["stakes"])

    def test_ministry_is_the_default_and_stays_compatible(self):
        script = build_walkthrough(self.narrative)
        self.assertEqual(script["audience"], "ministry")
        self.assertEqual([s["scene"] for s in script["scenes"]], list(SCENES))
        # ministry has no stakes layer — absence of a frame is honest
        self.assertTrue(all("stakes" not in s for s in script["scenes"]))

    def test_audience_changes_arc_never_numbers(self):
        ministry_md = render_markdown(build_walkthrough(self.narrative))
        campus_md = render_markdown(
            build_walkthrough(self.narrative, audience="campus"))
        n = self.narrative
        for needle in (str(n["arrivals"]), str(n["covers"]),
                       str(n["claims"]), f"{n['loss_ratio']:.4f}"):
            self.assertIn(needle, ministry_md)
            self.assertIn(needle, campus_md)
        self.assertNotEqual(ministry_md, campus_md)

    def test_unknown_audience_refused(self):
        with self.assertRaises(ValueError):
            build_walkthrough(self.narrative, audience=" Martians ")

    def test_campus_markdown_renders_stakes_and_header(self):
        md = render_markdown(build_walkthrough(self.narrative,
                                               audience="campus"))
        self.assertIn("# VHC scheme — campus walkthrough", md)
        self.assertIn("**Why this room cares:**", md)
        self.assertEqual(md.count("## Scene"), len(SCENES))

    def test_cli_audience_flag(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "campus.md")
            rc = vhdemo.main(["--seed", "42", "--audience", "campus",
                              "--currency", "₦", "--out", out])
            self.assertEqual(rc, 0)
            md = open(out).read()
            self.assertIn("campus walkthrough", md)
            self.assertIn("₦", md)

    def test_red_finding_gate_holds_for_campus_too(self):
        import vhdemo
        import sys as _sys
        narrative = dict(self.narrative)
        narrative["br10_all_acknowledged"] = False
        vhpilot_mod = _sys.modules["vhpilot"]
        original = vhpilot_mod.seed_pilot
        vhpilot_mod.seed_pilot = lambda **kw: {"narrative": narrative,
                                               "registries": {}}
        try:
            self.assertEqual(vhdemo.main(["--audience", "campus"]), 1)
        finally:
            vhpilot_mod.seed_pilot = original


class TestCaboVerde(unittest.TestCase):
    """The archipelago tailoring: rescue moves up the arc, escudos ride
    the BCV peg from the profile — never a hardcoded rate."""

    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]

    def test_arc_leads_border_then_rescue(self):
        arc = AUDIENCE_ARCS["cabo-verde"]
        self.assertEqual(sorted(arc), sorted(SCENES))
        self.assertEqual(arc[:3], ("arrival", "verdict", "rescue"))
        self.assertEqual(arc[-1], "proof")

    def test_stakes_speak_archipelago(self):
        script = build_walkthrough(self.narrative, audience="cabo-verde")
        stakes = " ".join(s.get("stakes", "") for s in script["scenes"])
        self.assertIn("nine inhabited islands", stakes)
        self.assertIn("Amílcar Cabral", stakes)
        self.assertIn("Sal", stakes)
        rescue = next(s for s in script["scenes"] if s["scene"] == "rescue")
        self.assertIn("inter-island medevac", rescue["stakes"])

    def test_cve_display_rides_the_profile_peg(self):
        from vhcover import VHC_PROFILES
        peg = VHC_PROFILES["cv-vhc@1.0.0"]["alt_currency"][1]
        script = build_walkthrough(self.narrative, audience="cabo-verde",
                                   currency_symbol="cve")
        money = next(s for s in script["scenes"] if s["scene"] == "money")
        premium_cve = self.narrative["premium_collected_minor"] / 100 * peg
        escudos = int(premium_cve)
        centavos = round((premium_cve - escudos) * 100)
        if centavos == 100:
            escudos, centavos = escudos + 1, 0
        expect = f"{escudos:,}".replace(",", ".") + f"${centavos:02d}"
        shown = " ".join(money["talking_points"])
        # the escudo figure must equal EUR minor / 100 x peg, formatted
        # CV-style with '.' thousands and '$' before the decimals
        self.assertIn(expect, shown)
        self.assertNotIn("€", shown)   # no euro leakage in a CVE script

    def test_cve_format_carry_on_rounding(self):
        from vhdemo import _fmt_cve
        # craft an amount whose centavos round up to 100
        self.assertEqual(_fmt_cve(1), "1$10")   # €0.01 x 110.265 = 1.10265

    def test_cli_cabo_verde_with_escudos(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "cv.md")
            rc = vhdemo.main(["--seed", "42", "--audience", "cabo-verde",
                              "--currency", "cve", "--out", out])
            self.assertEqual(rc, 0)
            md = open(out).read()
            self.assertIn("cabo-verde walkthrough", md)
            self.assertIn("$", md)


if __name__ == "__main__":
    unittest.main()
