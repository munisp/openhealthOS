#!/usr/bin/env python3
"""Rollup tests — per-LGA aggregation, state totals, ministry brief."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rollups import facility_rollup, render_markdown, state_rollup

FACILITIES = [
    {"id": "F1", "state": "Lagos", "lga": "Ikeja", "level": "PHC", "ownership": "public",
     "bhcpf": True, "beds": 8, "doctors": 1, "nurses": 4,
     "amenities": ["pharmacy", "cold-chain"], "power": "hybrid", "water": "borehole",
     "latitude": 6.6, "longitude": 3.35},
    {"id": "F2", "state": "Lagos", "lga": "Ikeja", "level": "hospital", "ownership": "private",
     "bhcpf": False, "beds": 60, "doctors": 12, "nurses": 30,
     "amenities": ["pharmacy", "lab", "xray", "oxygen", "ambulance", "maternity"],
     "power": "grid", "water": "piped", "latitude": 6.61, "longitude": 3.36},
    {"id": "F3", "state": "Lagos", "lga": "Epe", "level": "PHC", "ownership": "public",
     "bhcpf": True, "beds": 6, "doctors": 0, "nurses": 3,
     "amenities": ["pharmacy"], "power": "solar", "water": "well"},
    {"id": "F4", "state": "Kano", "lga": "Nassarawa", "level": "clinic",
     "ownership": "faith-based", "bhcpf": False, "beds": 10, "doctors": 2, "nurses": 5,
     "amenities": ["pharmacy", "lab", "maternity"], "power": "generator",
     "water": "borehole", "latitude": 12.0, "longitude": 8.52},
]

EVENTS = [
    {"type": "claim", "facility_id": "F1", "status": "submitted"},
    {"type": "claim", "facility_id": "F1", "status": "approved", "amount_ngn": 45000.0},
    {"type": "claim", "facility_id": "F2", "status": "approved", "amount_ngn": 380000.0},
    {"type": "claim", "facility_id": "F2", "status": "rejected", "amount_ngn": 12000.0},
    {"type": "claim", "facility_id": "GHOST", "status": "approved", "amount_ngn": 999.0},
    {"type": "stockout", "facility_id": "F3"},
    {"type": "stockout", "facility_id": "F3"},
    {"type": "encounter", "facility_id": "F1"},  # not a rollup event
]


class TestFacilityRollup(unittest.TestCase):
    def test_per_lga_aggregation(self):
        out = facility_rollup(FACILITIES, EVENTS)
        ikeja = out["Lagos"]["Ikeja"]
        self.assertEqual(ikeja["facilities"], 2)
        self.assertEqual(ikeja["by_level"], {"PHC": 1, "hospital": 1})
        self.assertEqual(ikeja["by_ownership"], {"public": 1, "private": 1})
        self.assertEqual(ikeja["beds"], 68)
        self.assertEqual(ikeja["doctors"], 13)
        self.assertEqual(ikeja["nurses"], 34)
        self.assertEqual(ikeja["bhcpf"], 1)
        self.assertEqual(ikeja["amenity_coverage"]["oxygen"], 1)
        self.assertEqual(ikeja["amenity_coverage_pct"]["oxygen"], 50.0)
        self.assertEqual(ikeja["amenity_coverage_pct"]["pharmacy"], 100.0)
        self.assertEqual(ikeja["geocoded"], 2)
        # claims attributed through the facility
        self.assertEqual(ikeja["claims_submitted"], 1)
        self.assertEqual(ikeja["claims_approved"], 2)
        self.assertEqual(ikeja["claims_rejected"], 1)
        self.assertEqual(ikeja["claims_approved_ngn"], 425000.0)

    def test_unknown_facility_events_unattributed(self):
        out = facility_rollup(FACILITIES, EVENTS)
        total_approved = sum(b["claims_approved"] for lgas in out.values() for b in lgas.values())
        self.assertEqual(total_approved, 2)  # GHOST's claim counted nowhere

    def test_stockouts_and_power(self):
        out = facility_rollup(FACILITIES, EVENTS)
        epe = out["Lagos"]["Epe"]
        self.assertEqual(epe["stockouts"], 2)
        self.assertEqual(epe["power"], {"solar": 1})
        self.assertEqual(epe["geocoded"], 0)
        self.assertEqual(epe["beds_per_facility"], 6.0)

    def test_empty_inputs(self):
        self.assertEqual(facility_rollup([], []), {})


class TestStateRollup(unittest.TestCase):
    def test_state_totals(self):
        lgas = facility_rollup(FACILITIES, EVENTS)
        states = state_rollup(lgas)
        lag = states["Lagos"]
        self.assertEqual(lag["facilities"], 3)
        self.assertEqual(lag["lgas"], 2)
        self.assertEqual(lag["beds"], 74)
        self.assertEqual(lag["doctors"], 13)
        self.assertEqual(lag["nurses"], 37)
        self.assertEqual(lag["claims_approved_ngn"], 425000.0)
        self.assertEqual(lag["stockouts"], 2)
        self.assertEqual(lag["amenity_coverage"]["cold-chain"], 1)
        self.assertAlmostEqual(lag["amenity_coverage_pct"]["cold-chain"], 33.3)
        kano = states["Kano"]
        self.assertEqual(kano["facilities"], 1)
        self.assertEqual(kano["by_ownership"], {"faith-based": 1})


class TestReport(unittest.TestCase):
    def test_markdown_brief(self):
        lgas = facility_rollup(FACILITIES, EVENTS)
        md = render_markdown(lgas, state_rollup(lgas))
        self.assertIn("## Lagos — 3 facilities across 2 LGAs", md)
        self.assertIn("| Ikeja | 2 | 1 | 1 | 68 | 13 | 34 | 50.0 |", md)
        self.assertIn("425,000", md)
        self.assertIn("## Kano", md)


if __name__ == "__main__":
    unittest.main()
