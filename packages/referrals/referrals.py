"""referrals.py — closed-loop referral register.

Every protocol pack in the platform ends some pathways with
"emergency-refer" — and until now the patient vanished at that word.
A referral that is sent but never received is a patient lost between
facilities; this module makes the loop visible.

Lifecycle: created -> dispatched -> received -> outcome recorded.
               \-> refused-by-receiving (still recorded, still counted)

Doctrine:
- A REFERRAL IS NOT DONE UNTIL THE RECEIVING FACILITY CONFIRMS.
  "Sent" means nothing; overdue = no confirmation within the window.
- EMERGENCY WINDOW IS SHORT: emergencies overdue after 1 day,
  urgent after 3, routine after 7.
- COUNTER-REFERRAL CLOSES THE LOOP BACK: the receiving facility's
  feedback (diagnosis, treatment, instructions) is a first-class
  record, not a phone call nobody logged.
- Denominators visible: completion rates always ship with counts.
"""

WINDOWS = {"emergency": 1, "urgent": 3, "routine": 7}
STATES = ("created", "dispatched", "received", "refused-by-receiving")


class ReferralError(Exception):
    pass


class ReferralRegister:
    def __init__(self):
        self.referrals = {}
        self._n = 0

    def create(self, *, patient_id, from_facility, to_facility, reason,
               urgency, day, pre_referral_treatment=None):
        if urgency not in WINDOWS:
            raise ReferralError(f"unknown urgency: {urgency}")
        if from_facility == to_facility:
            raise ReferralError("self-referral is not a referral")
        self._n += 1
        rid = f"REF-{self._n}"
        self.referrals[rid] = {
            "id": rid, "patient_id": patient_id,
            "from": from_facility, "to": to_facility, "reason": reason,
            "urgency": urgency, "created_day": day,
            "pre_referral_treatment": pre_referral_treatment,
            "state": "created", "history": [("created", day)],
        }
        return dict(self.referrals[rid])

    def dispatch(self, rid, *, day, transport=None):
        r = self._ref(rid)
        self._require_state(r, "created")
        r["transport"] = transport
        self._set(r, "dispatched", day)
        return dict(r)

    def receive(self, rid, *, day, receiving_clinician):
        """The moment the loop closes: patient physically confirmed."""
        r = self._ref(rid)
        self._require_state(r, "dispatched")
        r["received_by"] = receiving_clinician
        self._set(r, "received", day)
        return dict(r)

    def refuse(self, rid, *, day, reason):
        """Receiving facility cannot take the patient — recorded loudly,
        the referral stays open for re-routing."""
        r = self._ref(rid)
        self._require_state(r, "dispatched")
        r["refusal_reason"] = reason
        self._set(r, "refused-by-receiving", day)
        return dict(r)

    def reroute(self, rid, *, to_facility, day):
        r = self._ref(rid)
        if r["state"] not in ("refused-by-receiving", "created"):
            raise ReferralError(f"cannot reroute from state {r['state']}")
        r["to"] = to_facility
        self._set(r, "dispatched" if r["state"] == "refused-by-receiving"
                  else "created", day)
        return dict(r)

    def record_outcome(self, rid, *, day, diagnosis=None, treatment=None,
                       feedback=None, counter_referral=False):
        r = self._ref(rid)
        self._require_state(r, "received")
        r["outcome"] = {"day": day, "diagnosis": diagnosis,
                        "treatment": treatment, "feedback": feedback,
                        "counter_referral": bool(counter_referral)}
        return dict(r)

    def overdue(self, as_of):
        """Dispatched (or refused) referrals past their urgency window —
        the lost-patient list."""
        out = []
        for r in self.referrals.values():
            if r["state"] not in ("created", "dispatched",
                                  "refused-by-receiving"):
                continue
            last_day = r["history"][-1][1]
            age = as_of - last_day
            window = WINDOWS[r["urgency"]]
            if age > window:
                out.append({"id": r["id"], "patient_id": r["patient_id"],
                            "urgency": r["urgency"], "state": r["state"],
                            "days_overdue": age - window,
                            "from": r["from"], "to": r["to"]})
        out.sort(key=lambda x: (WINDOWS[x["urgency"]], -x["days_overdue"]))
        return out

    def stats(self, as_of):
        total = len(self.referrals)
        received = sum(1 for r in self.referrals.values()
                       if r["state"] == "received")
        refused = sum(1 for r in self.referrals.values()
                      if r["state"] == "refused-by-receiving")
        od = self.overdue(as_of)
        with_feedback = sum(1 for r in self.referrals.values()
                            if r.get("outcome", {}).get("feedback"))
        return {"as_of": as_of, "total": total, "received": received,
                "refused": refused, "open": total - received - refused,
                "overdue": len(od),
                "emergencies_overdue": sum(1 for o in od
                                           if o["urgency"] == "emergency"),
                "with_counter_referral_feedback": with_feedback,
                "note": "received/total only meaningful with the counts"}

    def _ref(self, rid):
        if rid not in self.referrals:
            raise ReferralError(f"unknown referral: {rid}")
        return self.referrals[rid]

    @staticmethod
    def _require_state(r, state):
        if r["state"] != state:
            raise ReferralError(
                f"{r['id']} is {r['state']}, expected {state}")

    @staticmethod
    def _set(r, state, day):
        r["state"] = state
        r["history"].append((state, day))
