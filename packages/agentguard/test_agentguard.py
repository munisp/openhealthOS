"""Agent doctrine tests: the envelope + all seven agents."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "protocolpack"))
sys.path.insert(0, os.path.join(HERE, "..", "protocolpack", "packs"))

from guard import ScopeViolation  # noqa: E402
from providers import NullProvider, CallableProvider  # noqa: E402
from agents import (Summariser, Scribe, Shepherd, Watchdog,  # noqa: E402
                    CoPilot, Scout, Mentor)
from packs.ng_standing_orders import PACKS  # noqa: E402

KEY = b"agent-guard-test-key-32-bytes-0000"
HIST = [{"at": i, "type": "encounter", "summary": s}
        for i, s in enumerate(["fever, RDT+", "started ACT",
                               "BP 150/95", "adherence counsel"])]


class TestEnvelope(unittest.TestCase):
    def test_out_of_scope_output_impossible(self):
        s = Summariser(KEY)
        with self.assertRaises(ScopeViolation):
            s.draft("prescription", {"drug": "x"})  # no such scope

    def test_tampered_draft_refused_at_confirm(self):
        d = Summariser(KEY).summarise("p1", HIST)
        d["payload"]["prose"] = "edited to say something else"
        r = Summariser(KEY).confirm(d, "nurse-a", {"chart:read"})
        self.assertEqual(r["reason"], "draft-tampered")

    def test_wrong_human_scope_refused(self):
        d = Summariser(KEY).summarise("p1", HIST)
        r = Summariser(KEY).confirm(d, "receptionist", {"front-desk"})
        self.assertEqual(r["reason"], "human-scope-insufficient")

    def test_double_confirm_refused_and_null_provider_declared(self):
        ag = Summariser(KEY, provider=NullProvider())
        d = ag.summarise("p1", HIST)
        self.assertEqual(d["llm"], "unavailable")
        self.assertEqual(d["payload"]["regime"], "template")
        ok = ag.confirm(d, "nurse-a", {"chart:read"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ag.confirm(d, "nurse-a", {"chart:read"})["reason"],
                         "already-confirmed")


class TestAgents(unittest.TestCase):
    def test_summariser_cites_sources(self):
        d = Summariser(KEY).summarise("p1", HIST)
        for c in d["payload"]["claims"]:
            self.assertIn("source", c)
        self.assertIn("[0]", d["payload"]["prose"])

    def test_summariser_model_regime_via_provider(self):
        p = CallableProvider(lambda prompt, **kw: "Model summary [0].")
        d = Summariser(KEY, provider=p).summarise("p1", HIST)
        self.assertEqual(d["llm"], "active")
        self.assertEqual(d["payload"]["regime"], "model")

    def test_scribe_asks_one_question_on_ambiguity(self):
        sc = Scribe(KEY)
        r = sc.transcribe("malaria, gave amodiaquine 200mg")
        self.assertIn("question", r)  # amodiaquine unmapped -> question
        self.assertEqual(r["question"]["kind"], "clarification-question")
        r2 = sc.transcribe("malaria, gave paracetamol 500mg")
        self.assertNotIn("question", r2)
        self.assertIn("diagnosis", r2["draft"]["payload"]["fields"])

    def test_shepherd_drafts_only_for_known_events(self):
        sh = Shepherd(KEY)
        self.assertIsNone(sh.on_event({"type": "heartbeat"}))
        d = sh.on_event({"type": "result_released", "patient_ref": "p1"})
        self.assertEqual(d["payload"]["action"], "draft.patient-notification")

    def test_watchdog_restock_and_redistribution(self):
        w = Watchdog(KEY)
        day = 86400
        cons = {"amox": [(i * day, 10) for i in range(30)]}
        drafts = w.assess({"amox": 50}, cons,
                          [{"item": "insulin", "qty": 100, "days_left": 30,
                            "facilities_burning": ["OHF-B"]}])
        kinds = {d["kind"] for d in drafts}
        self.assertEqual(kinds, {"restock-draft", "redistribution-draft"})
        # nothing expiring far out, healthy stock => silence
        day2 = 86400
        cons2 = {"x": [(i * day2, 1) for i in range(30)]}
        self.assertEqual(w.assess({"x": 500}, cons2, []), [])

    def test_copilot_cites_pack_and_only_escalates(self):
        cp = CoPilot(KEY, packs=PACKS)
        a = cp.ask("fever", "how many days fever before danger?")
        self.assertEqual(a["payload"]["citation"]["pack"], "ngso-fever-1.0.0-seed")
        b = cp.ask("fever", "can I give chemotherapy?")
        self.assertTrue(b["payload"]["escalate"])  # uncovered -> escalate
        adv = cp.sniff([("s1", "a"), ("s2", "b"), ("s3", "c"), ("s4", "d")])
        self.assertEqual(adv["payload"]["direction"], "escalate-only")
        self.assertIsNone(cp.sniff([("s1", "a")]))

    def test_scout_flags_uptick_stays_quiet_otherwise(self):
        s = Scout(KEY)
        drafts = s.scan({"fever": [10, 11, 12, 30]}, baseline={"fever": 11})
        self.assertEqual(drafts[0]["payload"]["ratio"], round(30 / 11, 2))
        self.assertEqual(s.scan({"fever": [10, 11, 12]}, baseline={"fever": 11}), [])

    def test_mentor_cites_or_refuses(self):
        m = Mentor(KEY, packs=PACKS)
        a = m.teach("what are the danger signs of fever?")
        self.assertIsNotNone(a["payload"]["answer"])
        self.assertEqual(a["payload"]["citation"]["step"], "danger")
        b = m.teach("how do I perform surgery?")
        self.assertIsNone(b["payload"]["answer"])
        note = m.assess("chw-7", {"fever": True, "htn": False})
        self.assertEqual(note["payload"]["gaps"], ["htn"])
        self.assertFalse(note["payload"]["ready"])


if __name__ == "__main__":
    unittest.main()
