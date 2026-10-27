"""mpdsr.py — maternal & perinatal death surveillance and response
(under-5 scope: stillbirths, neonatal, infant, and child deaths).

Every stillbirth and child death is notified, reviewed, and answered.
Nigeria's MPDSR mandate exists on paper; this is the executable chain:
notify (fast) -> review (structured: preventable factors, the three
delays) -> respond (named actions with owners) -> count (monthly
summary that never hides the unreviewed).

Doctrine:
  - notification is NEVER refused for lateness — a death reported on
    day 30 is still a death; lateness is FLAGGED (24h doctrine) and
    counted, because a registry that punishes late honesty gets lies;
  - reviews require a note and at least one delay classification —
    "no preventable factors" is a finding that must be argued, not a
    checkbox;
  - unreviewed deaths are first-class citizens of every summary — a
    dashboard of only reviewed deaths is a lie of omission;
  - response actions have owners and deadlines; the summary lists
    overdue actions next to the death counts.
"""
from __future__ import annotations

AGE_BANDS = ("stillbirth", "neonatal", "infant", "child-1-59m")
DELAYS = ("seeking-care", "reaching-care", "receiving-care")
PREVENTABLE_FACTORS = (
    "delayed-presentation", "transport-barrier", "no-money-for-care",
    "stockout-medication", "stockout-blood-or-oxygen", "staff-unavailable",
    "referral-delayed", "protocol-not-followed", "caregiver-declined-care",
    "none-argued",
)
NOTIFY_WINDOW_DAYS = 1  # 24h doctrine


class MPDSRError(Exception):
    pass


def age_band(age_days: int | None, stillbirth: bool = False) -> str:
    if stillbirth:
        return "stillbirth"
    if age_days is None:
        raise MPDSRError("age in days required for a liveborn death")
    if age_days < 0:
        raise MPDSRError("negative age")
    if age_days <= 28:
        return "neonatal"
    if age_days < 365:
        return "infant"
    if age_days < 1825:
        return "child-1-59m"
    raise MPDSRError("age 5y+ is outside the under-5 registry scope")


class DeathRegistry:
    def __init__(self):
        self.deaths = {}

    def notify(self, death_id: str, *, stillbirth: bool = False,
               age_days: int | None = None, place: str, facility: str,
               death_day: str, notified_day: str, reporter: str) -> dict:
        if death_id in self.deaths:
            raise MPDSRError(f"duplicate notification: {death_id}")
        if not reporter.strip() or not place.strip():
            raise MPDSRError("reporter and place are required")
        band = age_band(age_days, stillbirth)
        late = _days(death_day, notified_day) > NOTIFY_WINDOW_DAYS
        rec = {"id": death_id, "band": band, "place": place,
               "facility": facility, "death_day": death_day,
               "notified_day": notified_day, "reporter": reporter,
               "late_notification": late, "review": None}
        self.deaths[death_id] = rec
        return rec

    def review(self, death_id: str, *, reviewer: str, factors: list[str],
               delays: list[str], preventable: bool, note: str,
               actions: list[dict] | None = None, day: str) -> dict:
        rec = self._r(death_id)
        if rec["review"] is not None:
            raise MPDSRError("already reviewed — corrections append via "
                             "addendum, history is never rewritten")
        if not note.strip():
            raise MPDSRError("review note required")
        if not reviewer.strip():
            raise MPDSRError("reviewer identity required")
        bad_f = [f for f in factors if f not in PREVENTABLE_FACTORS]
        bad_d = [d for d in delays if d not in DELAYS]
        if bad_f:
            raise MPDSRError(f"unknown preventable factors: {bad_f}")
        if bad_d:
            raise MPDSRError(f"unknown delays: {bad_d}")
        if preventable and not factors:
            raise MPDSRError("a preventable death must name its factors")
        if not preventable and "none-argued" not in factors:
            raise MPDSRError(
                "a not-preventable verdict must argue it explicitly "
                "(factor 'none-argued') — absence of factors is not an "
                "argument")
        acts = []
        for a in (actions or []):
            if not a.get("owner") or not a.get("due_day") or not a.get("action"):
                raise MPDSRError("every action needs action, owner, due_day")
            acts.append({**a, "done": False})
        rec["review"] = {"reviewer": reviewer, "factors": factors,
                         "delays": delays, "preventable": preventable,
                         "note": note, "actions": acts, "day": day}
        return rec["review"]

    def addendum(self, death_id: str, note: str, day: str, by: str):
        rec = self._r(death_id)
        if rec["review"] is None:
            raise MPDSRError("review first, then addenda")
        if not note.strip():
            raise MPDSRError("addendum note required")
        rec["review"].setdefault("addenda", []).append(
            {"by": by, "day": day, "note": note})

    def complete_action(self, death_id: str, action_index: int, day: str):
        rec = self._r(death_id)
        rec["review"]["actions"][action_index]["done"] = day

    def summary(self, as_of: str) -> dict:
        by_band = {}
        preventable = 0
        unreviewed, late = [], []
        overdue_actions = []
        for d in self.deaths.values():
            by_band[d["band"]] = by_band.get(d["band"], 0) + 1
            if d["late_notification"]:
                late.append(d["id"])
            if d["review"] is None:
                unreviewed.append(d["id"])
                continue
            if d["review"]["preventable"]:
                preventable += 1
            for i, a in enumerate(d["review"]["actions"]):
                if not a["done"] and _days(a["due_day"], as_of) > 0:
                    overdue_actions.append({"death": d["id"], "index": i,
                                            "action": a["action"],
                                            "owner": a["owner"],
                                            "due": a["due_day"]})
        total = len(self.deaths)
        return {
            "total_deaths": total,
            "by_band": by_band,
            "reviewed": total - len(unreviewed),
            "unreviewed": sorted(unreviewed),
            "unreviewed_is_a_finding": bool(unreviewed),
            "preventable_reviewed": preventable,
            "late_notifications": sorted(late),
            "overdue_actions": overdue_actions,
        }

    def _r(self, did):
        if did not in self.deaths:
            raise MPDSRError(f"no such death: {did}")
        return self.deaths[did]


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
