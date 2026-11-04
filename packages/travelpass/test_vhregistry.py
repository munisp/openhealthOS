"""Tests for vhregistry — pseudonymized incident registry, BR-10
outbreak alerts with duty-officer acknowledgement, anonymized export
packs, and the public dashboard with stated suppression."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhregistry import IncidentRegistry, RegistryError  # noqa: E402


def _reg():
    return IncidentRegistry(rules_version="visitor-incidents@1.0.0",
                            salt="e2e-salt")


def _gi(reg, n, *, facility="HOSP-SAL", site="sal", day=10):
    for i in range(n):
        reg.register_incident(traveler_key="TV-%d" % i,
                              syndromic_class="gastrointestinal",
                              facility=facility, site=site,
                              outcome="discharged", cost_minor=2500,
                              day=day, cover_ref="VHC-%d" % i)


class TestRegistration(unittest.TestCase):
    def test_pseudonymized_at_the_door(self):
        reg = _reg()
        inc = reg.register_incident(
            traveler_key="TV-REAL-NAME", syndromic_class="trauma",
            facility="H", site="s", outcome="discharged",
            cost_minor=0, day=1)
        self.assertNotIn("TV-REAL-NAME", str(reg.incidents))
        self.assertEqual(len(inc["traveler_pseudo"]), 64)

    def test_vocabulary_is_versioned_not_improvised(self):
        reg = _reg()
        with self.assertRaises(RegistryError):
            reg.register_incident(traveler_key="T",
                                  syndromic_class="tummy-bug",
                                  facility="H", site="s",
                                  outcome="discharged", cost_minor=0,
                                  day=1)
        with self.assertRaises(RegistryError):
            reg.register_incident(traveler_key="T",
                                  syndromic_class="trauma",
                                  facility="H", site="s",
                                  outcome="walked-it-off",
                                  cost_minor=0, day=1)

    def test_salt_is_mandatory(self):
        with self.assertRaises(RegistryError):
            IncidentRegistry(rules_version="visitor-incidents@1.0.0",
                             salt="")

    def test_cost_none_is_a_hole(self):
        reg = _reg()
        with self.assertRaises(RegistryError):
            reg.register_incident(traveler_key="T",
                                  syndromic_class="trauma", facility="H",
                                  site="s", outcome="discharged",
                                  cost_minor=None, day=1)


class TestBR10(unittest.TestCase):
    def test_five_gi_same_facility_week_opens_alert(self):
        reg = _reg()
        _gi(reg, 4)
        self.assertEqual(reg.evaluate_alerts(day=12)["opened"], [])
        reg.register_incident(traveler_key="TV-5",
                              syndromic_class="gastrointestinal",
                              facility="HOSP-SAL", site="sal",
                              outcome="admitted", cost_minor=9000,
                              day=11)
        out = reg.evaluate_alerts(day=12)
        self.assertEqual(out["opened"], ["BR10-HOSP-SAL-W1"])
        self.assertEqual(out["total_open"], 1)

    def test_different_facility_or_week_does_not_trip(self):
        reg = _reg()
        _gi(reg, 3, facility="HOSP-A", day=10)
        _gi(reg, 3, facility="HOSP-B", day=10)
        _gi(reg, 3, facility="HOSP-A", day=30)  # later week
        self.assertEqual(reg.evaluate_alerts(day=31)["opened"], [])

    def test_evaluation_is_idempotent(self):
        reg = _reg()
        _gi(reg, 5)
        reg.evaluate_alerts(day=12)
        self.assertEqual(reg.evaluate_alerts(day=13)["opened"], [])

    def test_acknowledgement_names_officer_and_action(self):
        reg = _reg()
        _gi(reg, 5)
        alert = reg.evaluate_alerts(day=12)["opened"][0]
        with self.assertRaises(RegistryError):
            reg.acknowledge(alert, by="", note="", day=13)
        reg.acknowledge(alert, by="duty-officer-1",
                        note="stool cultures ordered, water supply "
                             "inspection dispatched", day=14)
        rep = reg.report()
        self.assertEqual(rep["alerts_open"], 0)
        self.assertEqual(rep["ack_response_days"], [2])


class TestExportsAndDashboard(unittest.TestCase):
    def test_export_pack_aggregates_with_checksum(self):
        reg = _reg()
        _gi(reg, 5)
        pack = reg.export_pack(export_format="ihr", week=1)
        self.assertEqual(pack["rows"],
                         [{"syndromic_class": "gastrointestinal",
                           "site": "sal", "cases": 5}])
        self.assertEqual(len(pack["pack_sha256"]), 64)
        self.assertEqual(pack["suppressed_cells"], 0)

    def test_small_cells_suppressed_and_stated(self):
        reg = _reg()
        _gi(reg, 5)
        reg.register_incident(traveler_key="TV-R1",
                              syndromic_class="neurological",
                              facility="HOSP-SAL", site="sal",
                              outcome="transferred", cost_minor=90000,
                              day=10)
        pack = reg.export_pack(export_format="ecdc", week=1)
        self.assertEqual(pack["suppressed_cells"], 1)
        self.assertEqual(len(pack["rows"]), 1)  # the neuro cell hid

    def test_unknown_format_refuses(self):
        reg = _reg()
        with self.assertRaises(RegistryError):
            reg.export_pack(export_format="darkweb", week=1)

    def test_public_dashboard_denominators(self):
        reg = _reg()
        _gi(reg, 6)
        reg.evaluate_alerts(day=12)
        dash = reg.public_dashboard(quarter_day=10)
        self.assertEqual(dash["incidents_total"], 6)
        self.assertEqual(dash["by_syndromic_class"],
                         {"gastrointestinal": 6})
        self.assertEqual(dash["covered_share"],
                         {"covered": 6, "total": 6})
        self.assertEqual(dash["alerts"]["unacknowledged"], 1)


if __name__ == "__main__":
    unittest.main()
