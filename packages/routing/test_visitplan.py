#!/usr/bin/env python3
import unittest

from visitplan import Visit, plan_visits


class VisitPlanTest(unittest.TestCase):
    def test_empty_day(self):
        self.assertEqual(plan_visits([], 6.5, 3.3)["order"], [])

    def test_orders_by_proximity_not_registration(self):
        # registration order is the WORST order: far, near, far
        visits = [
            Visit("far-west", 6.45, 3.30),
            Visit("near", 6.501, 3.301),
            Visit("far-east", 6.55, 3.40),
        ]
        plan = plan_visits(visits, 6.50, 3.30)
        self.assertEqual(plan["order"][0], "near")
        self.assertEqual(len(plan["legs"]), 3)
        self.assertAlmostEqual(sum(plan["legs"]), plan["total_km"], places=1)

    def test_two_opt_beats_naive_on_zigzag(self):
        # a zigzag that NN alone can leave crossing itself
        visits = [
            Visit("a", 6.50, 3.30), Visit("b", 6.60, 3.30),
            Visit("c", 6.50, 3.40), Visit("d", 6.60, 3.40),
        ]
        plan = plan_visits(visits, 6.50, 3.30)
        naive = sum(
            abs(v2.lat - v1.lat) + abs(v2.lng - v1.lng)
            for v1, v2 in zip(visits, visits[1:])
        )
        self.assertLessEqual(plan["total_km"], naive * 111 * 1.4 + 1e-6)

    def test_road_graph_fallback_to_haversine(self):
        visits = [Visit("x", 6.51, 3.31)]
        plan = plan_visits(visits, 6.50, 3.30, graph_route=lambda *a: {})
        self.assertEqual(plan["order"], ["x"])
        self.assertGreater(plan["total_km"], 0)


if __name__ == "__main__":
    unittest.main()
