"""E2E composition + pack governance lifecycle."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
for pkg in ("protocolpack", "protocolpack/packs"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from day_in_life import run_day  # noqa: E402
from packreview import PackRegistry  # noqa: E402
from packs.ng_standing_orders import PACKS  # noqa: E402

KEY = b"lga-shared-hmac-key-32-bytes-00000"


class TestDayInLife(unittest.TestCase):
    def test_full_flow_composes(self):
        log = run_day()  # every assertion inside is the test
        self.assertEqual(log["pack_status"], "live")
        self.assertEqual(log["referral"]["facility"], "OHF-LGA-GENERAL")
        self.assertEqual(log["shepherd"], {"drafted": 2, "confirmed": 1})
        self.assertEqual(log["queue_first"], "pseudo-0042")


class TestPackGovernance(unittest.TestCase):
    def test_unreviewed_pack_refused_in_production(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="a", now=1)
        with self.assertRaises(PermissionError):
            reg.session("fever", "chw")               # draft: no sessions
        s = reg.session("fever", "chw", allow_seed=True)  # training only
        self.assertEqual(s.pack_status, "draft")      # status on the record

    def test_author_cannot_self_approve_and_note_required(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="dr-a", now=1)
        self.assertEqual(reg.review("fever", "dr-a", "consultant", True, "ok")["reason"],
                         "author-cannot-self-approve")
        self.assertEqual(reg.review("fever", "dr-b", "consultant", True, "")["reason"],
                         "note-required")
        self.assertEqual(reg.review("fever", "chw-x", "chw", True, "fine")["reason"],
                         "insufficient-reviewer-role")

    def test_rejected_pack_unusable_and_edit_returns_to_draft(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="a", now=1)
        reg.review("fever", "dr-b", "consultant", False, "needs hematology step", now=2)
        with self.assertRaises(PermissionError):
            reg.session("fever", "chw", allow_seed=True)  # rejected is rejected
        # approve, then edit -> back to draft, honestly logged
        reg.register("htn", PACKS["hypertension-followup"], author="a", now=1)
        reg.review("htn", "dr-b", "consultant", True, "approved", now=2)
        edited = dict(PACKS["hypertension-followup"], version="tampered-2")
        reg.register("htn", edited, author="a", now=3)
        self.assertEqual(reg.packs["htn"]["status"], "draft")
        self.assertIn("edit-after-signoff",
                      [h["event"] for h in reg.packs["htn"]["history"]])


if __name__ == "__main__":
    unittest.main()


class TestOutbreakComposition(unittest.TestCase):
    def test_outbreak_scenario_composes(self):
        from outbreak_day import run_outbreak
        out = run_outbreak()
        self.assertEqual(len(out["log"]), 10)
        self.assertIn("Cases: 2", out["aar"])


class TestConnectedDay(unittest.TestCase):
    def test_ten_seams_compose(self):
        from connected_day import run_connected_day
        log = run_connected_day()  # assertions inside are the test
        self.assertEqual(log["seams_proven"], 32)
        self.assertTrue(log["empty_pool_refused"])
        self.assertEqual(log["metformin_blocked_at"], 25)
        self.assertEqual(log["teleicu_case"], "icu-EVT-1")
        self.assertEqual(log["cryo_ticket"], "P2")
        self.assertTrue(log["capture_gate_refused"])
        self.assertEqual(log["duel_arbitrated"], "pdr")
        self.assertTrue(log["scorecard_peer_review"])
        self.assertTrue(log["contraindicated_went_stat"])
        self.assertEqual(log["timeout_queued"], "timeout-ABX-1-due2")
        self.assertEqual(log["tww_breach"], "DC-2")
        self.assertEqual(log["news2_icu_case"], "icu-news2-p-ward2")
        self.assertTrue(log["hpv_same_visit_treated"])
        self.assertEqual(log["hpv_hiv_recall_months"], 36)
        self.assertEqual(log["anomaly_incomplete_scan"], "US-A1")
        self.assertTrue(log["anomaly_ga_gate_refused"])
        self.assertEqual(log["priors_attached"], 2)
        self.assertEqual(log["callrecall_coverage"], round(2 / 3, 3))
        self.assertEqual(log["callrecall_never_came"], "p-cx-never")
        self.assertEqual(log["histology_loop_closed"], "p-cx3")
        self.assertEqual(log["sepsis_breaches_open"], 5)
        self.assertEqual(log["sepsis_all_or_nothing_met"], 1)
        self.assertEqual(log["bedboard_open_first"], "p-ward3")
        self.assertEqual(log["bedboard_device_days"], 5)
        self.assertTrue(log["acuity_bare_score_refused"])
        self.assertEqual(log["acuity_high_band_n"], 1)
        self.assertEqual(log["dr_series_broken_gaps"], [139])
        self.assertTrue(log["dr_pregnancy_override"])
        self.assertEqual(log["medrec_discrepancy"],
                         "ordered-despite-withhold")
        self.assertTrue(log["terminology_unbound_refused"])
        self.assertEqual(log["terminology_coverage"], round(2 / 3, 3))
        self.assertEqual(log["mammo_audit_ppv"], 1.0)
        self.assertEqual(log["mammo_overdue_first"], "w-m4")
        self.assertEqual(log["offline_synced"], 1)
        self.assertEqual(log["offline_conflict_kept"], "CX-O1")
        self.assertTrue(log["teach_leak_refused"])
        self.assertTrue(log["teach_retract_blocks_use"])
        self.assertEqual(log["teach_contributed"], 1)
        self.assertTrue(log["vocab_converged"])
        self.assertTrue(log["ecg_implausible_refused"])
        self.assertEqual(log["ecg_qtc_method"], "bazett")
        self.assertEqual(log["synoptic_signed"], "SYN-1")
        self.assertEqual(log["biometry_flagged"], "US-B1")
        self.assertTrue(log["semen_single_zero_held"])
        self.assertEqual(log["dictation_engine"], "whisper@large-v3")
        self.assertEqual(log["mdt_orders_placed"], 1)
        self.assertTrue(log["mdt_allergy_refused"])
        self.assertTrue(log["tnm_edition_refused"])
        self.assertEqual(log["tnm_restaged"], "cT3N1AM1B")
        self.assertEqual(log["frozen_tat_minutes"], 24)
        self.assertTrue(log["frozen_readback_refused"])
        self.assertEqual(log["derivative_slides"], 4)
        self.assertTrue(log["reflex_off_menu_refused"])
        self.assertEqual(log["reflex_addended"], "PC-COLON")
        self.assertTrue(log["holter_insufficient_refused"])
        self.assertTrue(log["ecg_machine_edited"])
        self.assertTrue(log["hbv_nonresponder_actioned"])
        self.assertGreaterEqual(log["fert_registry_cycles"], 1)
        self.assertEqual(log["lesion_change_flagged"], "L-9")
        self.assertTrue(log["cervix_device_qa_refused"])
        self.assertTrue(log["pixel_gate_held"])
        self.assertEqual(log["pixel_gate_verified"], "VS-1")
        self.assertTrue(log["family_update_logged"])
        self.assertTrue(log["iv_tpn_screened"])
        self.assertEqual(log["fault_reseated"], "HD-M2")
        self.assertTrue(log["pgt_no_result_held"])
        self.assertTrue(log["pgt_aneuploid_held"])
        self.assertTrue(log["growth_crossing_flagged"])
        self.assertTrue(log["vendor_sdk_never_vendored"])
        self.assertEqual(log["device_feed_ingested"], "MSG-1")
