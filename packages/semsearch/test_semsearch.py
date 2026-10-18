import unittest

import semsearch as ss


GUIDELINES = {
    "g-malaria": ("Malaria protocol",
                  "Uncomplicated malaria: confirm with RDT before treatment. "
                  "Give artemether-lumefantrine twice daily for three days. "
                  "Severe malaria with convulsions needs IV artesunate and referral."),
    "g-anc": ("Antenatal care",
              "First antenatal visit before 12 weeks. Check blood pressure at "
              "every visit. Give iron and folate. Four visits minimum, eight "
              "contacts preferred. Refer any bleeding in pregnancy immediately."),
    "g-cholera": ("Cholera response",
                  "Suspect cholera in acute watery diarrhoea with vomiting. "
                  "Rehydrate aggressively with ORS. Notify the surveillance "
                  "officer immediately — cholera is immediately notifiable."),
}


class TestIndex(unittest.TestCase):
    def setUp(self):
        self.idx = ss.GuidelineIndex()
        for did, (title, text) in GUIDELINES.items():
            self.idx.add_or_update(did, title, text)

    def test_search_finds_right_guideline(self):
        hits = self.idx.search("bleeding in pregnancy what do I do")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc"], "g-anc")

    def test_search_malaria(self):
        hits = self.idx.search("artesunate severe malaria convulsions")
        self.assertEqual(hits[0]["doc"], "g-malaria")

    def test_search_notifiable(self):
        hits = self.idx.search("watery diarrhoea notify surveillance")
        self.assertEqual(hits[0]["doc"], "g-cholera")

    def test_incremental_reindex_noop_when_unchanged(self):
        out = self.idx.add_or_update("g-malaria", *GUIDELINES["g-malaria"])
        self.assertTrue(out["unchanged"])
        self.assertEqual(out["indexed"], 0)

    def test_reindex_on_change(self):
        before = self.idx.stats()["chunks"]
        out = self.idx.add_or_update("g-anc", "Antenatal care",
                                     GUIDELINES["g-anc"][1] + " New section on ultrasound.")
        self.assertFalse(out["unchanged"])
        self.assertGreaterEqual(self.idx.stats()["chunks"], before - 1)

    def test_remove(self):
        self.assertTrue(self.idx.remove("g-cholera"))
        hits = self.idx.search("watery diarrhoea notify surveillance")
        self.assertFalse(any(h["doc"] == "g-cholera" for h in hits))
        self.assertFalse(self.idx.remove("g-cholera"))

    def test_chunk_overlap_preserves_steps(self):
        text = "step one do this. " * 60 + "CRITICAL STEP: give magnesium sulphate. " + "tail. " * 60
        chunks = ss.chunk(text)
        # the critical step must appear with context in some chunk
        self.assertTrue(any("magnesium sulphate" in c for c in chunks))

    def test_pg_export_matches_offline_store(self):
        rows = self.idx.export_for_pg()
        self.assertEqual(len(rows), self.idx.stats()["chunks"])
        for r in rows:
            self.assertEqual(len(r["embedding"]), 4096)
            self.assertIn("content_hash", r)
        self.assertIn("VECTOR(4096)", self.idx.pg_ddl())

    def test_score_floor(self):
        self.assertEqual(self.idx.search("zzzq unrelated xyzzy"), [])


if __name__ == "__main__":
    unittest.main()
