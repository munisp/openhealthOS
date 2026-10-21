"""Tests for the formerly-deferred ADR-0066 work: live-verify tooling and
the Nigerian-language NER fine-tuning pipeline."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ner"))

import live_verify  # noqa: E402
import ngpatterns  # noqa: E402
from corpus import SEED_CORPUS, LANGS, validate  # noqa: E402
from prepare import bio_encode, split, tokenize  # noqa: E402
from evaluate import prf1  # noqa: E402


class TestLiveVerify(unittest.TestCase):
    def _tmp_manifest(self):
        d = tempfile.mkdtemp()
        dst = os.path.join(d, "models-manifest.json")
        shutil.copy(os.path.join(HERE, "models-manifest.json"), dst)
        return d, dst

    def test_pin_manifest_rewrites_with_real_digests(self):
        d, path = self._tmp_manifest()
        fake_digest = "a" * 64

        def fake_fetcher(repo):
            return {"model.safetensors": fake_digest}, "rev123"

        changed = live_verify.pin_manifest(path, fetcher=fake_fetcher,
                                           now="2026-10-22T00:00:00+00:00")
        self.assertEqual(len(changed), 1)
        m = json.load(open(path))
        e = m["models"][0]
        self.assertEqual(e["sha256"], fake_digest)
        self.assertEqual(e["weight_file"], "model.safetensors")
        self.assertEqual(e["hf_revision"], "rev123")
        self.assertEqual(e["verified_at"], "2026-10-22T00:00:00+00:00")
        # and the REAL gate must now accept the entry
        from model_gate import vet
        vet(path)  # must not raise
        shutil.rmtree(d)

    def test_pin_manifest_fails_closed_without_network(self):
        d, path = self._tmp_manifest()
        before = open(path).read()

        def dead_fetcher(repo):
            raise live_verify.LiveVerifyError("cannot reach HuggingFace API")

        with self.assertRaises(live_verify.LiveVerifyError):
            live_verify.pin_manifest(path, fetcher=dead_fetcher)
        self.assertEqual(open(path).read(), before)  # untouched
        shutil.rmtree(d)

    def test_fetch_rejects_non_hex_or_short_oids(self):
        d, path = self._tmp_manifest()

        def bad_fetcher(repo):
            return {"model.safetensors": "zzz-not-hex"}, "rev"
        with self.assertRaises(Exception):
            live_verify.pin_manifest(path, fetcher=bad_fetcher)
        shutil.rmtree(d)


class TestCorpus(unittest.TestCase):
    def test_corpus_valid_and_multilingual(self):
        self.assertTrue(validate())
        self.assertEqual(set(LANGS), {"en", "ha", "yo", "ig"})
        self.assertGreaterEqual(len(SEED_CORPUS), 8)

    def test_spans_slice_real_identifiers(self):
        for r in SEED_CORPUS:
            for s in r["spans"]:
                frag = r["text"][s["start"]:s["end"]]
                if s["label"] in ("NIN", "PHONE", "NHIA_ID", "FACILITY"):
                    self.assertTrue(any(c.isdigit() for c in frag),
                                    "%s span text %r" % (r["id"], frag))


class TestPrepare(unittest.TestCase):
    def test_bio_round_trip(self):
        r = SEED_CORPUS[0]
        enc = bio_encode(r)
        b_tags = [t for t in enc["tags"] if t.startswith("B-")]
        self.assertEqual(len(b_tags), len(r["spans"]))
        self.assertEqual(len(enc["tokens"]), len(enc["tags"]))

    def test_tokenize_preserves_offsets(self):
        toks = tokenize("NIN 12345678901, thanks")
        self.assertEqual(toks[1], ("12345678901", 4, 15))

    def test_split_deterministic_and_total(self):
        t1, d1 = split(SEED_CORPUS)
        t2, d2 = split(SEED_CORPUS)
        self.assertEqual([r["id"] for r in t1], [r["id"] for r in t2])
        self.assertEqual(len(t1) + len(d1), len(SEED_CORPUS))


class TestEvaluateBaseline(unittest.TestCase):
    def test_pattern_pack_baseline_is_honest(self):
        """The deterministic pack is the pre-training baseline on the seed
        corpus: it must nail format-invariant identifiers and honestly
        miss PERSON (regex cannot recognise names)."""
        def detect(text):
            return [{"start": s["start"], "end": s["end"], "label":
                     {"nin": "NIN", "phone_ng": "PHONE",
                      "nhia_enrollee_id": "NHIA_ID",
                      "facility_id": "FACILITY",
                      "email": "EMAIL"}[s["label"]]}
                    for s in ngpatterns.detect(text)]
        res = prf1(SEED_CORPUS, detect)
        micro = res["micro"]
        # identifiers are caught -> recall well above zero...
        self.assertGreater(micro["recall"], 0.5)
        # ...but PERSON tokens are honestly missed
        self.assertGreater(micro["fn"], 0)
        # precision high: the pack rarely fires on non-entities here
        self.assertGreater(micro["precision"], 0.9)


class TestTrainGuard(unittest.TestCase):
    def test_refuses_seed_without_bringup_flag(self):
        import train
        self.assertEqual(train.main(["--base-model", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
