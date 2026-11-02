import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fertility import (FertilityClinic, FertilityError,
                       DISPOSITION_VALIDITY_DAYS)


class TestFertility(unittest.TestCase):
    def setUp(self):
        self.cl = FertilityClinic("fert-1")
        self.cl.register_couple("CPL", partner_a="pa", partner_b="pb",
                                day=0)
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cycle-treatment",
                                  method="signed", by=p, day=0)

    def _cycle_to_fertilization(self):
        self.cl.start_cycle("CY1", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        for stage in ("stimulation", "triggered", "retrieval",
                      "fertilization"):
            self.cl.advance("CY1", stage, day=2, by="dr-1")

    def test_cycle_requires_both_consents(self):
        self.cl.register_couple("CPL2", partner_a="pc", partner_b="pd",
                                day=0)
        self.cl.grant_consent("CPL2", "cycle-treatment", method="signed",
                              by="pc", day=0)
        with self.assertRaises(FertilityError):
            self.cl.start_cycle("CY2", couple_id="CPL2",
                                protocol_version="antag-v3", day=1)

    def test_consent_never_by_staff(self):
        with self.assertRaises(FertilityError):
            self.cl.grant_consent("CPL", "embryo-transfer",
                                  method="signed", by="nurse-1", day=0)

    def test_stages_sequential(self):
        self.cl.start_cycle("CY1", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        with self.assertRaises(FertilityError):
            self.cl.advance("CY1", "retrieval", day=2, by="dr-1")

    def test_embryo_moves_double_witnessed(self):
        self._cycle_to_fertilization()
        self.cl.fertilize("CY1", method="icsi", n_embryos=3, day=3,
                          embryologist="emb-1")
        with self.assertRaises(FertilityError):
            self.cl.move_embryo("CY1-E1", "frozen", day=3,
                                witness_1="emb-1", witness_2="emb-1")
        with self.assertRaises(FertilityError):  # no cryo consent
            self.cl.move_embryo("CY1-E1", "frozen", day=3,
                                witness_1="emb-1", witness_2="emb-2",
                                tank="T1", canister="C2", position="P3")
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cryopreservation",
                                  method="signed", by=p, day=3)
        with self.assertRaises(FertilityError):  # no location
            self.cl.move_embryo("CY1-E1", "frozen", day=3,
                                witness_1="emb-1", witness_2="emb-2")
        out = self.cl.move_embryo("CY1-E1", "frozen", day=3,
                                  witness_1="emb-1", witness_2="emb-2",
                                  tank="T1", canister="C2", position="P3")
        self.assertEqual(out["stage"], "frozen")
        self.assertEqual(self.cl.cryo_inventory()["frozen_embryos"], 1)

    def test_discard_refused_on_expired_consent(self):
        self._cycle_to_fertilization()
        self.cl.fertilize("CY1", method="ivf", n_embryos=1, day=3,
                          embryologist="emb-1")
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "disposition-discard",
                                  method="signed", by=p, day=0)
        with self.assertRaises(FertilityError):
            self.cl.move_embryo("CY1-E1", "discarded",
                                day=DISPOSITION_VALIDITY_DAYS + 1,
                                witness_1="emb-1", witness_2="emb-2")
        out = self.cl.move_embryo("CY1-E1", "discarded", day=100,
                                  witness_1="emb-1", witness_2="emb-2")
        self.assertEqual(out["stage"], "discarded")

    def test_donor_screening_complete_or_refused(self):
        with self.assertRaises(FertilityError):
            self.cl.register_donor("D1", screening={"hiv": "negative"},
                                   day=0)
        self.cl.register_donor("D1", screening={
            "hiv": "negative", "hbv": "negative", "hcv": "negative",
            "syphilis": "negative"}, day=0)
        self._cycle_to_fertilization()
        with self.assertRaises(FertilityError):  # no donor consent
            self.cl.use_donor("CY1", "D1", day=2)
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "donor-gametes",
                                  method="signed", by=p, day=1)
        self.assertEqual(self.cl.use_donor("CY1", "D1", day=2)["donor"],
                         "D1")

    def test_ohss_escalation(self):
        self.cl.ohss_assess("pa", severity="mild", day=5, by="n-1")
        self.assertEqual(len(self.cl.escalations), 0)
        self.cl.ohss_assess("pa", severity="severe", day=6, by="n-1")
        self.assertEqual(len(self.cl.unacknowledged_ohss(7)), 1)
        self.cl.acknowledge_ohss("pa", day=7, by="dr-1")
        self.assertEqual(self.cl.unacknowledged_ohss(8), [])

    def test_transport_gate(self):
        self._cycle_to_fertilization()
        self.cl.fertilize("CY1", method="ivf", n_embryos=1, day=3,
                          embryologist="emb-1")
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cryopreservation",
                                  method="signed", by=p, day=3)
        self.cl.move_embryo("CY1-E1", "frozen", day=3, witness_1="emb-1",
                            witness_2="emb-2", tank="T1", canister="C1",
                            position="P1")
        with self.assertRaises(FertilityError):
            self.cl.transport_embryo("CY1-E1", destination="accra",
                                     day=4, release_id=None)
        with self.assertRaises(FertilityError):
            self.cl.transport_embryo("CY1-E1", destination="accra",
                                     day=4, release_id="rel-1",
                                     bundle_hash="a", expected_hash="b")
        out = self.cl.transport_embryo("CY1-E1", destination="accra",
                                       day=4, release_id="rel-1",
                                       bundle_hash="a", expected_hash="a")
        self.assertTrue(out["sent"])

    def test_outcome_stats_with_denominators(self):
        self._cycle_to_fertilization()
        self.cl.fertilize("CY1", method="ivf", n_embryos=2, day=3,
                          embryologist="emb-1")
        self.cl.advance("CY1", "culture", day=3, by="emb-1")
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "embryo-transfer",
                                  method="signed", by=p, day=5)
        self.cl.advance("CY1", "transfer", day=5, by="dr-1")
        self.cl.move_embryo("CY1-E1", "transferred", day=5,
                            witness_1="dr-1", witness_2="n-1")
        self.cl.advance("CY1", "outcome-pending", day=6, by="dr-1")
        with self.assertRaises(FertilityError):
            self.cl.close_cycle("CY1", day=30, pregnancy_test=False,
                                clinical_pregnancy=True)
        self.cl.close_cycle("CY1", day=300, pregnancy_test=True,
                            clinical_pregnancy=True, live_birth=True,
                            singleton=True)
        stats = self.cl.outcome_stats()
        self.assertEqual(stats["cycles_started"], 1)
        self.assertEqual(stats["singleton_live_births"], 1)

    def test_refund_package_from_ledger(self):
        self.cl.register_package("CPL", cycles_included=2,
                                 refund_kobo=150000000)
        st = self.cl.refund_status("CPL")
        self.assertEqual(st["refund_due_kobo"], 0)
        self._cycle_to_fertilization()
        self.cl.advance("CY1", "culture", day=3, by="e")
        self.cl.advance("CY1", "transfer", day=5, by="d")
        self.cl.advance("CY1", "outcome-pending", day=6, by="d")
        self.cl.close_cycle("CY1", day=30, pregnancy_test=False)
        st = self.cl.refund_status("CPL")
        self.assertEqual(st["cycles_closed"], 1)
        self.assertEqual(st["refund_due_kobo"], 0)  # only 1 of 2 closed

    def test_sealed_access_logged_and_aggregate_report(self):
        self.cl.view("CPL", by="dr-1", role="physician", day=1)
        self.assertEqual(len(self.cl.access_log), 1)
        rep = self.cl.report()
        self.assertNotIn("partner_a", str(rep))
        self.assertEqual(rep["couples"], 1)

    def test_expiring_dispositions_surfaced(self):
        self._cycle_to_fertilization()
        self.cl.fertilize("CY1", method="ivf", n_embryos=1, day=3,
                          embryologist="emb-1")
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cryopreservation",
                                  method="signed", by=p, day=3)
            self.cl.grant_consent("CPL", "disposition-discard",
                                  method="signed", by=p, day=3)
        self.cl.move_embryo("CY1-E1", "frozen", day=3, witness_1="emb-1",
                            witness_2="emb-2", tank="T1", canister="C1",
                            position="P1")
        exp = self.cl.expiring_dispositions(as_of=3 +
                                            DISPOSITION_VALIDITY_DAYS - 10)
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["frozen_embryos"], 1)


if __name__ == "__main__":
    unittest.main()


class TestStimulationMonitoring(unittest.TestCase):
    def setUp(self):
        self.cl = FertilityClinic("fert-1")
        self.cl.register_couple("CPL", partner_a="pa", partner_b="pb",
                                day=0)
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cycle-treatment",
                                  method="signed", by=p, day=0)
        self.cl.start_cycle("CY1", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        self.cl.advance("CY1", "stimulation", day=1, by="dr-1")

    def _mon(self, day, e2, ge14, total, endo=8, fsh=225):
        return self.cl.record_monitoring(
            "CY1", day=day, e2_pmol_l=e2, follicles_ge_14mm=ge14,
            follicles_total=total, endometrium_mm=endo, fsh_dose_iu=fsh,
            by="son-1")

    def test_monitoring_attaches_only_in_stimulation(self):
        self.cl.start_cycle("CY2", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        with self.assertRaises(FertilityError):
            self.cl.record_monitoring(
                "CY2", day=1, e2_pmol_l=500, follicles_ge_14mm=0,
                follicles_total=5, endometrium_mm=6, fsh_dose_iu=225,
                by="son-1")

    def test_implausible_observations_refused(self):
        with self.assertRaises(FertilityError):      # E2
            self._mon(2, 99999, 0, 5)
        with self.assertRaises(FertilityError):      # ≥14 > total
            self._mon(2, 500, 6, 5)
        with self.assertRaises(FertilityError):      # endometrium
            self._mon(2, 500, 0, 5, endo=40)

    def test_stalled_response_drafts_bounded_increase(self):
        self._mon(2, 400, 0, 6)
        self._mon(4, 430, 0, 6)   # flat E2, no growth
        rec = self.cl.monitoring_review("CY1", day=4)
        self.assertEqual(rec["recommendation"]["action"],
                         "increase-fsh")
        self.assertEqual(rec["recommendation"]["max_step_pct"], 50)
        self.assertTrue(rec["draft"])

    def test_overshoot_drafts_decrease(self):
        self._mon(2, 400, 2, 8)
        self._mon(4, 2600, 6, 12)  # E2 6.5x in 2d
        rec = self.cl.monitoring_review("CY1", day=4)
        self.assertEqual(rec["recommendation"]["action"],
                         "decrease-fsh")

    def test_ohss_flood_drafts_coast_or_cancel(self):
        self._mon(2, 400, 2, 8)
        self._mon(5, 14000, 9, 18)
        self._mon(6, 16000, 21, 28)  # ≥14mm flood
        rec = self.cl.monitoring_review("CY1", day=6)
        self.assertEqual(rec["recommendation"]["action"],
                         "coast-or-cancel")

    def test_trigger_ready(self):
        self._mon(2, 400, 1, 7)
        self._mon(4, 900, 3, 9)
        rec = self.cl.monitoring_review("CY1", day=4)
        self.assertEqual(rec["recommendation"]["action"],
                         "trigger-ready")

    def test_review_without_monitoring_refuses(self):
        with self.assertRaises(FertilityError):
            self.cl.monitoring_review("CY1", day=3)


class TestEmbryologyGrading(unittest.TestCase):
    def setUp(self):
        self.cl = FertilityClinic("fert-1")
        self.cl.register_couple("CPL", partner_a="pa", partner_b="pb",
                                day=0)
        for p in ("pa", "pb"):
            self.cl.grant_consent("CPL", "cycle-treatment",
                                  method="signed", by=p, day=0)
        self.cl.start_cycle("CY1", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        for stage in ("stimulation", "triggered", "retrieval",
                      "fertilization"):
            self.cl.advance("CY1", stage, day=2, by="dr-1")
        self.cl.fertilize("CY1", method="ivf", n_embryos=2, day=3,
                          embryologist="emb-1")

    def test_pn_check_with_grader_identity(self):
        g = self.cl.grade_embryo("CY1-E1", culture_day=1, day=4,
                                 by="emb-1", pn="2PN")
        self.assertEqual(g["pn"], "2PN")
        with self.assertRaises(FertilityError):      # unsigned
            self.cl.grade_embryo("CY1-E2", culture_day=1, day=4,
                                 by="", pn="2PN")
        with self.assertRaises(FertilityError):      # unknown PN
            self.cl.grade_embryo("CY1-E2", culture_day=1, day=4,
                                 by="emb-1", pn="maybe")

    def test_gardner_bounds(self):
        with self.assertRaises(FertilityError):
            self.cl.grade_embryo("CY1-E1", culture_day=5, day=8,
                                 by="emb-1", expansion=7, icm="A",
                                 te="B")
        g = self.cl.grade_embryo("CY1-E1", culture_day=5, day=8,
                                 by="emb-1", expansion=4, icm="A",
                                 te="B")
        self.assertEqual(g["expansion"], 4)

    def test_day3_bounds_and_unknown_day_refused(self):
        with self.assertRaises(FertilityError):
            self.cl.grade_embryo("CY1-E1", culture_day=3, day=6,
                                 by="emb-1", cells=42, fragmentation=1)
        with self.assertRaises(FertilityError):
            self.cl.grade_embryo("CY1-E1", culture_day=2, day=5,
                                 by="emb-1")

    def test_regrade_is_appended_not_edited(self):
        self.cl.grade_embryo("CY1-E1", culture_day=3, day=6, by="emb-1",
                             cells=7, fragmentation=2)
        self.cl.grade_embryo("CY1-E1", culture_day=3, day=6, by="emb-2",
                             cells=8, fragmentation=1)
        self.assertEqual(len(self.cl.grades["CY1-E1"]), 2)

    def test_grade_report_denominators(self):
        self.cl.grade_embryo("CY1-E1", culture_day=1, day=4, by="e",
                             pn="2PN")
        self.cl.grade_embryo("CY1-E1", culture_day=5, day=8, by="e",
                             expansion=4, icm="A", te="B")
        self.cl.grade_embryo("CY1-E2", culture_day=1, day=4, by="e",
                             pn="0PN")
        rep = self.cl.grade_report("CY1")
        self.assertEqual(rep["embryos"], 2)
        self.assertEqual(rep["fertilized_2pn"], 1)
        self.assertEqual(rep["usable"], 1)


class TestSemenAnalysis(unittest.TestCase):
    def setUp(self):
        self.c = FertilityClinic("f1")
        self.c.register_couple("C1", partner_a="pa", partner_b="pb",
                               day=0)

    def _record(self, **kw):
        base = dict(day=1, by="lab-1", abstinence_days=3,
                    volume_ml=2.5, concentration_m_per_ml=40.0,
                    total_motility_pct=55.0,
                    progressive_motility_pct=40.0,
                    normal_forms_pct=6.0)
        base.update(kw)
        return self.c.record_semen_analysis("C1", **base)

    def test_normal_sample_has_no_flags(self):
        out = self._record()
        self.assertEqual(out["below_reference"], {})
        self.assertFalse(out["azoospermia_needs_confirmation"])
        self.assertEqual(out["ref_version"], "who-semen-2021@1.0.0")

    def test_below_reference_is_a_flag_not_a_verdict(self):
        out = self._record(concentration_m_per_ml=10.0,
                           normal_forms_pct=2.0)
        self.assertEqual(set(out["below_reference"]),
                         {"concentration_m_per_ml", "normal_forms_pct"})
        self.assertIn("flag, not a verdict", out["note"])

    def test_single_zero_is_not_a_diagnosis(self):
        out = self._record(concentration_m_per_ml=0.0)
        self.assertTrue(out["azoospermia_needs_confirmation"])
        self.assertFalse(self.c.semen_summary("C1")
                         ["azoospermia_confirmed"])
        self._record(concentration_m_per_ml=0.0, day=30)
        self.assertTrue(self.c.semen_summary("C1")
                        ["azoospermia_confirmed"])

    def test_progressive_cannot_exceed_total(self):
        with self.assertRaises(FertilityError):
            self._record(total_motility_pct=30.0,
                         progressive_motility_pct=40.0)

    def test_implausible_and_bad_metadata_refuse(self):
        with self.assertRaises(FertilityError):
            self._record(abstinence_days=40)
        with self.assertRaises(FertilityError):
            self._record(volume_ml=99.0)

    def test_summary_has_denominators(self):
        self._record()
        self._record(concentration_m_per_ml=5.0, day=30)
        s = self.c.semen_summary("C1")
        self.assertEqual(s["samples"], 2)
        self.assertEqual(s["below_reference"], 1)
        self.assertEqual(s["below_reference_rate"], 0.5)

    def test_absence_is_not_normal(self):
        with self.assertRaises(FertilityError):
            self.c.semen_summary("C1")
