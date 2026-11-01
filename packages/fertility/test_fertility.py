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
