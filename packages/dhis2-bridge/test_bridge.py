import unittest

from bridge import aggregate, period_of, to_datavalueset

EVENTS = [
    {"type": "encounter", "facility": "PHC-007", "ts": "2026-10-01T09:00:00Z"},
    {"type": "encounter", "facility": "PHC-007", "ts": "2026-10-02T11:00:00Z"},
    {"type": "immunisation", "facility": "PHC-007", "ts": "2026-10-02T11:30:00Z"},
    {"type": "encounter", "facility": "PHC-012", "ts": "2026-10-03T08:00:00Z"},
    {"type": "encounter", "facility": "PHC-007", "ts": "2026-11-01T09:00:00Z"},  # next period
    {"type": "unknown_thing", "facility": "PHC-007", "ts": "2026-10-01T09:00:00Z"},  # ignored
]


class TestBridge(unittest.TestCase):
    def test_period(self):
        self.assertEqual(period_of("2026-10-01T09:00:00Z"), "202610")

    def test_aggregation_groups_correctly(self):
        vals = aggregate(EVENTS)
        got = {(v["orgUnit"], v["period"], v["dataElement"]): v["value"] for v in vals}
        self.assertEqual(got[("PHC-007", "202610", "DE_OPD_VISITS")], 2)
        self.assertEqual(got[("PHC-007", "202610", "DE_IMMUNISATIONS")], 1)
        self.assertEqual(got[("PHC-012", "202610", "DE_OPD_VISITS")], 1)
        self.assertEqual(got[("PHC-007", "202611", "DE_OPD_VISITS")], 1)
        self.assertEqual(len(vals), 4)  # unknown event type never aggregates

    def test_datavalueset_shape(self):
        dvs = to_datavalueset(aggregate(EVENTS))
        self.assertIn("dataValueSets", dvs)
        for s in dvs["dataValueSets"]:
            self.assertEqual(s["dataSet"], "OH-PHC-MONTHLY")
            self.assertTrue(s["dataValues"])
            self.assertRegex(s["period"], r"^\d{6}$")

    def test_empty_input(self):
        self.assertEqual(to_datavalueset([])["dataValueSets"], [])


if __name__ == "__main__":
    unittest.main()
