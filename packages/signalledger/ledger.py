"""ledger.py — the signal precision ledger: did the alarm mean anything?

Every surveillance signal — a Scout uptick, a verified EBS signal — gets
an OUTCOME label once the facts are known: verified case, false alarm,
or (after a deadline) unresolved. With those labels the platform can
answer the central surveillance trade-off with evidence instead of
argument: does a lower alert threshold catch outbreaks earlier at an
acceptable false-alarm cost?

The labels also feed policyeval directly: precision at a threshold is a
policy, and a threshold change is a candidate policy — provable on last
outbreak's log before it governs the next one.

Doctrine:
  - unresolved is a deadline, not a shrug: signals without outcomes past
    resolution_days are counted and reported as unresolved, and a ledger
    with too many unresolved says so loudly — precision computed on the
    resolved subset only, with the unresolved count printed next to it;
  - labels are append-only: a false-alarm label can be corrected to
    verified by a NEW entry citing the old; history is never rewritten.
"""
from __future__ import annotations

OUTCOMES = ("verified-case", "false-alarm", "duplicate")


class LedgerError(Exception):
    pass


class SignalLedger:
    def __init__(self, resolution_days: int = 14):
        self.resolution_days = resolution_days
        self.signals = {}  # id -> {source, day, threshold, outcome, corrections}

    def register(self, signal_id: str, source: str, day: str,
                 threshold: float) -> dict:
        if signal_id in self.signals:
            raise LedgerError(f"duplicate signal: {signal_id}")
        s = {"id": signal_id, "source": source, "day": day,
             "threshold": threshold, "outcome": None, "history": []}
        self.signals[signal_id] = s
        return s

    def label(self, signal_id: str, outcome: str, day: str, note: str):
        s = self._s(signal_id)
        if outcome not in OUTCOMES:
            raise LedgerError(f"outcome must be one of {OUTCOMES}")
        if not note.strip():
            raise LedgerError("labels require a note — the basis IS the "
                              "label")
        if s["outcome"] is not None:
            raise LedgerError(
                f"already labelled {s['outcome']} — use correct() to "
                "append a revision, history is never rewritten")
        s["outcome"] = outcome
        s["history"].append(("labelled", outcome, day, note))

    def correct(self, signal_id: str, outcome: str, day: str, note: str):
        s = self._s(signal_id)
        if s["outcome"] is None:
            raise LedgerError("nothing to correct — label first")
        if outcome not in OUTCOMES:
            raise LedgerError(f"outcome must be one of {OUTCOMES}")
        s["history"].append(("corrected", s["outcome"], "->", outcome,
                             day, note))
        s["outcome"] = outcome

    def sweep_unresolved(self, today: str) -> list[str]:
        return sorted(s["id"] for s in self.signals.values()
                      if s["outcome"] is None
                      and _days(s["day"], today) > self.resolution_days)

    def precision_report(self, today: str) -> dict:
        """Precision by source, with unresolved counted honestly. A
        threshold change evaluated on a log that ignores unresolved
        signals is exactly the lie this report refuses to tell."""
        unresolved = self.sweep_unresolved(today)
        by_source = {}
        for s in self.signals.values():
            if s["outcome"] is None:
                continue
            b = by_source.setdefault(s["source"], {"verified": 0,
                                                   "total": 0})
            b["total"] += 1
            if s["outcome"] == "verified-case":
                b["verified"] += 1
        for b in by_source.values():
            b["precision"] = round(b["verified"] / b["total"], 3)
        return {"by_source": by_source,
                "unresolved_count": len(unresolved),
                "unresolved_ids": unresolved,
                "warning": ("precision computed on resolved subset only; "
                            f"{len(unresolved)} signals past deadline "
                            "without outcomes" if unresolved else "")}

    def to_policyeval_events(self, threshold: float) -> list[dict]:
        """Resolved signals as decision events for policyeval: action =
        'alerted', context = source, reward = 1 iff verified. Propensity
        must come from the alerting policy's exploration regime — this
        ledger refuses to invent it; callers supply it."""
        out = []
        for s in self.signals.values():
            if s["outcome"] is None:
                continue
            out.append({"context": s["source"], "action": "alerted",
                        "propensity": None,  # caller must fill honestly
                        "reward": 1.0 if s["outcome"] == "verified-case"
                                  else 0.0})
        return out

    def _s(self, sid):
        if sid not in self.signals:
            raise LedgerError(f"no such signal: {sid}")
        return self.signals[sid]


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
