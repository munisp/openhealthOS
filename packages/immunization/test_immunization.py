import unittest
from immunization import (ChildRecord, Fridge, ImmunizationError,
                          NPI_SCHEDULE)


class TestChildRecord(unittest.TestCase):
    def setUp(self):
        self.rec = ChildRecord()
        self.rec.register("c1", birth_day=0, facility="phc-1")

    def test_birth_doses(self):
        d = self.rec.record_dose("c1", "BCG", day=1, vaccinator="chw-3")
        self.assertFalse(d["early"])
        self.assertFalse(d["refused"])

    def test_duplicate_dose_and_unknown_antigen_refused(self):
        self.rec.record_dose("c1", "BCG", day=1, vaccinator="v")
        with self.assertRaises(ImmunizationError):
            self.rec.record_dose("c1", "BCG", day=10, vaccinator="v")
        with self.assertRaises(ImmunizationError):
            self.rec.record_dose("c1", "Smallpox", day=10, vaccinator="v")

    def test_early_dose_flagged(self):
        d = self.rec.record_dose("c1", "Penta-1", day=30, vaccinator="v")
        self.assertTrue(d["early"])  # 30d << 42d-4 grace

    def test_refusal_is_data(self):
        d = self.rec.record_dose("c1", "OPV-0", day=1, vaccinator="v",
                                 refused=True)
        self.assertTrue(d["refused"])
        # refused dose doesn't count as done -> child defaults at 6wk+
        self.assertTrue(self.rec.defaulters(as_of=100))

    def test_defaulters_overdue_first(self):
        self.rec.register("c2", birth_day=0, facility="phc-1")
        # c1 fully vaccinated through 14wk
        for a, day in (("BCG", 1), ("OPV-0", 1), ("HepB-0", 1),
                       ("Penta-1", 42), ("OPV-1", 42), ("PCV-1", 42),
                       ("Rota-1", 42), ("Penta-2", 70), ("OPV-2", 70),
                       ("PCV-2", 70), ("Rota-2", 70), ("Penta-3", 98),
                       ("OPV-3", 98), ("PCV-3", 98), ("IPV", 98)):
            self.rec.record_dose("c1", a, day=day, vaccinator="v")
        rows = self.rec.defaulters(as_of=300)  # c1 inside 9mo window, not overdue
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["child_id"], "c2")
        self.assertEqual(rows[0]["visit"], "birth")
        self.assertEqual(rows[0]["days_overdue"], 300 - 14)

    def test_coverage_denominators(self):
        self.rec.register("c2", birth_day=0, facility="phc-1")
        self.rec.record_dose("c1", "BCG", day=1, vaccinator="v")
        self.rec.record_dose("c1", "OPV-0", day=1, vaccinator="v")
        self.rec.record_dose("c1", "HepB-0", day=1, vaccinator="v")
        cov = self.rec.coverage("birth", as_of=50)
        self.assertEqual(cov["eligible"], 2)
        self.assertEqual(cov["fully_covered"], 1)
        self.assertEqual(cov["defaulters"], 1)


class TestFridge(unittest.TestCase):
    def test_excursion_requires_action(self):
        f = Fridge("fridge-1", "phc-1")
        f.log_temp(1, 4.0, logger="chw")
        f.log_temp(2, 12.5, logger="chw")   # excursion
        self.assertEqual(f.open_excursions(10), [2])
        f.record_action(3, officer="cold-chain-officer",
                        action="moved vaccines to backup fridge; technician called")
        self.assertEqual(f.open_excursions(10), [])

    def test_open_excursions_never_disappear(self):
        f = Fridge("fridge-1", "phc-1")
        f.log_temp(1, -5.0, logger="chw")
        rep = f.report(as_of=30)
        self.assertEqual(rep["excursions"], 1)
        self.assertEqual(rep["open_excursion_days"], [1])
        self.assertIn("potency", rep["note"])

    def test_implausible_temp_refused(self):
        f = Fridge("fridge-1", "phc-1")
        with self.assertRaises(ImmunizationError):
            f.log_temp(1, 99.0, logger="chw")

    def test_empty_action_refused(self):
        f = Fridge("fridge-1", "phc-1")
        f.log_temp(1, 11.0, logger="chw")
        with self.assertRaises(ImmunizationError):
            f.record_action(1, officer="x", action="")


if __name__ == "__main__":
    unittest.main()
