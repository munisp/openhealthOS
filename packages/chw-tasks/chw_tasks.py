"""OpenHealth OS — community health worker (CHW) task engine.

Spec module 3, OpenSRP pattern: a CHW's day is a sorted list of tasks
generated from household census data — ANC contact schedules, childhood
immunisation visits per the Nigeria NPI schedule, and danger-sign
referrals. The engine is pure and offline: it computes everything from
census records and an injectable "today", so an edge device can run it
with no connectivity and sync outcomes later.

Determinism matters here: two devices holding the same census record
must generate the same tasks. All schedule math is anchored to calendar
dates (date arithmetic only, no wall-clock reads inside generators).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum


# ---------------------------------------------------------------- census

@dataclass(frozen=True)
class Member:
    """One person in a household census."""

    member_id: str
    name: str
    dob: date
    sex: str  # "F" | "M"
    pregnant: bool = False
    lmp: date | None = None  # last menstrual period, when pregnant

    def age_days(self, today: date) -> int:
        return (today - self.dob).days


@dataclass(frozen=True)
class Household:
    household_id: str
    facility_id: str
    head_name: str
    members: tuple[Member, ...]
    gps: tuple[float, float] | None = None


# ---------------------------------------------------------------- tasks

class TaskKind(str, Enum):
    ANC_CONTACT = "anc-contact"
    IMMUNISATION = "immunisation"
    DANGER_SIGN_REFERRAL = "danger-sign-referral"
    HOUSEHOLD_FOLLOWUP = "household-followup"


class TaskStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class Task:
    task_id: str
    household_id: str
    member_id: str | None
    kind: TaskKind
    label: str
    due: date
    priority: int = 3  # 0 = emergency … 3 = routine
    status: TaskStatus = TaskStatus.OPEN
    escalation: str | None = None

    def overdue_days(self, today: date) -> int:
        return max(0, (today - self.due).days)


# ------------------------------------------------------- ANC schedule
# Nigeria FMoH adopted the WHO 2016 eight-contact model. Contacts are
# anchored to gestational-age weeks counted from LMP.

ANC_CONTACT_WEEKS: tuple[int, ...] = (12, 20, 26, 30, 34, 36, 38, 40)


def anc_tasks(household: Household, member: Member, today: date) -> list[Task]:
    """Generate ANC contact tasks for a pregnant member.

    Contacts already past by more than the contact window are skipped —
    the CHW cannot travel back in time — but the next contact is always
    generated even if slightly overdue, because a missed ANC contact is
    itself a risk signal worth surfacing.
    """
    if not member.pregnant or member.lmp is None:
        return []
    tasks: list[Task] = []
    for i, week in enumerate(ANC_CONTACT_WEEKS, start=1):
        due = member.lmp + timedelta(weeks=week)
        if due < today - timedelta(days=14):
            continue  # long past; clinical record shows it was missed
        tasks.append(Task(
            task_id=f"ANC-{household.household_id}-{member.member_id}-{i}",
            household_id=household.household_id,
            member_id=member.member_id,
            kind=TaskKind.ANC_CONTACT,
            label=f"ANC contact {i} (week {week})",
            due=due,
            priority=2,
        ))
    return tasks


# ---------------------------------------------- immunisation schedule
# Nigeria NPI routine schedule (birth dose through second measles).

NPI_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("BCG+OPV0+HepB", 0),
    ("Penta1+OPV1+PCV1+IPV1", 42),    # 6 weeks
    ("Penta2+OPV2+PCV2", 70),          # 10 weeks
    ("Penta3+OPV3+PCV3+IPV2", 98),     # 14 weeks
    ("Measles1+YellowFever", 274),     # 9 months
    ("Measles2", 456),                 # 15 months
)

# A dose is dropped from the work list once the child is this far past
# the scheduled date — the campaign has moved on and catch-up is a
# facility decision, not a CHW doorstep task.
DOSE_EXPIRY_DAYS = 60


def immunisation_tasks(household: Household, member: Member, today: date) -> list[Task]:
    """Generate immunisation visit tasks for children under two."""
    if member.age_days(today) > 730:
        return []
    tasks: list[Task] = []
    for antigen, offset in NPI_SCHEDULE:
        due = member.dob + timedelta(days=offset)
        if due < today - timedelta(days=DOSE_EXPIRY_DAYS):
            continue
        tasks.append(Task(
            task_id=f"IMM-{household.household_id}-{member.member_id}-{offset}",
            household_id=household.household_id,
            member_id=member.member_id,
            kind=TaskKind.IMMUNISATION,
            label=f"Immunisation: {antigen}",
            due=due,
            priority=2,
        ))
    return tasks


# ------------------------------------------------------- danger signs
# Triage red flags that turn into immediate referral tasks. These are
# deliberately a small, high-specificity set — a CHW protocol fails if
# everything is an emergency.

DANGER_SIGNS: dict[str, str] = {
    "convulsions": "Convulsions — refer now",
    "unable-to-breastfeed": "Child unable to drink/breastfeed — refer now",
    "vomits-everything": "Vomits everything — refer now",
    "fast-breathing": "Fast breathing (possible pneumonia) — refer today",
    "severe-dehydration": "Severe dehydration — refer now",
    "pregnancy-bleeding": "Bleeding in pregnancy — refer now",
    "pregnancy-headache-blurred-vision": "Severe headache with blurred vision in pregnancy — refer now",
    "reduced-fetal-movement": "Reduced fetal movement — refer today",
}

_IMMEDIATE_SIGNS = {
    "convulsions", "unable-to-breastfeed", "vomits-everything",
    "severe-dehydration", "pregnancy-bleeding",
    "pregnancy-headache-blurred-vision",
}


def danger_sign_task(
    household: Household, member: Member, sign: str, observed: date,
) -> Task:
    """Create a referral task for an observed danger sign.

    Unknown signs are rejected: a typo must not silently downgrade an
    emergency into a note nobody reads.
    """
    if sign not in DANGER_SIGNS:
        raise ValueError(f"unknown danger sign: {sign!r}")
    immediate = sign in _IMMEDIATE_SIGNS
    return Task(
        task_id=f"DSR-{household.household_id}-{member.member_id}-{sign}",
        household_id=household.household_id,
        member_id=member.member_id,
        kind=TaskKind.DANGER_SIGN_REFERRAL,
        label=DANGER_SIGNS[sign],
        due=observed,  # due the day it is observed
        priority=0 if immediate else 1,
    )


# ---------------------------------------------------------------- engine

@dataclass
class TaskEngine:
    """Builds and maintains the CHW work list for a set of households."""

    households: list[Household] = field(default_factory=list)
    tasks: dict[str, Task] = field(default_factory=dict)

    def register_household(self, household: Household, today: date) -> list[Task]:
        """Add a household and generate its initial tasks."""
        self.households.append(household)
        generated: list[Task] = []
        for member in household.members:
            generated.extend(anc_tasks(household, member, today))
            generated.extend(immunisation_tasks(household, member, today))
        for task in generated:
            self.tasks[task.task_id] = task
        return generated

    def raise_danger_sign(
        self, household_id: str, member_id: str, sign: str, observed: date,
    ) -> Task:
        household = self._household(household_id)
        member = self._member(household, member_id)
        task = danger_sign_task(household, member, sign, observed)
        self.tasks[task.task_id] = task
        return task

    def complete(self, task_id: str) -> Task:
        task = self.tasks[task_id]
        if task.status != TaskStatus.OPEN:
            raise ValueError(f"task {task_id} is not open (status={task.status.value})")
        task.status = TaskStatus.DONE
        return task

    def sweep_expiries(self, today: date) -> list[Task]:
        """Expire open tasks past their working window and escalate the rest.

        ANC and immunisation tasks expire after their catch-up window;
        an overdue-but-live task gets an escalation marker rather than
        silently ageing in the list.
        """
        changed: list[Task] = []
        for task in self.tasks.values():
            if task.status != TaskStatus.OPEN:
                continue
            overdue = task.overdue_days(today)
            if task.kind == TaskKind.DANGER_SIGN_REFERRAL and overdue > 2:
                # An unactioned danger sign is the worst outcome in the
                # system: escalate loudly, never expire quietly.
                task.escalation = "facility-nurse-in-charge"
                task.priority = 0
                changed.append(task)
            elif task.kind in (TaskKind.ANC_CONTACT, TaskKind.IMMUNISATION):
                window = 14 if task.kind == TaskKind.ANC_CONTACT else DOSE_EXPIRY_DAYS
                if overdue > window:
                    task.status = TaskStatus.EXPIRED
                    changed.append(task)
                elif overdue > 7:
                    task.escalation = "chw-supervisor"
                    changed.append(task)
        return changed

    def work_list(self, chw_day: date) -> list[Task]:
        """The CHW's sorted day: priority first, then earliest due."""
        open_tasks = [t for t in self.tasks.values() if t.status == TaskStatus.OPEN]
        return sorted(open_tasks, key=lambda t: (t.priority, t.due, t.task_id))

    def _household(self, household_id: str) -> Household:
        for h in self.households:
            if h.household_id == household_id:
                return h
        raise KeyError(f"unknown household: {household_id}")

    @staticmethod
    def _member(household: Household, member_id: str) -> Member:
        for m in household.members:
            if m.member_id == member_id:
                return m
        raise KeyError(f"unknown member {member_id} in household {household.household_id}")
