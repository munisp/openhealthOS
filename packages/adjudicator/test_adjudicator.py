import json
import unittest

from adjudicate import adjudicate


def claim(**kw):
    base = {"claim_id": "C-1", "member_id": "NG-0001", "provider_id": "PHC-007",
            "service_date": "2026-10-01", "items": [{"code": "MAL-RDT", "qty": 1}],
            "claimed_ngn": 1500}
    base.update(kw)
    return base


class TestAdjudicator(unittest.TestCase):
    def test_approve(self):
        d = adjudicate(claim())
        self.assertEqual(d["decision"], "APPROVE")
        self.assertEqual(d["allowed_ngn"], 1500)
        self.assertEqual(d["copay_ngn"], 0)
        self.assertEqual(d["payable_ngn"], 1500)

    def test_unknown_member(self):
        self.assertEqual(adjudicate(claim(member_id="X"))["decision"], "DENY")

    def test_expired_or_inactive(self):
        self.assertEqual(adjudicate(claim(member_id="NG-0003"))["decision"], "DENY")

    def test_not_in_benefit(self):
        d = adjudicate(claim(member_id="NG-0002", items=[{"code": "ITN-001", "qty": 1}]))
        self.assertEqual(d["decision"], "DENY")
        self.assertTrue(any(r.startswith("BENEFIT") for r in d["reasons"]))

    def test_frequency_cap_pends(self):
        d = adjudicate(claim(items=[{"code": "MAL-RDT", "qty": 5}]))
        self.assertEqual(d["decision"], "PEND")

    def test_duplicate_flags(self):
        h = [{"member_id": "NG-0001", "provider_id": "PHC-007",
              "service_date": "2026-09-28", "items": [{"code": "MAL-RDT"}]}]
        d = adjudicate(claim(history=h))
        self.assertEqual(d["decision"], "PEND")
        self.assertTrue(any(f.startswith("DUPLICATE") for f in d["flags"]))

    def test_price_drift_flags(self):
        self.assertEqual(adjudicate(claim(claimed_ngn=5000))["decision"], "PEND")

    def test_copay_for_non_vulnerable(self):
        d = adjudicate(claim(member_id="NG-0002", items=[{"code": "LAB-MP", "qty": 1}]))
        self.assertEqual(d["copay_ngn"], 350)
        self.assertEqual(d["payable_ngn"], 3150)

    def test_decision_json_roundtrip(self):
        d = adjudicate(claim())
        json.dumps(d)  # every decision must be serializable — it is an event


if __name__ == "__main__":
    unittest.main()
