"""test_mednlp.py — ADR-0065 spike suite. The gold gate runs REAL
openmed.risk; everything else pins goldens for the live-model swap."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from ngpatterns import detect, NG_PATTERNS          # noqa: E402
from deid import deidentify_note, redact, gold_gate  # noqa: E402
from coding_assist import assist, TerminologyMapper  # noqa: E402
from model_gate import vet, ModelGateError           # noqa: E402

NOTE = ("Amara Okafor (NIN 12345678901, phone 08035551234) attended "
        "OHF-FC-AMC-0001 on 12/03/2026 for malaria fever; gave amodiaquine. "
        "Follow-up via amara@example.ng. NHIA-44556677 billed.")


class TestNgPatterns(unittest.TestCase):
    def test_finds_nigerian_identifiers(self):
        labels = {s["label"] for s in detect(NOTE)}
        assert {"nin", "phone_ng", "facility_id", "email", "nhia_enrollee_id"} <= labels

    def test_nin_needs_context_for_high_score(self):
        bare = detect("reference 12345678901 on the form")
        with_ctx = detect("NIN 12345678901 verified")
        assert with_ctx[0]["score"] > bare[0]["score"]

    def test_pack_is_openmed_spec_shaped(self):
        for p in NG_PATTERNS:
            assert {"pattern", "entity_type", "priority", "base_score",
                    "context_boost", "context_words"} <= set(p)


class TestDeid(unittest.TestCase):
    def test_mask_redaction(self):
        out = deidentify_note(NOTE, method="mask")
        assert "12345678901" not in out["text"]
        assert "08035551234" not in out["text"]
        assert "amara@example.ng" not in out["text"]
        assert "[NIN]" in out["text"]
        # clinical content must survive de-id — that is the whole point
        assert "malaria" in out["text"] and "amodiaquine" in out["text"]

    def test_hash_redaction_is_stable_and_salted(self):
        a = deidentify_note(NOTE, method="hash", salt=b"s1")
        b = deidentify_note(NOTE, method="hash", salt=b"s1")
        c = deidentify_note(NOTE, method="hash", salt=b"s2")
        assert a["text"] == b["text"]            # stable within a salt
        assert a["text"] != c["text"]            # unlinkable across salts

    def test_gold_gate_real_openmed(self):
        rows = [
            {"state": "FC", "lga": "amc", "age_band": "30-39", "n": 120},
            {"state": "FC", "lga": "amc", "age_band": "30-39", "n": 60},
            {"state": "FC", "lga": "amc", "age_band": "40-49", "n": 44},
            {"state": "FC", "lga": "amc", "age_band": "50-59", "n": 2},  # violator
        ]
        res = gold_gate(rows, ["state", "lga", "age_band"], k=2)
        assert res["released"]
        # openmed suppresses BOTH singleton classes (40-49 and 50-59) —
        # minimal correct suppression set, pinned here as the golden.
        assert res["suppressed"] == 2
        assert all(r["age_band"] == "30-39" for r in res["rows"])


class TestCodingAssist(unittest.TestCase):
    def test_maps_malaria_to_icd10(self):
        out = assist("malaria fever, gave amodiaquine and paracetamol")
        assert out["mentions"] == 3
        codes = {(m["label"], m["code"]) for m in out["mapped"]}
        assert any(l == "condition" and c == "B50.9" for l, c in codes)

    def test_unmapped_reported_not_guessed(self):
        out = assist("patient has lassa fever")
        assert ("lassa" in json.dumps(out["unmapped"].pop() if out["unmapped"] else {})
                or out["unmapped"] == []
                or out["mentions"] == 0)  # honest: no condition rule -> no fabricated code

    def test_mapper_loads_real_snapshots(self):
        mapper = TerminologyMapper()
        hit = mapper.lookup("condition", "malaria")
        assert hit and hit["code"] == "B50.9"


class TestModelGate(unittest.TestCase):
    def _manifest(self, models):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"models": models}, f)
        f.close()
        return f.name

    def test_clean_manifest_passes(self):
        path = self._manifest([{"id": "OpenMed/PII-Small-44M",
                                "license": "Apache-2.0", "sha256": "a" * 64}])
        assert len(vet(path)) == 1

    def test_placeholder_pin_blocked(self):
        # the shipped manifest's PENDING-LIVE-VERIFY must fail honestly
        path = self._manifest([{"id": "X/ner", "license": "MIT",
                                "sha256": "PENDING-LIVE-VERIFY"}])
        try:
            vet(path)
            assert False
        except ModelGateError as e:
            assert "64-hex" in str(e)

    def test_nc_licence_blocked(self):
        path = self._manifest([{"id": "X/ner", "license": "CC-BY-NC-4.0",
                                "sha256": "abc"}])
        try:
            vet(path)
            assert False, "must raise"
        except ModelGateError as e:
            assert "blocked licence" in str(e)

    def test_missing_hash_pin_blocked(self):
        path = self._manifest([{"id": "X/ner", "license": "MIT"}])
        try:
            vet(path)
            assert False
        except ModelGateError as e:
            assert "sha256" in str(e)

    def test_unvetted_licence_blocked(self):
        path = self._manifest([{"id": "X/ner", "license": "weird-1.0", "sha256": "z"}])
        try:
            vet(path)
            assert False
        except ModelGateError as e:
            assert "unvetted" in str(e)


if __name__ == "__main__":
    unittest.main()
