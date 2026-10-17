"""Tests for the CHW task engine — schedule math, danger signs, work lists."""

import unittest
from datetime import date, timedelta

from chw_tasks import (
    ANC_CONTACT_WEEKS,
    DOSE_EXPIRY_DAYS,
    Household,
    Member,
    TaskEngine,
    TaskKind,
    TaskStatus,
    anc_tasks,
    danger_sign_task,
    immunisation_tasks,
)

TODAY = date(2026, 10, 18)

PREGNANT = Member(
    member_id="M-01", name="Amina Bello", dob=date(1998, 3, 1), sex="F",
    pregnant=True, lmp=TODAY - timedelta(weeks=19),
)
INFANT = Member(member_id="M-02", name="Chidi Bello", dob=TODAY - timedelta(days=30), sex="M")
HOUSEHOLD = Household(
    household_id="HH-100", facility_id="NG-KD-0014", head_name="Amina Bello",
    members=(PREGNANT, INFANT), gps=(10.52, 7.44),
)


class TestANC(unittest.TestCase):
    def test_eight_contact_schedule(self):
        # Fresh pregnancy: all eight contacts generated.
        fresh = Member("M-9", "New Mother", date(2000, 1, 1), "F",
                       pregnant=True, lmp=TODAY)
        tasks = anc_tasks(HOUSEHOLD, fresh, TODAY)
        self.assertEqual(len(tasks), len(ANC_CONTACT_WEEKS))
        first_due = TODAY + timedelta(weeks=ANC_CONTACT_WEEKS[0])
        self.assertEqual(tasks[0].due, first_due)
        self.assertEqual(tasks[0].kind, TaskKind.ANC_CONTACT)

    def test_long_past_contacts_skipped_next_kept(self):
        # At week 19, contacts 1 (wk 12) is 7 weeks past → skipped;
        # contact 2 (wk 20) is next week → present.
        tasks = anc_tasks(HOUSEHOLD, PREGNANT, TODAY)
        dues = [t.due for t in tasks]
        self.assertNotIn(PREGNANT.lmp + timedelta(weeks=12), dues)
        self.assertIn(PREGNANT.lmp + timedelta(weeks=20), dues)

    def test_not_pregnant_no_tasks(self):
        not_pregnant = Member("M-7", "Other", date(1999, 1, 1), "F")
        self.assertEqual(anc_tasks(HOUSEHOLD, not_pregnant, TODAY), [])


class TestImmunisation(unittest.TestCase):
    def test_infant_schedule_from_dob(self):
        tasks = immunisation_tasks(HOUSEHOLD, INFANT, TODAY)
        labels = [t.label for t in tasks]
        self.assertIn("Immunisation: BCG+OPV0+HepB", labels)
        self.assertIn("Immunisation: Penta1+OPV1+PCV1+IPV1", labels)
        # Birth dose at dob; 6-week dose exactly 42 days after dob.
        birth = next(t for t in tasks if "BCG" in t.label)
        penta1 = next(t for t in tasks if "Penta1" in t.label)
        self.assertEqual(birth.due, INFANT.dob)
        self.assertEqual((penta1.due - INFANT.dob).days, 42)

    def test_expired_doses_dropped(self):
        old_infant = Member("M-8", "Old Baby", TODAY - timedelta(days=200), "M")
        tasks = immunisation_tasks(HOUSEHOLD, old_infant, TODAY)
        # Birth dose (200 days past) exceeds the 60-day window; 14-week
        # dose (98 days offset → 102 days past) also expired; nothing
        # earlier than 9-month dose should appear.
        self.assertFalse(any("BCG" in t.label for t in tasks))
        self.assertTrue(any("Measles1" in t.label for t in tasks))

    def test_child_over_two_no_tasks(self):
        toddler = Member("M-6", "Toddler", TODAY - timedelta(days=731), "M")
        self.assertEqual(immunisation_tasks(HOUSEHOLD, toddler, TODAY), [])


class TestDangerSigns(unittest.TestCase):
    def test_immediate_sign_priority_zero(self):
        task = danger_sign_task(HOUSEHOLD, INFANT, "convulsions", TODAY)
        self.assertEqual(task.priority, 0)
        self.assertEqual(task.due, TODAY)

    def test_unknown_sign_rejected(self):
        with self.assertRaises(ValueError):
            danger_sign_task(HOUSEHOLD, INFANT, "sniffles", TODAY)

    def test_danger_sign_top_of_work_list(self):
        engine = TaskEngine()
        engine.register_household(HOUSEHOLD, TODAY)
        engine.raise_danger_sign("HH-100", "M-02", "fast-breathing", TODAY)
        engine.raise_danger_sign("HH-100", "M-01", "pregnancy-bleeding", TODAY)
        work = engine.work_list(TODAY)
        self.assertEqual(work[0].kind, TaskKind.DANGER_SIGN_REFERRAL)
        self.assertEqual(work[0].priority, 0)


class TestEngine(unittest.TestCase):
    def test_register_household_generates_tasks(self):
        engine = TaskEngine()
        generated = engine.register_household(HOUSEHOLD, TODAY)
        self.assertTrue(generated)
        self.assertEqual(len(engine.tasks), len(generated))

    def test_complete_marks_done_once(self):
        engine = TaskEngine()
        generated = engine.register_household(HOUSEHOLD, TODAY)
        task = engine.complete(generated[0].task_id)
        self.assertEqual(task.status, TaskStatus.DONE)
        with self.assertRaises(ValueError):
            engine.complete(task.task_id)

    def test_sweep_escalates_and_expires(self):
        engine = TaskEngine()
        engine.register_household(HOUSEHOLD, TODAY)
        engine.raise_danger_sign("HH-100", "M-02", "convulsions", TODAY - timedelta(days=3))
        changed = engine.sweep_expiries(TODAY)
        danger = [t for t in changed if t.kind == TaskKind.DANGER_SIGN_REFERRAL]
        self.assertEqual(danger[0].escalation, "facility-nurse-in-charge")
        self.assertEqual(danger[0].status, TaskStatus.OPEN)  # never quietly expired

    def test_expiry_of_anc_after_window(self):
        engine = TaskEngine()
        generated = engine.register_household(HOUSEHOLD, TODAY)
        anc = next(t for t in generated if t.kind == TaskKind.ANC_CONTACT)
        far_future = anc.due + timedelta(days=DOSE_EXPIRY_DAYS)
        engine.sweep_expiries(far_future)
        self.assertEqual(engine.tasks[anc.task_id].status, TaskStatus.EXPIRED)

    def test_work_list_sorted_priority_then_due(self):
        engine = TaskEngine()
        engine.register_household(HOUSEHOLD, TODAY)
        engine.raise_danger_sign("HH-100", "M-02", "fast-breathing", TODAY)
        work = engine.work_list(TODAY)
        keys = [(t.priority, t.due, t.task_id) for t in work]
        self.assertEqual(keys, sorted(keys))

    def test_unknown_household_and_member_rejected(self):
        engine = TaskEngine()
        engine.register_household(HOUSEHOLD, TODAY)
        with self.assertRaises(KeyError):
            engine.raise_danger_sign("HH-XXX", "M-02", "convulsions", TODAY)
        with self.assertRaises(KeyError):
            engine.raise_danger_sign("HH-100", "M-99", "convulsions", TODAY)


if __name__ == "__main__":
    unittest.main()
