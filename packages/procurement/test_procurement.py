import unittest

from procurement import (CatalogueError, build_scorecards, pool_orders,
                         price_order)


class TestCatalogue(unittest.TestCase):
    def test_framework_pricing(self):
        self.assertEqual(price_order({"PARA-TAB": 10, "MAL-RDT": 5}), 12000 + 7500)

    def test_unknown_sku_rejected(self):
        with self.assertRaises(CatalogueError):
            price_order({"FAKE-1": 1})

    def test_zero_qty_rejected(self):
        with self.assertRaises(CatalogueError):
            price_order({"PARA-TAB": 0})


class TestPooling(unittest.TestCase):
    def test_cluster_aggregation(self):
        pooled = pool_orders([
            {"facility": "PHC-007", "items": {"PARA-TAB": 10, "ORS-001": 20}},
            {"facility": "PHC-012", "items": {"PARA-TAB": 5}},
        ])
        self.assertEqual(pooled, {"PARA-TAB": 15, "ORS-001": 20})


class TestScorecards(unittest.TestCase):
    EVENTS = [
        {"type": "order_placed", "supplier": "MedServe"},
        {"type": "order_placed", "supplier": "MedServe"},
        {"type": "order_placed", "supplier": "MedServe"},
        {"type": "order_placed", "supplier": "MedServe"},
        {"type": "order_placed", "supplier": "MedServe"},
        {"type": "delivery", "supplier": "MedServe", "on_time": True},
        {"type": "delivery", "supplier": "MedServe", "on_time": True},
        {"type": "delivery", "supplier": "MedServe", "on_time": False},
        {"type": "delivery", "supplier": "MedServe", "on_time": False},
        {"type": "delivery_failed", "supplier": "MedServe"},
        {"type": "stockout_day", "supplier": "MedServe"},
        {"type": "order_placed", "supplier": "SlowPharma"},
        {"type": "delivery_failed", "supplier": "SlowPharma"},
    ]

    def test_fulfilment_and_grade(self):
        cards = build_scorecards(self.EVENTS)
        ms = cards["MedServe"]
        self.assertEqual(ms.ordered, 5)
        self.assertEqual(ms.delivered_on_time, 2)
        self.assertAlmostEqual(ms.fulfilment_rate, 0.4)
        self.assertEqual(ms.grade, "FAILING")
        self.assertEqual(ms.stockout_days_linked, 1)

    def test_insufficient_data(self):
        cards = build_scorecards(self.EVENTS)
        self.assertEqual(cards["SlowPharma"].grade, "INSUFFICIENT-DATA")

    def test_a_grade(self):
        events = [{"type": "order_placed", "supplier": "Good"}] * 20 + \
                 [{"type": "delivery", "supplier": "Good", "on_time": True}] * 19 + \
                 [{"type": "delivery", "supplier": "Good", "on_time": False}]
        self.assertEqual(build_scorecards(events)["Good"].grade, "A")


if __name__ == "__main__":
    unittest.main()
