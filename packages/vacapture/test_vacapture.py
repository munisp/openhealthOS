import unittest
from vacapture import VARegistry, VAError


def _complete(reg, did):
    reg.record_consent(did, consented=True, witness="community-leader", day=10)
    reg.fill_section(did, "identity", {"age_days": 200, "sex": "F"})
    reg.fill_section(did, "symptoms",
                     {"reported": ["fast-breathing", "chest-indrawing", "cough"]})
    reg.fill_section(did, "care_seeking", {"sought_care": True, "facility": "phc-1"})


class TestVA(unittest.TestCase):
    def test_full_flow_advisory_hint(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        _complete(r, "d1")
        r.submit("d1")
        out = r.probable_cause("d1")
        self.assertTrue(out["advisory"])
        self.assertIn("possible-pneumonia", out["hints"])

    def test_submit_without_consent_refused(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        _complete(r, "d1")
        r.forms["d1"]["consent"] = False
        with self.assertRaises(VAError):
            r.submit("d1")

    def test_incomplete_form_refused_and_missing_named(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        r.record_consent("d1", consented=True, witness="w", day=10)
        with self.assertRaises(VAError) as cm:
            r.submit("d1")
        self.assertIn("symptoms", str(cm.exception))

    def test_hint_requires_submitted(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        _complete(r, "d1")
        with self.assertRaises(VAError):
            r.probable_cause("d1")

    def test_unknown_symptoms_give_undetermined(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        r.record_consent("d1", consented=True, witness="w", day=10)
        r.fill_section("d1" if False else "d1", "identity", {})
        r.fill_section("d1", "symptoms", {"reported": ["hiccups"]})
        r.fill_section("d1", "care_seeking", {})
        r.submit("d1")
        self.assertEqual(r.probable_cause("d1")["hints"],
                         ["undetermined-from-reported-symptoms"])

    def test_consent_section_cannot_be_backfilled(self):
        r = VARegistry()
        r.start("d1", interviewer="chw-7", day=9)
        with self.assertRaises(VAError):
            r.fill_section("d1", "consent", {"consented": True})

    def test_aggregate_is_counts_only(self):
        r = VARegistry()
        r.start("d1", interviewer="a", day=1)
        _complete(r, "d1")
        r.submit("d1")
        r.start("d2", interviewer="a", day=2)  # left as draft
        agg = r.aggregate()
        self.assertEqual(agg["submitted"], 1)
        self.assertEqual(agg["drafts"], 1)
        self.assertEqual(agg["advisory_cause_counts"]["possible-pneumonia"], 1)
        self.assertIn("advisory", agg["note"])

    def test_duplicate_start_refused(self):
        r = VARegistry()
        r.start("d1", interviewer="a", day=1)
        with self.assertRaises(VAError):
            r.start("d1", interviewer="b", day=2)


if __name__ == "__main__":
    unittest.main()
