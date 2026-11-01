import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from pacs import (DICOMwebAdapter, PACSError, StudyRegistry,
                  DOSE_ALERT_MGY, DOSE_CUMULATIVE_ALERT_MGY)


class TestAdapter(unittest.TestCase):
    def test_honest_spike_refuses_without_config(self):
        a = DICOMwebAdapter()
        with self.assertRaises(PACSError):
            a.qido_studies({})
        with self.assertRaises(PACSError):
            a.stow([])

    def test_calls_reach_configured_base(self):
        seen = []
        a = DICOMwebAdapter(base_url="http://hub:8080/dcm4chee-arc/"
                                     "aets/DCM4CHEE/rs",
                            fetch=lambda url, body: seen.append(url) or {})
        a.qido_studies({"PatientID": "x"})
        self.assertIn("qido-rs/studies", seen[0])

    def test_oidc_refuses_without_config(self):
        a = DICOMwebAdapter(base_url="http://hub/rs", fetch=lambda u, b: {})
        with self.assertRaises(PACSError):
            a.access_token()

    def test_oidc_client_credentials(self):
        a = DICOMwebAdapter(
            base_url="http://hub/rs",
            oidc_token_url="http://kc/realms/dcm4che/protocol"
                           "/openid-connect/token",
            oidc_client_id="openhealth", oidc_client_secret="s3cret",
            fetch=lambda url, body: {"access_token": "tok-1"})
        self.assertEqual(a.access_token(), "tok-1")
        self.assertEqual(a.token, "tok-1")

    def test_oidc_empty_token_fails_loud(self):
        a = DICOMwebAdapter(oidc_token_url="http://kc/t",
                            oidc_client_id="c", oidc_client_secret="s",
                            fetch=lambda url, body: {})
        with self.assertRaises(PACSError):
            a.access_token()


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.r = StudyRegistry()
        self.r.register_study(study_uid="S1", accession="A1",
                              patient_id="p1", modality="CT",
                              exam_protocol="head-ct-v2",
                              priority="stat", facility="phc-1", day=0)

    def test_dedupe_uid_and_accession(self):
        with self.assertRaises(PACSError):
            self.r.register_study(study_uid="S1", accession="A2",
                                  patient_id="p2", modality="DX",
                                  exam_protocol="cxr", priority="routine",
                                  facility="phc-1", day=1)
        with self.assertRaises(PACSError):
            self.r.register_study(study_uid="S2", accession="A1",
                                  patient_id="p2", modality="DX",
                                  exam_protocol="cxr", priority="routine",
                                  facility="phc-1", day=1)

    def test_fail_closed_enums(self):
        with self.assertRaises(PACSError):
            self.r.register_study(study_uid="S3", accession="A3",
                                  patient_id="p", modality="MRI",
                                  exam_protocol="x", priority="routine",
                                  facility="f", day=0)
        with self.assertRaises(PACSError):
            self.r.register_study(study_uid="S3", accession="A3",
                                  patient_id="p", modality="DX",
                                  exam_protocol="", priority="routine",
                                  facility="f", day=0)

    def test_spool_sync_failures_stay_queued(self):
        self.r.spool_upload("S1", b"blob", day=1)
        out = self.r.sync_spool(lambda e: (_ for _ in ()).throw(IOError()))
        self.assertEqual(out["still_queued"], 1)
        out = self.r.sync_spool(lambda e: True)
        self.assertEqual(out["synced"], 1)
        self.assertEqual(self.r.studies["S1"]["state"], "acquired")

    def test_export_refuses_burned_in(self):
        self.r.register_study(study_uid="S2", accession="A2",
                              patient_id="p2", modality="CR",
                              exam_protocol="cxr", priority="routine",
                              facility="phc-1", day=0,
                              burned_in_annotations=True)
        with self.assertRaises(PACSError):
            self.r.export_metadata("S2", purpose="research")
        out = self.r.export_metadata("S1", purpose="teleradiology")
        self.assertNotIn("patient_id", out)
        self.assertTrue(out["deidentified"])

    def test_dose_alerts(self):
        over = self.r.record_dose("S1", DOSE_ALERT_MGY["ct"] + 1, day=1)
        self.assertTrue(any("single-study" in a for a in over["alerts"]))
        self.r.register_study(study_uid="S4", accession="A4",
                              patient_id="p1", modality="CT",
                              exam_protocol="abd-ct-v1", priority="urgent",
                              facility="phc-1", day=2)
        big = DOSE_CUMULATIVE_ALERT_MGY
        cum = self.r.record_dose("S4", big, day=2)
        self.assertTrue(any("cumulative" in a for a in cum["alerts"]))
        with self.assertRaises(PACSError):
            self.r.record_dose("S1", 0, day=3)

    def test_qc_repeat_rate_and_tier_honesty(self):
        with self.assertRaises(PACSError):
            self.r.record_qc_reject("S1", reason="", day=1)
        self.r.record_qc_reject("S1", reason="motion", day=1, retaken=True)
        rr = self.r.repeat_rate()
        self.assertEqual((rr["studies"], rr["rejects"]), (1, 1))
        out = self.r.set_tier("S1", "nearline")
        self.assertEqual(out["expected_retrieval"], "hours")
        with self.assertRaises(PACSError):
            self.r.set_tier("S1", "frozen")

    def test_ai_draft_governance(self):
        with self.assertRaises(PACSError):
            self.r.submit_ai_result("S1", model_id="cxr-ai",
                                    model_version="", findings={})
        e = self.r.submit_ai_result("S1", model_id="cxr-ai",
                                    model_version="1.2.0", findings={})
        self.assertEqual(e["status"], "draft-awaiting-radiologist")
        out = self.r.confirm_ai_result("S1", radiologist="rad-1",
                                       agree=False, note="overcalled")
        self.assertEqual(out["status"], "overruled")
        with self.assertRaises(PACSError):
            self.r.confirm_ai_result("S1", radiologist="rad-1", agree=True)

    def test_orphans_and_gaps_both_directions(self):
        out = self.r.orphans_and_gaps(["S1", "GHOST"])
        self.assertEqual(out["orphans_in_appliance"], ["GHOST"])
        self.assertEqual(out["missing_from_appliance"], [])
        out = self.r.orphans_and_gaps([])
        self.assertEqual(out["missing_from_appliance"], ["S1"])

    def test_integrity_chain_advances(self):
        h1 = self.r.seal_instances("S1", ["aaa"])["chain_head"]
        h2 = self.r.seal_instances("S1", ["bbb"])["chain_head"]
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(h1, "0" * 64)

    # ---- hub-archive (dcm4chee) pull-sync ----
    @staticmethod
    def _qido(uid, modality="CT", accession="ACC-9", patient="EXT-1"):
        rec = {"0020000D": {"vr": "UI", "Value": [uid]}}
        if modality is not None:
            rec["00080061"] = {"vr": "CS", "Value": [modality]}
        if accession is not None:
            rec["00080050"] = {"vr": "SH", "Value": [accession]}
        if patient is not None:
            rec["00100020"] = {"vr": "LO", "Value": [patient]}
        return rec

    def test_sync_from_archive_registers_unknown(self):
        out = self.r.sync_from_archive(
            [self._qido("S1"), self._qido("S-NEW")], day=5,
            facility="hub")
        self.assertEqual((out["registered"], out["already_known"]), (1, 1))
        self.assertEqual(out["malformed"], [])
        # pre-existing S1 untouched: our governed state survives sync
        self.assertEqual(self.r.studies["S1"]["facility"], "phc-1")
        self.assertEqual(self.r.studies["S-NEW"]["state"], "acquired")
        self.assertEqual(out["reconciliation"]["orphans_in_appliance"],
                         [])

    def test_sync_from_archive_garbage_is_finding_not_crash(self):
        out = self.r.sync_from_archive(
            [{"00080020": {"vr": "DA", "Value": ["20260101"]}},  # no UID
             self._qido("S-BAD", modality="OT")],                # bad modality
            day=5, facility="hub")
        self.assertEqual(out["registered"], 0)
        self.assertEqual(len(out["malformed"]), 2)
        self.assertEqual(out["malformed"][0]["reason"],
                         "no StudyInstanceUID")
        self.assertIn("unknown modality", out["malformed"][1]["reason"])

    def test_sync_from_archive_reconciliation_flags_gaps(self):
        # registry knows S1, archive does not → missing_from_appliance
        out = self.r.sync_from_archive([self._qido("S-ORPHAN")], day=5,
                                       facility="hub")
        recon = out["reconciliation"]
        self.assertEqual(recon["missing_from_appliance"], ["S1"])
        self.assertEqual(recon["orphans_in_appliance"], [])


if __name__ == "__main__":
    unittest.main()
