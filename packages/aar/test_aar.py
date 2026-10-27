#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "contacttrace"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "labtrack"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signalledger"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "casedef"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ringops"))
from aar import render_aar  # noqa: E402
from contacttrace import ContactGraph  # noqa: E402
from labtrack import LabTracker  # noqa: E402
from ledger import SignalLedger  # noqa: E402
from casedef import CaseDefRegistry  # noqa: E402
from ringops import Ring  # noqa: E402


class TestAAR(unittest.TestCase):
    def test_full_aar_from_live_records(self):
        g = ContactGraph()
        g.add_case("CASE-1", "2026-10-20")
        g.add_contact("CASE-1", "C-01", "2026-10-07")
        g.checkin("C-01", "2026-10-10")
        g.sweep("2026-10-28")

        lt = LabTracker()
        for i, h in enumerate((12, 14, 16)):
            sid = f"S-{i}"
            lt.register(sid, "CASE-1", "NRL", "2026-10-20T08:00:00Z")
            lt.advance(sid, "in-transit", "2026-10-20T09:00:00Z")
            lt.advance(sid, "received", "2026-10-20T20:00:00Z")
            lt.result(sid, "negative", "2026-10-21T08:00:00Z")

        sl = SignalLedger()
        sl.register("SIG-1", "scout", "2026-10-10", 2.0)
        sl.label("SIG-1", "verified-case", "2026-10-13", "pcr")

        cd = CaseDefRegistry()
        cd.activate("ebola", {"version": "v1",
                              "suspect_criteria": {"fever": True},
                              "confirmed_criteria": {}}, "2026-10-20",
                    author="ncdc")
        cd.screen("ebola", "v1", {"fever": True}, "PHC-007", "2026-10-21")

        ring = Ring("R-1", "CASE-1", ["C-01"])
        ring.consent("C-01", "2026-10-21")
        ring.dose("C-01", "2026-10-21", "vacc-1")

        md = render_aar("Ebola Bundibugyo, FCT importation",
                        ("2026-10-07", "2026-10-28"),
                        graph=g, labs=lt, ledger=sl, casedefs=cd,
                        casedef_name="ebola", rings=[ring],
                        notes=["PoE screening gaps at land borders"])
        self.assertIn("Cases: 1", md)
        self.assertIn("median", md)
        self.assertIn("precision 100.0%", md)
        self.assertIn("v1: 1", md)
        self.assertIn("R-1", md)
        self.assertIn("PoE screening gaps", md)
        self.assertNotIn("— ABSENT", md)

    def test_absent_sections_are_themselves_findings(self):
        md = render_aar("Test event", ("2026-10-01", "2026-10-30"))
        self.assertIn("Contact tracing — ABSENT", md)
        self.assertIn("absence is itself a", md)

    def test_unresolved_signals_noted(self):
        sl = SignalLedger()
        sl.register("SIG-9", "ebs", "2026-10-01", 2.0)
        md = render_aar("X", ("2026-10-01", "2026-10-30"), ledger=sl)
        self.assertIn("never received outcome labels", md)


if __name__ == "__main__":
    unittest.main()
