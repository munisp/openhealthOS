"""Tests for datasetforge.py and crosslink seam 20."""
import os
import sys
import unittest

PKG = os.path.join(os.path.dirname(__file__), "..")
for p in ("datasetforge", "crosslink", "pacs", "drugkb"):
    sys.path.insert(0, os.path.join(PKG, p))

import crosslink  # noqa: E402
from datasetforge import DatasetForge, ForgeError  # noqa: E402
from pacs import PACSError, StudyRegistry  # noqa: E402

SALT = "deployment-secret-salt"


def _forge_with_cases(n=10):
    forge = DatasetForge()
    forge.register_task("cervix-v1",
                        label_schema=("normal", "suspicious",
                                      "cancer"),
                        version="cervix-labels@1.0.0")
    labels = ["normal", "suspicious", "cancer"]
    for i in range(n):
        forge.register_case(
            "cervix-v1", case_ref=f"case-{i}", source_ref=f"study-{i}",
            patient_key=DatasetForge.patient_key_from(f"pat-{i}",
                                                      salt=SALT),
            label=labels[i % 3], labeled_by="path-1", day=1,
            deid_policy_version="deid@1.0.0")
    return forge


class TestTasksAndCases(unittest.TestCase):
    def test_label_outside_schema_refuses(self):
        forge = DatasetForge()
        forge.register_task("t", label_schema=("a",), version="v1")
        with self.assertRaises(ForgeError) as cm:
            forge.register_case("t", case_ref="c1", source_ref="s1",
                                patient_key="k1", label="b",
                                labeled_by="x", day=1,
                                deid_policy_version="d")
        self.assertIn("unbound", str(cm.exception))

    def test_unlabeled_by_refuses(self):
        forge = _forge_with_cases(0)
        with self.assertRaises(ForgeError) as cm:
            forge.register_case("cervix-v1", case_ref="c1",
                                source_ref="s1", patient_key="k1",
                                label="normal", labeled_by="", day=1,
                                deid_policy_version="d")
        self.assertIn("guess", str(cm.exception))

    def test_orphan_case_refuses(self):
        forge = _forge_with_cases(0)
        with self.assertRaises(ForgeError):
            forge.register_case("cervix-v1", case_ref="c1",
                                source_ref="", patient_key="k1",
                                label="normal", labeled_by="x", day=1,
                                deid_policy_version="d")

    def test_patient_key_needs_salt(self):
        with self.assertRaises(ForgeError) as cm:
            DatasetForge.patient_key_from("MRN-1", salt="")
        self.assertIn("lookup table", str(cm.exception))
        k = DatasetForge.patient_key_from("MRN-1", salt=SALT)
        self.assertNotIn("MRN-1", k)


class TestSplits(unittest.TestCase):
    def test_patient_level_no_leakage(self):
        forge = _forge_with_cases(10)
        # two cases from the SAME patient must land in the same split
        key = DatasetForge.patient_key_from("pat-0", salt=SALT)
        forge.register_case("cervix-v1", case_ref="case-dup",
                            source_ref="study-dup", patient_key=key,
                            label="normal", labeled_by="path-1", day=1,
                            deid_policy_version="deid@1.0.0")
        out = forge.split("cervix-v1", seed=42)
        homes = [name for name in ("train", "val", "test")
                 if "case-0" in out[name]]
        dup_homes = [name for name in ("train", "val", "test")
                     if "case-dup" in out[name]]
        self.assertEqual(homes, dup_homes)

    def test_reproducible_by_seed(self):
        forge = _forge_with_cases(10)
        self.assertEqual(forge.split("cervix-v1", seed=7),
                         forge.split("cervix-v1", seed=7))

    def test_empty_task_split_refuses(self):
        forge = DatasetForge()
        forge.register_task("t", label_schema=("a",), version="v1")
        with self.assertRaises(ForgeError):
            forge.split("t")


class TestConvertAndVerify(unittest.TestCase):
    def setUp(self):
        self.forge = _forge_with_cases(6)

    def test_no_converter_refuses(self):
        manifest = self.forge.export_manifest("cervix-v1")
        with self.assertRaises(ForgeError) as cm:
            self.forge.convert(manifest, converter=None, day=2)
        self.assertIn("service-boundary job", str(cm.exception))

    def test_convert_and_verify_flow(self):
        manifest = self.forge.export_manifest("cervix-v1",
                                              split_name="train")
        ds = self.forge.convert(
            manifest, day=2,
            converter=lambda m: {"dataset_ref": "s3://ds/cervix-train",
                                 "container_sha256": "abc"})
        self.assertIsNone(ds["verified"])  # absence is not verification
        out = self.forge.verify(
            ds["dataset_id"],
            verifier=lambda ref: (True, "all 640 blocks CRC-ok"))
        self.assertTrue(out["verified"])

    def test_failed_verification_quarantines_loudly(self):
        manifest = self.forge.export_manifest("cervix-v1")
        ds = self.forge.convert(manifest, day=2,
                                converter=lambda m:
                                {"dataset_ref": "s3://ds/x"})
        with self.assertRaises(ForgeError) as cm:
            self.forge.verify(ds["dataset_id"],
                              verifier=lambda ref: (False, "block 12 "
                                                    "CRC mismatch"))
        self.assertIn("quarantined", str(cm.exception))

    def test_report_has_denominators(self):
        self.test_convert_and_verify_flow()
        rep = self.forge.report("cervix-v1")
        self.assertEqual(rep["verified"], 1)
        self.assertEqual(rep["cases"], 6)
        self.assertEqual(rep["patients"], 6)


class TestSeam20(unittest.TestCase):
    def _pacs(self, burned_in=False):
        reg = StudyRegistry()
        reg.register_study(study_uid="1.2.3", accession="ACC-1",
                           patient_id="pat-1", modality="MG",
                           exam_protocol="mammo-screening",
                           priority="routine", facility="phc-1", day=1,
                           burned_in_annotations=burned_in)
        return reg

    def test_deid_gate_feeds_forge(self):
        forge = DatasetForge()
        forge.register_task("mammo-v1", label_schema=("birads-1",
                                                      "birads-4"),
                            version="mammo-labels@1.0.0")
        case = crosslink.deid_export_to_dataset(
            self._pacs(), forge, "1.2.3", task_id="mammo-v1",
            label="birads-4", labeled_by="rad-1", day=2, salt=SALT,
            deid_policy_version="deid@1.0.0")
        self.assertEqual(case["source_ref"], "1.2.3")
        # the forge never saw the patient id — only the salted key
        self.assertNotIn("pat-1", str(forge.cases))

    def test_burned_in_annotations_refuse_before_the_forge(self):
        forge = DatasetForge()
        forge.register_task("mammo-v1", label_schema=("birads-1",),
                            version="v1")
        with self.assertRaises(PACSError):
            crosslink.deid_export_to_dataset(
                self._pacs(burned_in=True), forge, "1.2.3",
                task_id="mammo-v1", label="birads-1",
                labeled_by="rad-1", day=2, salt=SALT,
                deid_policy_version="deid@1.0.0")
        self.assertEqual(len(forge.cases), 0)


if __name__ == "__main__":
    unittest.main()
