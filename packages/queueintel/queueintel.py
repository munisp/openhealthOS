"""queueintel.py — the one doctor sees the right patients first.

Pre-arrival triage (howUdeyChat structured flows) assigns every patient
an outcome class before they reach the facility. The queue then serves
by clinical need, not arrival order — with a starvation guard so that
'routine' never means 'never'.

Doctrine:
- emergency > visit > routine, always.
- Within a class, arrival order (fair).
- STARVATION_CAP: a routine patient waiting longer than the cap is
  promoted into the visit class — documented, not silent. Clinic flow
  must never quietly abandon the back of the queue.
"""

STARVATION_CAP_MS = 3 * 3600 * 1000  # 3 hours
CLASS_ORDER = {"emergency": 0, "visit": 1, "routine": 2}


class Queue:
    def __init__(self, starvation_cap_ms=STARVATION_CAP_MS):
        self.cap = starvation_cap_ms
        self.waiting = []  # [{patient_ref, klass, arrived_at, promoted}]

    def arrive(self, patient_ref, klass, now):
        if klass not in CLASS_ORDER:
            raise ValueError(f"unknown triage class: {klass}")
        self.waiting.append({"patient_ref": patient_ref, "klass": klass,
                             "arrived_at": now, "promoted": False})

    def _effective(self, p, now):
        if p["klass"] == "routine" and now - p["arrived_at"] > self.cap:
            return "visit"  # starvation promotion
        return p["klass"]

    def next(self, now):
        if not self.waiting:
            return None
        for p in self.waiting:
            eff = self._effective(p, now)
            if eff != p["klass"]:
                p["promoted"] = True  # documented on the record
        self.waiting.sort(key=lambda p: (CLASS_ORDER[self._effective(p, now)],
                                         p["arrived_at"]))
        return self.waiting[0]

    def serve(self, now):
        nxt = self.next(now)
        if nxt:
            self.waiting.remove(nxt)
        return nxt
