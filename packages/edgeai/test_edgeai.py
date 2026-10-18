#!/usr/bin/env python3
"""EdgeAI rails: drafts never write, red flags fire, ML seam is gated."""
import unittest

from edgeai import (EdgeAIDrafter, ModelManifest, ModelRegistry,
                    RULES_MANIFEST)


class TestDrafts(unittest.TestCase):
    def setUp(self):
        self.drafter = EdgeAIDrafter()

    def test_red_flag_draft_is_urgent_and_never_final(self):
        d = self.drafter.draft("PT-1", {"cough", "chest_indrawing", "fast_breathing"},
                               {"spo2": 89, "resp_rate": 52})
        self.assertEqual(d.status, "DRAFT")
        self.assertTrue(d.approval_required)
        self.assertEqual(d.assessment["priority"], "P1")
        self.assertEqual(d.assessment["disposition"], "REFER-URGENT")
        self.assertTrue(any("SpO2" in r for r in d.rationale))

    def test_obstetric_context_feeds_rules(self):
        d = self.drafter.draft("PT-2", {"vaginal_bleeding"}, {}, {"pregnant": True})
        self.assertEqual(d.assessment["priority"], "P0")

    def test_no_flags_is_routine(self):
        d = self.drafter.draft("PT-3", {"mild_headache"}, {"spo2": 98, "temp_c": 36.8})
        self.assertEqual(d.assessment["priority"], "P4")
        self.assertEqual(d.assessment["disposition"], "ROUTINE")

    def test_proposal_content_carries_provenance(self):
        d = self.drafter.draft("PT-4", {"fever"}, {"temp_c": 39.5})
        content = d.to_proposal_content()
        self.assertEqual(content["ai_draft"]["model"]["id"], RULES_MANIFEST.model_id)
        self.assertTrue(content["ai_draft"]["inputs_digest"])


class TestManifestSeam(unittest.TestCase):
    def test_ml_manifest_registers_but_is_not_executable(self):
        reg = ModelRegistry()
        reg.register(ModelManifest("pneumonia-cnn", "0.3.0", "tflite",
                                   entrypoint="/models/pneumonia.tflite", sha256="ab" * 32))
        self.assertFalse(reg.executable("pneumonia-cnn"))
        with self.assertRaises(ValueError):
            EdgeAIDrafter(reg, "pneumonia-cnn")

    def test_ml_manifest_requires_artifact_digest(self):
        reg = ModelRegistry()
        with self.assertRaises(ValueError):
            reg.register(ModelManifest("bad-ml", "0.1", "onnx"))

    def test_unregistered_model_refused(self):
        with self.assertRaises(KeyError):
            EdgeAIDrafter(ModelRegistry(), "ghost-model")


if __name__ == "__main__":
    unittest.main()
