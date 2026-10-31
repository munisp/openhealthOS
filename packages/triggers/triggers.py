"""triggers.py — vitals-driven event rules engine.

Readings arrive via vitals.py; rules here decide when a reading (or a
trend) becomes an EVENT. Events draft escalations — they do not
execute. A triggered event goes to a named human's worklist with an
acknowledgement tracker; an unacknowledged critical event escalates
again. The alert is never the action.

Rule kinds:
- threshold:  single reading breaches a bound (SpO2 < 92)
- sustained:  breach persists across N readings (HTN: BP >= 140/90 x3)
- trend:      rate-of-change over a window (weight dropping, SpO2
              falling even while still in normal range)

Doctrine:
- DRAFT, DON'T EXECUTE: a rule firing creates an event for a human,
  never an autonomous clinical action. Cooldowns prevent alert
  storms; unacknowledged criticals re-escalate.
- STALE-CALIBRATION READINGS DOWNGRADE: a threshold breach on an
  unverified device fires as "confirm-with-verified-device" — flagged,
  not trusted.
- SILENCE RULES EXIST TOO: "no BP reading in 7 days from a known
  hypertensive" is a trigger — absence of data is data.
- EVERY FIRING IS AUDITABLE: rule id, rule version, the readings that
  fired it, and the eventual human disposition.
"""

COOLDOWN_DAYS = {"critical": 0, "high": 1, "moderate": 3}
REESCALATE_AFTER_DAYS = {"critical": 1, "high": 2, "moderate": 5}
SEVERITIES = ("critical", "high", "moderate")


class TriggerError(Exception):
    pass


class Rule:
    def __init__(self, rule_id, *, version, vital, kind, severity,
                 below=None, above=None, sustained_n=None,
                 window_days=None, drop_per_day=None, rise_per_day=None,
                 condition=None, action_hint):
        if version is None:
            raise TriggerError("rules must be versioned — an unversioned "
                               "rule firing is an unauditable firing")
        if severity not in SEVERITIES:
            raise TriggerError(f"unknown severity: {severity}")
        if kind not in ("threshold", "sustained", "trend", "silence"):
            raise TriggerError(f"unknown rule kind: {kind}")
        self.id = rule_id
        self.version = version
        self.vital = vital
        self.kind = kind
        self.severity = severity
        self.below = below
        self.above = above
        self.sustained_n = sustained_n
        self.window_days = window_days
        self.drop_per_day = drop_per_day
        self.rise_per_day = rise_per_day
        self.condition = condition      # e.g. "pneumonia", "hypertension"
        self.action_hint = action_hint  # what the human should consider

    def _breach(self, value):
        if self.below is not None and value < self.below:
            return f"{value} < {self.below}"
        if self.above is not None and value > self.above:
            return f"{value} > {self.above}"
        return None


class TriggerEngine:
    def __init__(self, vitals_ingest):
        self.vitals = vitals_ingest
        self.rules = {}
        self.events = []            # append-only
        self._last_fired = {}       # (rule, patient) -> day
        self._n = 0

    def add_rule(self, rule):
        key = rule.id
        if key in self.rules and self.rules[key].version == rule.version:
            raise TriggerError(f"rule {key} v{rule.version} already exists")
        self.rules[key] = rule  # newer version replaces
        return rule.id

    def evaluate(self, patient_id, vital, *, day, condition=None):
        """Run all rules for this vital against the patient's series.
        Returns newly fired events."""
        fired = []
        series = self.vitals.series(patient_id, vital)
        for rule in self.rules.values():
            if rule.vital != vital:
                continue
            if rule.condition and condition and rule.condition != condition:
                continue
            hit, evidence = self._check(rule, series, day)
            if not hit:
                continue
            ev = self._fire(rule, patient_id, day, evidence, series)
            if ev:
                fired.append(ev)
        return fired

    def check_silence(self, patient_id, *, day, enrolled_vitals, condition=None):
        """Silence rules: expected vitals not seen within window_days."""
        fired = []
        for rule in self.rules.values():
            if rule.kind != "silence":
                continue
            if rule.vital not in enrolled_vitals:
                continue
            if rule.condition and condition and rule.condition != condition:
                continue
            latest = self.vitals.latest(patient_id, rule.vital)
            gap = day - latest["day"] if latest else None
            if gap is None or gap > rule.window_days:
                ev = self._fire(rule, patient_id, day,
                                {"reason": "no reading in window",
                                 "days_since_last": gap},
                                stale=False)
                if ev:
                    fired.append(ev)
        return fired

    def acknowledge(self, event_id, *, day, by, note=""):
        ev = self._event(event_id)
        if ev.get("acknowledged"):
            raise TriggerError(f"event {event_id} already acknowledged")
        ev["acknowledged"] = {"day": day, "by": by, "note": note}
        return dict(ev)

    def unacknowledged(self, as_of):
        """Unacked events past their re-escalation window — the second
        bell. Sorted critical-first."""
        out = []
        for ev in self.events:
            if ev.get("acknowledged"):
                continue
            age = as_of - ev["day"]
            if age >= REESCALATE_AFTER_DAYS[ev["severity"]]:
                out.append(dict(ev, days_unacknowledged=age,
                                reescalate=True))
        sev_rank = {"critical": 0, "high": 1, "moderate": 2}
        out.sort(key=lambda e: (sev_rank[e["severity"]],
                                -e["days_unacknowledged"]))
        return out

    def _check(self, rule, series, day):
        if not series or rule.kind == "silence":
            return False, None
        if rule.kind == "threshold":
            latest = series[-1]
            b = rule._breach(latest["valueQuantity"]["value"])
            if b:
                stale = latest.get("flag") == "calibration-stale"
                return True, {"reading": latest, "breach": b, "stale": stale}
        elif rule.kind == "sustained":
            n = rule.sustained_n or 2
            recent = series[-n:]
            if len(recent) < n:
                return False, None
            if all(rule._breach(o["valueQuantity"]["value"]) for o in recent):
                return True, {"readings": [o["valueQuantity"]["value"]
                                           for o in recent],
                              "stale": any(o.get("flag") for o in recent)}
        elif rule.kind == "trend":
            window = [o for o in series
                      if o["day"] >= day - rule.window_days]
            if len(window) < 2:
                return False, None
            first, last = window[0], window[-1]
            span = max(last["day"] - first["day"], 1)
            rate = (last["valueQuantity"]["value"]
                    - first["valueQuantity"]["value"]) / span
            if rule.drop_per_day is not None and rate <= -rule.drop_per_day:
                return True, {"rate_per_day": round(rate, 3),
                              "from": first["valueQuantity"]["value"],
                              "to": last["valueQuantity"]["value"]}
            if rule.rise_per_day is not None and rate >= rule.rise_per_day:
                return True, {"rate_per_day": round(rate, 3),
                              "from": first["valueQuantity"]["value"],
                              "to": last["valueQuantity"]["value"]}
        return False, None

    def _fire(self, rule, patient_id, day, evidence, series_or_stale=None,
              stale=False):
        key = (rule.id, patient_id)
        last = self._last_fired.get(key)
        if last is not None and day - last < COOLDOWN_DAYS[rule.severity]:
            return None
        self._last_fired[key] = day
        self._n += 1
        if evidence.get("stale") if isinstance(evidence, dict) else stale:
            severity = "moderate"
            action = f"confirm with a verified device, then: {rule.action_hint}"
        else:
            action = rule.action_hint
            severity_ = rule.severity
            severity = severity_
        ev = {"id": f"EVT-{self._n}", "rule_id": rule.id,
              "rule_version": rule.version, "patient_id": patient_id,
              "vital": rule.vital, "day": day, "severity": severity,
              "kind": rule.kind, "evidence": evidence,
              "action_hint": action,
              "status": "drafted-for-human",
              "note": "a trigger drafts an escalation; a human confirms it"}
        self.events.append(ev)
        return dict(ev)

    def _event(self, eid):
        for ev in self.events:
            if ev["id"] == eid:
                return ev
        raise TriggerError(f"unknown event: {eid}")
