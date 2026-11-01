import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from aigov import AIGovError, ModelRegistry


class TestAIGov(unittest.TestCase):
    def setUp(self):
        self.reg = ModelRegistry()
        self.reg.register("cxr-ai", version="1.2.0", task="cxr-triage",
                          licence_class="embed-clean",
                          evidence_ref="doi:10.x/cxr")

    def test_register_fail_closed(self):
        with self.assertRaises(AIGovError):
            self.reg.register("x", version="", task="t",
                              licence_class="embed-clean",
                              evidence_ref="r")
        with self.assertRaises(AIGovError):
            self.reg.register("x", version="1", task="t",
                              licence_class="whatever",
                              evidence_ref="r")
        with self.assertRaises(AIGovError):
            self.reg.register("x", version="1", task="t",
                              licence_class="embed-clean",
                              evidence_ref="")

    def test_unregistered_refused(self):
        with self.assertRaises(AIGovError):
            self.reg.submit_draft("C1", model_id="ghost", version="9",
                                  output={})

    def test_research_only_shadow_mode(self):
        self.reg.register("mms-cls", version="0.1", task="audio",
                          licence_class="research-only",
                          evidence_ref="paper")
        with self.assertRaises(AIGovError):
            self.reg.submit_draft("C2", model_id="mms-cls", version="0.1",
                                  output={})
        d = self.reg.submit_draft("C2", model_id="mms-cls", version="0.1",
                                  output={}, show_to_clinician=False)
        self.assertTrue(d["shadow"])

    def test_outcome_and_rates(self):
        self.reg.submit_draft("C1", model_id="cxr-ai", version="1.2.0",
                              output={})
        self.reg.record_outcome("C1", reviewer="r1", agree=False,
                                note="overcalled")
        with self.assertRaises(AIGovError):
            self.reg.record_outcome("C1", reviewer="r1", agree=True)
        self.reg.submit_draft("C2", model_id="cxr-ai", version="1.2.0",
                              output={})
        self.reg.record_outcome("C2", reviewer="r1", agree=True)
        rates = self.reg.agreement_rates()
        self.assertEqual(rates["cxr-ai@1.2.0"]["n"], 2)
        self.assertEqual(rates["cxr-ai@1.2.0"]["agreement"], 0.5)

    def test_suspension(self):
        with self.assertRaises(AIGovError):
            self.reg.suspend("cxr-ai", "1.2.0", reason="", by="cmo")
        self.reg.suspend("cxr-ai", "1.2.0", reason="agreement collapsed",
                         by="cmo")
        with self.assertRaises(AIGovError):
            self.reg.submit_draft("C9", model_id="cxr-ai", version="1.2.0",
                                  output={})


if __name__ == "__main__":
    unittest.main()
