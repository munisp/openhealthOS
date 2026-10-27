"""labtrack.py — laboratory sample turnaround: the clock on every result.

During an outbreak, the suspect-case pipeline lives and dies on the lab:
sample drawn -> in transit -> received -> resulted. A sample that exceeds
its expected window is not a statistic, it is an emergency — a suspect
case sitting in a ward without a result, or a lost sample nobody missed.

Doctrine:
  - lifecycle is ordered and append-only: a sample cannot be "received"
    before it was "in transit" — impossible timestamps refuse;
  - per-lab baselines are LEARNED (median of completed turnarounds), and
    a sample whispers when it exceeds threshold_hours beyond that
    baseline — small labs with few samples say "learning", not a fake
    ETA (the nudge doctrine, applied to logistics);
  - negative/positive results both close the sample; "lost" is an
    explicit terminal state with a required reason — a lost sample is a
    reportable event, matching contacttrace's doctrine for lost contacts.
"""
from __future__ import annotations

STAGES = ("collected", "in-transit", "received", "resulted")
MIN_BASELINE = 3  # completed samples before a lab gets a learned baseline


class LabTrackError(Exception):
    pass


def _h(ts: str) -> float:
    """ISO timestamp -> hours (monotonic enough for TAT arithmetic)."""
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() / 3600


class LabTracker:
    def __init__(self, whisper_margin_hours: float = 6.0):
        self.samples = {}
        self.margin = whisper_margin_hours

    def register(self, sample_id: str, case_ref: str, lab: str,
                 collected_at: str) -> dict:
        if sample_id in self.samples:
            raise LabTrackError(f"duplicate sample: {sample_id}")
        s = {"id": sample_id, "case": case_ref, "lab": lab,
             "stages": [("collected", collected_at)], "result": None,
             "terminal": None}
        self.samples[sample_id] = s
        return s

    def advance(self, sample_id: str, stage: str, at: str):
        s = self._s(sample_id)
        if s["terminal"]:
            raise LabTrackError(f"sample is {s['terminal']} — closed")
        order = {name: i for i, name in enumerate(STAGES)}
        if stage not in order:
            raise LabTrackError(f"unknown stage: {stage}")
        last_stage, last_at = s["stages"][-1]
        if order[stage] != order[last_stage] + 1:
            raise LabTrackError(
                f"stage {stage} cannot follow {last_stage} — the lifecycle "
                "is strictly sequential; a sample that skips a stage is a "
                "broken record, not a shortcut")
        if _h(at) < _h(last_at):
            raise LabTrackError(
                f"impossible timestamp: {stage} at {at} precedes "
                f"{last_stage} at {last_at}")
        s["stages"].append((stage, at))

    def result(self, sample_id: str, outcome: str, at: str):
        if outcome not in ("positive", "negative", "indeterminate"):
            raise LabTrackError(f"unknown outcome: {outcome}")
        s = self._s(sample_id)
        self.advance(sample_id, "resulted", at)
        s["result"] = outcome
        s["terminal"] = "resulted"

    def mark_lost(self, sample_id: str, reason: str, at: str):
        if not reason.strip():
            raise LabTrackError("a lost sample requires a reason — it is "
                                "a reportable event")
        s = self._s(sample_id)
        s["terminal"] = "lost"
        s["history_note"] = reason

    # ---------------------------------------------------------- baselines

    def _completed(self, lab: str):
        out = []
        for s in self.samples.values():
            if s["lab"] == lab and s["terminal"] == "resulted":
                t0 = s["stages"][0][1]
                t1 = s["stages"][-1][1]
                out.append(_h(t1) - _h(t0))
        return sorted(out)

    def baseline(self, lab: str) -> dict:
        tats = self._completed(lab)
        if len(tats) < MIN_BASELINE:
            return {"lab": lab, "state": "learning",
                    "completed": len(tats)}
        med = tats[len(tats) // 2] if len(tats) % 2 else \
            (tats[len(tats) // 2 - 1] + tats[len(tats) // 2]) / 2
        return {"lab": lab, "state": "ready",
                "median_hours": round(med, 1),
                "p90_hours": round(tats[int(0.9 * (len(tats) - 1))], 1),
                "completed": len(tats)}

    def overdue(self, now: str) -> list[dict]:
        """Open samples exceeding (lab baseline + margin), or a global
        floor for learning labs. Sorted longest-waiting first."""
        out = []
        for s in self.samples.values():
            if s["terminal"]:
                continue
            waited = _h(now) - _h(s["stages"][0][1])
            base = self.baseline(s["lab"])
            limit = (base["median_hours"] if base["state"] == "ready"
                     else 48.0) + self.margin
            if waited > limit:
                out.append({"sample": s["id"], "case": s["case"],
                            "lab": s["lab"], "waiting_hours": round(waited, 1),
                            "limit_hours": round(limit, 1),
                            "stage": s["stages"][-1][0]})
        return sorted(out, key=lambda x: -x["waiting_hours"])

    def _s(self, sid):
        if sid not in self.samples:
            raise LabTrackError(f"no such sample: {sid}")
        return self.samples[sid]
