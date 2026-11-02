import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("dictate", "teleread", "aigov"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from dictate import DictationService, DictateError
from teleread import DomainConfig, ReaderRegistry, ReadingService, TeleReadError
from aigov import ModelRegistry


def make_svc():
    cfg = DomainConfig("radiology", scopes=("chest",),
                       sla_hours={"routine": 48})
    readers = ReaderRegistry()
    readers.register("rad-1", licence_id="L1", licence_expiry_day=10**9,
                     scopes=("chest",))
    svc = ReadingService(cfg, readers)
    svc.order("CX-1", priority="routine", question="TB?",
              facility="phc-1", referrer="dr-a", day=1)
    svc.assign("CX-1", day=1, prefer="rad-1")
    svc.order("CX-2", priority="routine", question="nodule?",
              facility="phc-1", referrer="dr-a", day=1)
    return svc


class TestTranscription(unittest.TestCase):
    def test_transcript_lands_as_draft_never_verified(self):
        svc = make_svc()
        d = DictationService(svc)
        out = d.dictate("CX-1", reader_id="rad-1",
                        transcript="Right upper lobe cavitation. "
                                   "Impression: active TB.",
                        engine="whisper", engine_version="large-v3",
                        day=1, template_version="cxr@1.0.0")
        self.assertEqual(out["state"], "draft")
        self.assertEqual(svc.cases["CX-1"]["state"], "reported-draft")
        # the seam cannot verify — that stays a human act
        svc.verify_report("CX-1", reader_id="rad-1", day=1)
        self.assertEqual(svc.cases["CX-1"]["state"],
                         "reported-verified")

    def test_provenance_is_mandatory(self):
        svc = make_svc()
        d = DictationService(svc)
        for kw in ({"engine": "", "engine_version": "v3"},
                   {"engine": "whisper", "engine_version": ""}):
            with self.assertRaises(DictateError):
                d.dictate("CX-1", reader_id="rad-1",
                          transcript="words", day=1,
                          template_version="cxr@1.0.0", **kw)

    def test_empty_transcript_refused(self):
        svc = make_svc()
        d = DictationService(svc)
        with self.assertRaises(DictateError):
            d.dictate("CX-1", reader_id="rad-1", transcript="   ",
                      engine="whisper", engine_version="v3", day=1,
                      template_version="cxr@1.0.0")

    def test_only_assigned_reader_dictates(self):
        svc = make_svc()
        d = DictationService(svc)
        with self.assertRaises(TeleReadError):
            # CX-2 is unassigned; the engine's own gate holds
            d.dictate("CX-2", reader_id="rad-1", transcript="words",
                      engine="whisper", engine_version="v3", day=1,
                      template_version="cxr@1.0.0")

    def test_transcript_stored_verbatim(self):
        svc = make_svc()
        d = DictationService(svc)
        d.dictate("CX-1", reader_id="rad-1", transcript="exact words",
                  engine="whisper", engine_version="v3", day=1,
                  template_version="cxr@1.0.0")
        self.assertEqual(d.sessions[0]["transcript"], "exact words")


class TestGenerativeGate(unittest.TestCase):
    def test_unregistered_generative_engine_refused(self):
        svc = make_svc()
        d = DictationService(svc, ModelRegistry())
        with self.assertRaises(DictateError):
            d.dictate("CX-1", reader_id="rad-1",
                      transcript="encounter audio text",
                      engine="ambient-ai", engine_version="1.0",
                      engine_kind="ambient-generative", day=1,
                      findings="f", impression="i",
                      template_version="cxr@1.0.0")

    def test_generative_without_registry_at_all_refused(self):
        svc = make_svc()
        d = DictationService(svc)  # no aigov registry wired
        with self.assertRaises(DictateError):
            d.dictate("CX-1", reader_id="rad-1", transcript="t",
                      engine="ambient-ai", engine_version="1.0",
                      engine_kind="ambient-generative", day=1,
                      findings="f", impression="i",
                      template_version="cxr@1.0.0")

    def test_registered_generative_engine_drafts_with_composition(self):
        svc = make_svc()
        reg = ModelRegistry()
        reg.register("ambient-ai", version="1.0", task="report-draft",
                     licence_class="embed-clean",
                     evidence_ref="validation-report-2026")
        d = DictationService(svc, reg)
        out = d.dictate("CX-1", reader_id="rad-1",
                        transcript="raw encounter words...",
                        engine="ambient-ai", engine_version="1.0",
                        engine_kind="ambient-generative", day=1,
                        findings="composed findings",
                        impression="composed impression",
                        template_version="cxr@1.0.0")
        self.assertEqual(out["state"], "draft")
        rep = svc.reports["CX-1"]
        self.assertEqual(rep["findings"], "composed findings")
        # the encounter transcript stays verbatim on the session
        self.assertEqual(d.sessions[0]["transcript"],
                         "raw encounter words...")

    def test_suspended_engine_stays_stopped(self):
        svc = make_svc()
        reg = ModelRegistry()
        reg.register("ambient-ai", version="1.0", task="report-draft",
                     licence_class="embed-clean",
                     evidence_ref="validation-report-2026")
        reg.suspend("ambient-ai", "1.0", reason="drift detected",
                    by="cmo")
        d = DictationService(svc, reg)
        with self.assertRaises(DictateError):
            d.dictate("CX-1", reader_id="rad-1", transcript="t",
                      engine="ambient-ai", engine_version="1.0",
                      engine_kind="ambient-generative", day=1,
                      findings="f", impression="i",
                      template_version="cxr@1.0.0")

    def test_provenance_report_with_denominators(self):
        svc = make_svc()
        reg = ModelRegistry()
        reg.register("ambient-ai", version="1.0", task="report-draft",
                     licence_class="embed-clean",
                     evidence_ref="ev-1")
        d = DictationService(svc, reg)
        svc.assign("CX-2", day=1, prefer="rad-1")
        d.dictate("CX-1", reader_id="rad-1", transcript="t1",
                  engine="whisper", engine_version="v3", day=1,
                  template_version="cxr@1.0.0")
        d.dictate("CX-2", reader_id="rad-1", transcript="t2",
                  engine="ambient-ai", engine_version="1.0",
                  engine_kind="ambient-generative", day=1,
                  findings="f", impression="i",
                  template_version="cxr@1.0.0")
        rep = d.provenance_report()
        self.assertEqual(rep["dictated_drafts"], 2)
        self.assertEqual(rep["generative_share"], 0.5)


if __name__ == "__main__":
    unittest.main()
