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


class TestPortugueseLayer(unittest.TestCase):
    """The PT presenter layer: translated points, stated fallbacks,
    coverage with denominators — never a silently half-translated deck."""

    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]

    def test_full_pt_coverage_no_fallbacks(self):
        script = build_walkthrough(self.narrative, audience="cabo-verde",
                                   lang="pt")
        tr = script["translation"]
        self.assertEqual(tr["translated"], tr["total"])
        self.assertEqual(tr["fallback_keys"], [])

    def test_pt_numbers_identical_to_en(self):
        en = build_walkthrough(self.narrative, audience="cabo-verde",
                               currency_symbol="cve")
        pt = build_walkthrough(self.narrative, audience="cabo-verde",
                               currency_symbol="cve", lang="pt")
        n = self.narrative
        for needle in (str(n["arrivals"]), str(n["covers"]),
                       str(n["claims"]), f"{n['loss_ratio']:.4f}",
                       "1.026.842$81"):
            en_text = " ".join(tp for s in en["scenes"]
                               for tp in s["talking_points"])
            pt_text = " ".join(tp for s in pt["scenes"]
                               for tp in s["talking_points"])
            self.assertIn(needle, en_text)
            self.assertIn(needle, pt_text)

    def test_pt_frames_and_stakes_translated(self):
        script = build_walkthrough(self.narrative, audience="cabo-verde",
                                   lang="pt")
        rescue = next(s for s in script["scenes"] if s["scene"] == "rescue")
        self.assertIn("quadro de evacuações", rescue["surface"])
        self.assertTrue(rescue["audience_question"].endswith("?"))
        self.assertIn("nove ilhas habitadas", rescue["stakes"])

    def test_missing_pt_key_falls_back_stated_and_counted(self):
        import vhdemo
        key = "proof.suites"
        saved = vhdemo.PT_TEMPLATES.pop(key)
        try:
            script = build_walkthrough(self.narrative, lang="pt")
            tr = script["translation"]
            self.assertIn(key, tr["fallback_keys"])
            self.assertEqual(tr["translated"], tr["total"] - 1)
            proof = next(s for s in script["scenes"] if s["scene"] == "proof")
            fallen = proof["talking_points"][
                proof["talking_point_keys"].index(key)]
            self.assertIn("sem tradução em português", fallen)
            self.assertIn("declarado", fallen)
        finally:
            vhdemo.PT_TEMPLATES[key] = saved

    def test_unknown_lang_refused(self):
        with self.assertRaises(ValueError):
            build_walkthrough(self.narrative, lang="de")

    def test_pt_markdown_labels_and_coverage_line(self):
        md = render_markdown(build_walkthrough(self.narrative,
                                               audience="cabo-verde",
                                               lang="pt"))
        self.assertIn("# Esquema VHC — apresentação cabo-verde", md)
        self.assertIn("**Posicione-se em:**", md)
        self.assertIn("**Porque esta sala se importa:**", md)
        self.assertIn("cobertura de tradução:", md)
        self.assertIn("recuos declarados: 0", md)

    def test_en_script_has_no_pt_markers(self):
        md = render_markdown(build_walkthrough(self.narrative))
        self.assertNotIn("Posicione-se", md)
        self.assertNotIn("cobertura de tradução", md)

    def test_cli_lang_pt(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "cv-pt.md")
            rc = vhdemo.main(["--seed", "42", "--audience", "cabo-verde",
                              "--currency", "cve", "--lang", "pt",
                              "--out", out])
            self.assertEqual(rc, 0)
            self.assertIn("Esquema VHC", open(out).read())

    def test_pt_disclaimer_translated(self):
        script = build_walkthrough(self.narrative, lang="pt")
        self.assertIn("sintéticos", script["disclaimer"])
        self.assertIn("Pax-NNNN", script["disclaimer"])


class TestEcowasFrench(unittest.TestCase):
    """The regional commission deck: surveillance leads, French carries
    the room, fallbacks stay stated."""

    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]

    def test_ecowas_arc_leads_with_surveillance_after_arrival(self):
        arc = AUDIENCE_ARCS["ecowas"]
        self.assertEqual(sorted(arc), sorted(SCENES))
        self.assertEqual(arc[0], "arrival")
        self.assertEqual(arc[1], "surveillance")
        self.assertLess(arc.index("care"), arc.index("money"))
        self.assertEqual(arc[-1], "proof")

    def test_fr_full_coverage_no_fallbacks(self):
        script = build_walkthrough(self.narrative, audience="ecowas",
                                   lang="fr")
        tr = script["translation"]
        self.assertEqual(tr["translated"], tr["total"])
        self.assertEqual(tr["fallback_keys"], [])

    def test_fr_numbers_identical_to_en(self):
        en = build_walkthrough(self.narrative, audience="ecowas")
        fr = build_walkthrough(self.narrative, audience="ecowas", lang="fr")
        for needle in ("328", "10", "0.0723", "€9,312.50"):
            self.assertIn(needle, " ".join(tp for s in en["scenes"]
                                           for tp in s["talking_points"]))
            self.assertIn(needle, " ".join(tp for s in fr["scenes"]
                                           for tp in s["talking_points"]))

    def test_fr_frames_stakes_disclaimer(self):
        script = build_walkthrough(self.narrative, audience="ecowas",
                                   lang="fr")
        surv = next(s for s in script["scenes"] if s["scene"] == "surveillance")
        self.assertIn("alertes BR-10", surv["surface"])
        self.assertTrue(surv["audience_question"].endswith("?"))
        self.assertIn("santé régionale", surv["stakes"])
        self.assertIn("synthétiques", script["disclaimer"])

    def test_fr_markdown_labels_and_coverage(self):
        md = render_markdown(build_walkthrough(self.narrative,
                                               audience="ecowas",
                                               lang="fr"))
        self.assertIn("# Dispositif VHC — présentation ecowas", md)
        self.assertIn("**Positionnez-vous sur:**", md)
        self.assertIn("**Pourquoi cette salle s'en soucie:**", md)
        self.assertIn("couverture de traduction", md)
        self.assertIn("basculements déclarés : 0", md)

    def test_fr_fallback_stated_when_stakes_missing(self):
        # campus has EN stakes but no FR stakes table — the fallback
        # must be stated and counted, never silent
        script = build_walkthrough(self.narrative, audience="campus",
                                   lang="fr")
        self.assertTrue(any(k.startswith("stakes.campus.")
                            for k in script["translation"]["fallback_keys"]))
        arrival = script["scenes"][0]
        self.assertIn("sans traduction française", arrival["stakes"])

    def test_cli_ecowas_fr(self):
        import vhdemo
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "ecowas.md")
            rc = vhdemo.main(["--seed", "42", "--audience", "ecowas",
                              "--lang", "fr", "--out", out])
            self.assertEqual(rc, 0)
            self.assertIn("Dispositif VHC", open(out).read())


class TestDemoBoard(unittest.TestCase):
    """The board wires scenes to surfaces that exist — and refuses a
    dead link, because a cable not plugged in is not an interface."""

    @classmethod
    def setUpClass(cls):
        cls.narrative = seed_pilot(seed=42)["narrative"]
        cls.script = build_walkthrough(cls.narrative)

    def test_board_links_only_existing_surfaces(self):
        from vhdemo import SCENE_SURFACES, build_board
        board = build_board(self.script, self.narrative)
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..", "apps", "pwa"))
        for s in board["scenes"]:
            if s["link"]:
                self.assertTrue(os.path.exists(os.path.join(pwa, s["link"])),
                                f"{s['link']} must exist in apps/pwa")
                self.assertFalse(s["unwired"])
            else:
                self.assertTrue(s["unwired"])

    def test_wired_and_unwired_scenes_named_honestly(self):
        from vhdemo import build_board
        board = build_board(self.script, self.narrative)
        by_scene = {s["scene"]: s for s in board["scenes"]}
        self.assertEqual(by_scene["arrival"]["link"], "vhc-verify.html")
        self.assertEqual(by_scene["rescue"]["link"], "dispatch.html")
        self.assertEqual(by_scene["care"]["link"], "traveler.html")
        self.assertEqual(by_scene["money"]["link"], "vhc-fund.html")
        self.assertEqual(by_scene["surveillance"]["link"], "vhc-br10.html")
        self.assertEqual(by_scene["oversight"]["link"], "vhc-helpdesk.html")
        # the terminal is the surface — proof stays unwired by design
        self.assertIsNone(by_scene["proof"]["link"])
        self.assertTrue(by_scene["proof"]["unwired"])

    def test_board_carries_red_findings(self):
        from vhdemo import build_board
        rigged = dict(self.narrative)
        rigged["br10_all_acknowledged"] = False
        rigged["settlement_breaches"] = 2
        board = build_board(self.script, rigged)
        self.assertEqual(len(board["red_findings"]), 2)
        self.assertTrue(any("BR-10" in r for r in board["red_findings"]))

    def test_cli_board_out_refuses_dead_links(self):
        import vhdemo
        import vhdemo as m
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "board.json")
            # tempdir has no PWA surfaces — links would be dead
            rc = m.main(["--seed", "42", "--board-out", out])
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(out))

    def test_cli_board_out_writes_into_pwa(self):
        import vhdemo as m
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..", "apps", "pwa"))
        out = os.path.join(pwa, "vhc-demo-board.json")
        try:
            rc = m.main(["--seed", "42", "--board-out", out])
            self.assertEqual(rc, 0)
            import json as _json
            board = _json.load(open(out))
            self.assertEqual(board["seed"], 42)
            self.assertEqual(board["red_findings"], [])
            self.assertEqual(len(board["scenes"]), 8)
        finally:
            if os.path.exists(out):
                os.unlink(out)


if __name__ == "__main__":
    unittest.main()
