"""Tests for crosslink seams 17 (travelpass → surveillance) and
18 (poe_screen → travelpass pass record)."""
import datetime
import os
import sys
import unittest

PKG = os.path.join(os.path.dirname(__file__), "..")
for p in ("crosslink", "travelpass", "surveillance", "poe_screen",
          "drugkb"):
    sys.path.insert(0, os.path.join(PKG, p))

import crosslink  # noqa: E402
from poe_screen import AlertList, BorderPost, PoEError  # noqa: E402
from surveillance import Surveillance  # noqa: E402
from travelpass import TravelPassRegistry  # noqa: E402

BASE = datetime.date(2026, 1, 1)


class TestImportedCaseSignal(unittest.TestCase):
    def setUp(self):
        self.reg = TravelPassRegistry(country_code="ng")
        self.reg.enroll("T1", full_name="Amina Bello", passport_ref="P1",
                        nationality="ng", entry_day=1, exit_day=10,
                        emergency_profile={"conditions": ["measles"]},
                        day=1)
        self.sess = self.reg.issue_pass("T1", day=1)
        self.tok = self.sess["token"]
        self.surveillance = Surveillance()

    def _emergency(self, summary="febrile rash consistent with measles",
                   day=3, facility="MMA-Clinic"):
        # pay in full so coverage is active, then break the glass
        self.reg.pay("T1", amount_kobo=10**9, method="transfer",
                     reference="R1", day=1)
        access = self.reg.emergency_access(
            self.tok, provider_id="dr1", facility=facility,
            reason="unconscious", day=day)
        return self.reg.record_emergency(
            "T1", session_id=access["session_id"], day=day,
            facility=facility, summary=summary)

    def test_notifiable_condition_becomes_syndrome_event(self):
        self._emergency()
        out = crosslink.imported_case_signal(
            self.reg, self.surveillance, traveler_id="T1", day=3,
            base_date=BASE)
        self.assertEqual(out["imported_signals"], 1)
        self.assertEqual(out["events"][0]["syndrome"],
                         "measles_like_rash")
        self.assertEqual(out["events"][0]["ts"],
                         (BASE + datetime.timedelta(days=3)).isoformat())

    def test_threshold_alert_fires_from_imported_signals(self):
        # measles_like_rash threshold is 3 within 7 days — three
        # travelers, same area
        for tid in ("T2", "T3"):
            self.reg.enroll(tid, full_name="X Y",
                            passport_ref=f"P{tid}", nationality="ng",
                            entry_day=1, exit_day=10,
                            emergency_profile={"conditions": ["measles"]},
                            day=1)
            s = self.reg.issue_pass(tid, day=1)
            self.reg.pay(tid, amount_kobo=10**9, method="transfer",
                         reference=f"R{tid}", day=1)
            acc = self.reg.emergency_access(
                s["token"], provider_id="dr1", facility="MMA-Clinic",
                reason="collapse", day=3)
            self.reg.record_emergency(tid, session_id=acc["session_id"],
                                      day=3, facility="MMA-Clinic",
                                      summary="measles suspected")
        self._emergency()
        outs = []
        for tid in ("T1", "T2", "T3"):
            outs.append(crosslink.imported_case_signal(
                self.reg, self.surveillance, traveler_id=tid, day=3,
                base_date=BASE))
        self.assertTrue(all(o["imported_signals"] >= 1 for o in outs))
        alerts = [a for o in outs for a in o["alerts"]]
        self.assertTrue(
            any(a["payload"]["syndrome"] == "measles_like_rash"
                for a in alerts))
        # one alert per window — re-evaluation does not re-fire
        self.assertEqual(
            self.surveillance.evaluate(datetime.datetime(2026, 1, 4)),
            [])

    def test_no_notifiable_condition_states_zero_signal(self):
        self.reg.enroll("T4", full_name="Calm Person", passport_ref="P4",
                        nationality="ng", entry_day=1, exit_day=10,
                        emergency_profile={"conditions": ["hypertension"]},
                        day=1)
        out = crosslink.imported_case_signal(
            self.reg, self.surveillance, traveler_id="T4", day=3,
            base_date=BASE)
        self.assertEqual(out["imported_signals"], 0)
        self.assertIn("stated", out["note"])

    def test_emergency_card_exposes_public_health_hold_absence(self):
        # absence stays absent: no hold key until seam 18 sets one
        t = self.reg._t("T1")
        self.assertNotIn("public_health_hold", t)


class TestPoEEntryScreen(unittest.TestCase):
    def setUp(self):
        self.reg = TravelPassRegistry(country_code="ng")
        self.reg.enroll("T1", full_name="Amina Bello", passport_ref="P1",
                        nationality="ng", entry_day=1, exit_day=10,
                        emergency_profile={}, day=1)
        self.alerts = AlertList()
        self.alerts.publish("who-2026-w01", ["freedonia"], "2026-01-01")
        self.post = BorderPost("MMA-LOS", self.alerts)

    def test_pass_outcome_stamps_record_no_hold(self):
        out = crosslink.poe_entry_screen(
            self.reg, self.post, "T1", list_version="who-2026-w01",
            travel_history_21d=["ghana"], temp_c=36.5, symptoms=[],
            screener="officer-1", day="2026-01-02")
        self.assertEqual(out["disposition"], "pass")
        t = self.reg._t("T1")
        self.assertEqual(t["poe_screens"][-1]["list_version"],
                         "who-2026-w01")
        self.assertNotIn("public_health_hold", t)

    def test_isolate_and_notify_sets_public_health_hold(self):
        out = crosslink.poe_entry_screen(
            self.reg, self.post, "T1", list_version="who-2026-w01",
            travel_history_21d=["freedonia"], temp_c=38.5, symptoms=[],
            screener="officer-1", day="2026-01-02")
        self.assertEqual(out["disposition"], "isolate-and-notify")
        t = self.reg._t("T1")
        self.assertEqual(t["public_health_hold"]["by"], "officer-1")
        self.assertEqual(t["public_health_hold"]["trace_seed"],
                         out["trace_seed"])

    def test_hold_clears_only_with_reference(self):
        crosslink.poe_entry_screen(
            self.reg, self.post, "T1", list_version="who-2026-w01",
            travel_history_21d=["freedonia"], temp_c=38.5, symptoms=[],
            screener="officer-1", day="2026-01-02")
        with self.assertRaises(crosslink.CrosslinkError):
            crosslink.clear_public_health_hold(
                self.reg, "T1", day=4, by="dr-1", clearance_ref="")
        out = crosslink.clear_public_health_hold(
            self.reg, "T1", day=4, by="dr-1",
            clearance_ref="PCR-NEG-2026-001")
        self.assertNotIn("public_health_hold", self.reg._t("T1"))
        self.assertEqual(out["clearance_ref"], "PCR-NEG-2026-001")

    def test_stale_list_refuses_loudly(self):
        self.alerts.publish("who-2026-w02", ["freedonia", "narnia"],
                            "2026-01-08")
        with self.assertRaises(PoEError) as cm:
            crosslink.poe_entry_screen(
                self.reg, self.post, "T1", list_version="who-2026-w01",
                travel_history_21d=[], temp_c=None, symptoms=[],
                screener="officer-1", day="2026-01-09")
        self.assertIn("stale", str(cm.exception))

    def test_clearing_nonexistent_hold_refuses(self):
        with self.assertRaises(crosslink.CrosslinkError) as cm:
            crosslink.clear_public_health_hold(
                self.reg, "T1", day=4, by="dr-1", clearance_ref="X")
        self.assertIn("no public-health hold", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
