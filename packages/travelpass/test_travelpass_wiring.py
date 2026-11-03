import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("travelpass", "ips", "payrails"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from travelpass import (TravelPassRegistry, TravelPassError,  # noqa: E402
                        quote_premium, ips_document, emr_encounter,
                        notification_line, expiring_passes, settle,
                        refund_early_exit, FEE_SPLIT, POLICY)
from payrails import RailRouter  # noqa: E402

PROFILE = {"blood_group": "A-", "allergies": ["sulfa"],
           "conditions": ["t2dm"], "medications": ["metformin"],
           "emergency_contacts": [{"name": "Kunle",
                                   "phone": "+234…"}]}


def make_paid_reg():
    reg = TravelPassRegistry("ng")
    reg.enroll("T-1", full_name="Jane Doe", passport_ref="A1",
               nationality="DE", entry_day=0, exit_day=13,
               emergency_profile=dict(PROFILE), day=0)
    reg.enroll("T-2", full_name="John Roe", passport_ref="B2",
               nationality="GB", entry_day=0, exit_day=6,
               emergency_profile={}, day=0)
    for tid in ("T-1", "T-2"):
        q = quote_premium(0, 13 if tid == "T-1" else 6)
        reg.pay(tid, amount_kobo=q["premium_kobo"], method="card",
                reference=f"pay-{tid}", day=0)
    return reg


class TestEMRLink(unittest.TestCase):
    def test_ips_document_builds_importable_bundle(self):
        reg = make_paid_reg()
        bundle = ips_document(reg, "T-1", author="travelpass", day=1)
        self.assertEqual(bundle["resourceType"], "Bundle")
        titles = [s["title"] for s in bundle["entry"][0]["resource"]
                  ["section"]]
        self.assertIn("Allergies and Intolerances", titles)
        self.assertEqual(bundle["x-travelpass"]["coverage_window"],
                         [0, 13])

    def test_ips_states_none_known_not_blank(self):
        reg = make_paid_reg()
        bundle = ips_document(reg, "T-2", author="travelpass", day=1)
        sections = bundle["entry"][0]["resource"]["section"]
        alg = next(s for s in sections
                   if s["title"] == "Allergies and Intolerances")
        self.assertEqual(alg["emptyReason"]["coding"][0]["code"],
                         "none-known")

    def test_emr_encounter_links_through_sink(self):
        reg = make_paid_reg()
        p = reg.issue_pass("T-1", day=0)
        card = reg.emergency_access(p["token"], provider_id="para-1",
                                    facility="er-lagos", reason="rta",
                                    day=2)
        linked = emr_encounter(reg, "T-1", card["session_id"], day=2,
                               sink=lambda ref, enc: "EMR-ENC-77")
        self.assertEqual(linked["emr_link"], "linked")
        self.assertEqual(linked["emr_ref"], "EMR-ENC-77")
        unlinked = emr_encounter(reg, "T-1", card["session_id"], day=2)
        self.assertEqual(unlinked["emr_link"], "unlinked")
        self.assertIn("open integration task", unlinked["note"])

    def test_encounter_requires_break_glass_provenance(self):
        reg = make_paid_reg()
        with self.assertRaises(TravelPassError):
            emr_encounter(reg, "T-1", "ER-999", day=1)


class TestChannels(unittest.TestCase):
    def test_notification_line_is_pointer_not_payload(self):
        reg = make_paid_reg()
        line = notification_line(reg, "pass-issued", "T-1", day=0,
                                 content_ref="qr://pass/T-1")
        import json
        d = json.loads(line)
        self.assertEqual(d["kind"], "pass-issued")
        self.assertNotIn("blood", line)  # no PHI rides the rail
        with self.assertRaises(TravelPassError):
            notification_line(reg, "pass-issued", "T-1", day=0,
                              content_ref="")
        with self.assertRaises(TravelPassError):
            notification_line(reg, "marketing", "T-1", day=0,
                              content_ref="x")

    def test_expiring_passes_overdue_first(self):
        reg = make_paid_reg()
        expiring = expiring_passes(reg, as_of=12, within_days=3)
        self.assertEqual([e["traveler_id"] for e in expiring],
                         ["T-1"])  # T-2 expired at day 6
        expiring2 = expiring_passes(reg, as_of=4, within_days=3)
        self.assertEqual(expiring2[0]["traveler_id"], "T-2")


class TestFeeSplitAndSettlement(unittest.TestCase):
    def test_settlement_splits_admin_and_pool(self):
        reg = make_paid_reg()
        router = RailRouter()  # sandbox rails
        entry = settle(reg, router, day=1, pool_account="POOL-NG",
                       admin_account="ADMIN-NG")
        gross = (quote_premium(0, 13)["premium_kobo"]
                 + quote_premium(0, 6)["premium_kobo"])
        self.assertEqual(entry["gross_kobo"], gross)
        self.assertEqual(entry["admin_kobo"],
                         gross * FEE_SPLIT["admin_pct"] // 100)
        self.assertEqual(entry["admin_kobo"] + entry["pool_kobo"],
                         gross)
        # receipts marked settled exactly once
        self.assertTrue(all(p.get("settled") for p in reg.payments))
        with self.assertRaises(TravelPassError):  # nothing left
            settle(reg, router, day=1, pool_account="P",
                   admin_account="A")

    def test_settlement_without_rail_refuses(self):
        reg = make_paid_reg()
        with self.assertRaises(TravelPassError) as ctx:
            settle(reg, None, day=1, pool_account="P",
                   admin_account="A")
        self.assertIn("imaginary", str(ctx.exception))

    def test_refund_early_exit_prorata_pool_share_only(self):
        reg = make_paid_reg()
        out = refund_early_exit(reg, "T-1", actual_exit_day=6, day=6,
                                reason="left early")
        # unused = 13-6 = 7 days; pool share 85% of per-day
        self.assertEqual(out["amount_kobo"],
                         -(POLICY["per_day_kobo"] * 7
                           * FEE_SPLIT["pool_pct"] // 100))
        with self.assertRaises(TravelPassError):  # double refund
            refund_early_exit(reg, "T-1", actual_exit_day=6, day=6,
                              reason="again")

    def test_no_refund_after_emergency(self):
        reg = make_paid_reg()
        p = reg.issue_pass("T-1", day=0)
        card = reg.emergency_access(p["token"], provider_id="x",
                                    facility="f", reason="r", day=2)
        reg.record_emergency("T-1", session_id=card["session_id"],
                             day=2, facility="f", summary="treated")
        with self.assertRaises(TravelPassError) as ctx:
            refund_early_exit(reg, "T-1", actual_exit_day=6, day=6,
                              reason="left early")
        self.assertIn("readiness was the product", str(ctx.exception))

    def test_report_reflects_refund(self):
        reg = make_paid_reg()
        before = reg.report(as_of=2)["premium_collected_kobo"]
        refund_early_exit(reg, "T-2", actual_exit_day=3, day=3,
                          reason="flight moved")
        after = reg.report(as_of=2)["premium_collected_kobo"]
        self.assertLess(after, before)


if __name__ == "__main__":
    unittest.main()
