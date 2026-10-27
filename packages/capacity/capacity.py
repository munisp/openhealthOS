"""capacity.py — the isolation capacity board.

During an outbreak the question "where can this patient go SAFELY?" is
usually answered by phone calls. This board makes it a query: every
isolation/treatment centre attests its beds, PPE days-on-hand, and
staffing; stale attestations degrade honestly (the referral-router
doctrine) — a centre whose numbers are 90 days old is "unverified",
never silently counted as available.

Capacity is attested, never self-reported-forever: an outbreak week
requires re-attestation every 7 days when active (RE attest cadence is
a policy parameter; the honest default is conservative).
"""
from __future__ import annotations

STALE_DAYS = 7  # outbreak cadence: weekly re-attestation


class CapacityError(Exception):
    pass


class CapacityBoard:
    def __init__(self, stale_days: int = STALE_DAYS):
        self.stale_days = stale_days
        self.centres = {}  # id -> {attestations: [...]}

    def attest(self, centre_id: str, beds_total: int, beds_free: int,
               ppe_days: float, staff_on_duty: int, day: str,
               attester: str) -> dict:
        if beds_free > beds_total:
            raise CapacityError("free beds cannot exceed total beds")
        if beds_total < 0 or ppe_days < 0 or staff_on_duty < 0:
            raise CapacityError("negative capacity is not capacity")
        if not attester.strip():
            raise CapacityError("attester identity required")
        rec = {"day": day, "beds_total": beds_total, "beds_free": beds_free,
               "ppe_days": ppe_days, "staff_on_duty": staff_on_duty,
               "attester": attester}
        self.centres.setdefault(centre_id, []).append(rec)
        return rec

    def _latest(self, centre_id: str):
        recs = self.centres.get(centre_id)
        if not recs:
            raise CapacityError(f"no attestations for {centre_id}")
        return recs[-1]

    def query(self, day: str) -> dict:
        """The board as of `day`: fresh attestations are usable, stale
        ones degrade to unverified with the number still visible —
        a 7-day-old '12 free beds' is a rumour, reported as one."""
        ready, stale = [], []
        for cid in sorted(self.centres):
            r = self._latest(cid)
            age = _days(r["day"], day)
            row = {"centre": cid, "beds_free": r["beds_free"],
                   "ppe_days": r["ppe_days"],
                   "staff_on_duty": r["staff_on_duty"],
                   "attested": r["day"], "age_days": age}
            if age > self.stale_days:
                row["verified"] = False
                stale.append(row)
            else:
                row["verified"] = True
                ready.append(row)
        return {"ready": ready, "unverified_stale": stale,
                "total_verified_beds": sum(r["beds_free"] for r in ready)}

    def where(self, day: str, need_beds: int = 1,
              min_ppe_days: float = 1.0) -> dict:
        """Placement query: verified centres that can actually take a
        patient — bed AND PPE AND staff, not just a number on a poster."""
        board = self.query(day)
        fits = [r for r in board["ready"]
                if r["beds_free"] >= need_beds
                and r["ppe_days"] >= min_ppe_days
                and r["staff_on_duty"] > 0]
        return {"ok": bool(fits), "options": fits,
                "stale_noted": len(board["unverified_stale"])}


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
