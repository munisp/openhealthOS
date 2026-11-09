
"""shiftshield.py — clinician fatigue guard.

Rostering treats clinicians as interchangeable hours; fatigue treats
them as humans with a degradation curve. This module scores proposed
rotas against declared fatigue limits — consecutive nights, weekly
hours, minimum rest — and refuses assignments that cross them. The
limits are policy: ratified, visible, and the refusal names the exact
limit breached.

Doctrine:
- LIMITS ARE RATIFIED POLICY: no default is silently safe; the
  registry declares max weekly hours, max consecutive nights, and
  minimum rest between shifts.
- REFUSAL NAMES THE LIMIT: 'rejected: exceeds 60h week (would be
  68h)' — a clinician can appeal a named rule, not a vibe.
- FATIGUE SCORE IS ADVISORY, LIMITS ARE HARD: the score informs; only
  declared limits refuse.
"""

VERSION = "shiftshield@1.0.0"


class ShieldError(Exception):
    pass


class LimitRegistry:
    def __init__(self):
        self._limits = None

    def ratify(self, *, max_weekly_hours, max_consecutive_nights,
               min_rest_hours, ratified_by):
        if not ratified_by:
            raise ShieldError("limits need a named ratifier")
        if max_weekly_hours <= 0 or min_rest_hours < 0:
            raise ShieldError("implausible limits")
        self._limits = {"max_weekly_hours": max_weekly_hours,
                        "max_consecutive_nights": max_consecutive_nights,
                        "min_rest_hours": min_rest_hours,
                        "ratified_by": ratified_by}

    def limits(self):
        if self._limits is None:
            raise ShieldError("no ratified fatigue limits — scheduling "
                              "without limits is refused")
        return self._limits


class Shift:
    def __init__(self, *, day, start_h, end_h, night=False):
        if end_h <= start_h:
            raise ShieldError("shift end must exceed start")
        self.day, self.start_h, self.end_h, self.night =             day, start_h, end_h, night
        self.hours = end_h - start_h


class Rota:
    """Assignments: clinician -> [Shift]. check() returns ok or the
    named refusal; fatigue_score() is advisory."""

    def __init__(self, registry):
        self.registry = registry
        self._shifts = {}

    def assign(self, clinician, shift):
        """Refuses loudly when a ratified limit would be breached."""
        lim = self.registry.limits()
        current = list(self._shifts.get(clinician, []))
        # weekly hours (7-day window ending at shift day)
        week = [s for s in current if shift.day - 6 <= s.day <= shift.day]
        week_hours = sum(s.hours for s in week) + shift.hours
        if week_hours > lim["max_weekly_hours"]:
            raise ShieldError(
                f"rejected: exceeds {lim['max_weekly_hours']}h week "
                f"(would be {week_hours}h)")
        # rest between shifts
        for s in current:
            gap = (shift.day - s.day) * 24 + (shift.start_h - s.end_h)
            if s.day == shift.day:
                gap = shift.start_h - s.end_h
            if 0 <= gap < lim["min_rest_hours"] or (
                    s.day == shift.day and shift.start_h < s.end_h):
                raise ShieldError(
                    f"rejected: only {gap}h rest after {s.day}/{s.end_h}h "
                    f"(minimum {lim['min_rest_hours']}h)")
        # consecutive nights
        if shift.night:
            streak = 1
            d = shift.day - 1
            nights = {s.day for s in current if s.night}
            while d in nights:
                streak += 1
                d -= 1
            if streak > lim["max_consecutive_nights"]:
                raise ShieldError(
                    f"rejected: would be night {streak} in a row "
                    f"(limit {lim['max_consecutive_nights']})")
        current.append(shift)
        self._shifts[clinician] = current

    def fatigue_score(self, clinician, *, window_days=14):
        """Advisory 0-100: hours + nights weighted. Never refuses."""
        shifts = self._shifts.get(clinician, [])
        hours = sum(s.hours for s in shifts)
        nights = sum(1 for s in shifts if s.night)
        return min(100, round(hours * 0.8 + nights * 4))
