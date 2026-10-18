#!/usr/bin/env python3
"""Household registers: defaulters, catchment coverage, safe GeoJSON."""
import unittest

from households import Household, HouseholdRegister, Member, haversine_km


class TestRegister(unittest.TestCase):
    def setUp(self):
        self.reg = HouseholdRegister()
        self.reg.register(Household("HH-1", "KAWO", 10.5200, 7.4380,
                                    [Member("PT-1", "2026-10-01", "measles-2", "Amina"),
                                     Member("PT-2", "2026-11-01", "anc-3", "Binta")]))
        self.reg.register(Household("HH-2", "KAWO", 10.6100, 7.5200,
                                    [Member("PT-3", "2026-09-20", "pentavalent-3", "Chidi")]))
        self.reg.register(Household("HH-3", "MAKERA", 11.5000, 8.5000))

    def test_invalid_coordinates_refused(self):
        with self.assertRaises(ValueError):
            self.reg.register(Household("HH-BAD", "X", 95.0, 7.0))

    def test_defaulters_oldest_first_and_grace(self):
        defs = self.reg.defaulters("2026-10-10")
        self.assertEqual([d.patient_id for d in defs], ["PT-3", "PT-1"])
        self.assertEqual(defs[0].overdue_days, 20)
        # 15 days of grace clears PT-1 but not PT-3
        defs = self.reg.defaulters("2026-10-10", grace_days=15)
        self.assertEqual([d.patient_id for d in defs], ["PT-3"])

    def test_coverage_radius(self):
        cov = self.reg.coverage(10.5200, 7.4380, radius_km=15)
        self.assertEqual(cov["households_inside"], 2)
        self.assertEqual(cov["households_outside"], 1)
        self.assertEqual(cov["outside_ids"], ["HH-3"])
        self.assertEqual(cov["members_inside"], 3)

    def test_haversine_sanity(self):
        # ~111 km per degree of latitude
        self.assertAlmostEqual(haversine_km(10.0, 7.0, 11.0, 7.0), 111.0, delta=2.0)


class TestGeoJSON(unittest.TestCase):
    def test_anonymized_by_default(self):
        reg = HouseholdRegister()
        reg.register(Household("HH-9", "KAWO", 10.5, 7.4,
                               [Member("PT-9", "2026-09-01", "measles-2", "Secret Name")]))
        gj = reg.to_geojson(as_of="2026-10-10")
        blob = str(gj)
        self.assertNotIn("PT-9", blob)
        self.assertNotIn("Secret Name", blob)
        self.assertNotIn("HH-9", blob)  # hashed
        props = gj["features"][0]["properties"]
        self.assertEqual(props["members"], 1)
        self.assertEqual(props["defaulters"], 1)

    def test_identified_export_is_explicit(self):
        reg = HouseholdRegister()
        reg.register(Household("HH-9", "KAWO", 10.5, 7.4))
        gj = reg.to_geojson(anonymize=False)
        self.assertEqual(gj["features"][0]["properties"]["household"], "HH-9")


if __name__ == "__main__":
    unittest.main()
