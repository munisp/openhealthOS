"""resultloop.py — closed-loop result delivery.

A verified result that never reaches the referrer is a diagnosis in a
drawer. This module is the loop between 'report verified' and 'the
referrer acted on it':

  dispatch → queued → sent → delivered → acknowledged

Doctrine:
- THE PAYLOAD TRAVELS BY REFERENCE. The delivery carries a content_ref
  (report id + hash), never clinical content — the notification rail
  is not the record.
- 'SENT' IS NOT CARE. Sent is a network event; delivered is a device
  event; only a NAMED human acknowledgment closes the loop.
- ACKS OFF-CHANNEL ARE REAL. A phone call closes the loop too — but
  it is flagged 'acked-off-channel', because the rail should learn
  where it is bypassed.
- CRITICAL UNACKED CLIMBS A LADDER. referrer → facility head → on-call
  medical officer. The ladder drafts escalations; humans still act.
- THE CHANNEL IS AN HONEST SPIKE. No endpoint → deliveries stay
  'queued' and the stats say so; nothing claims 'sent' it cannot prove.
"""

ACK_SLA_DAYS = {"critical": 0, "urgent": 1, "routine": 3}
CHANNEL_STATES = ("queued", "sent", "delivered")


class LoopError(Exception):
    pass


class ChannelAdapter:
    """Honest spike: a notification rail endpoint (notification-service
    / whatsapp-gateway). Without an endpoint the adapter refuses to
    claim sends — 'queued' is the honest state when there is no rail."""

    def __init__(self, endpoint=None):
        self.endpoint = endpoint

    @property
    def available(self):
        return bool(self.endpoint)

    def send(self, delivery):
        if not self.available:
            raise LoopError(
                "no notification rail configured — the delivery stays "
                "queued; pretending otherwise is how results vanish")
        # real deployments POST {delivery_id, content_ref} to the rail;
        # the rail's own receipt confirms 'sent'
        return {"delivery_id": delivery["delivery_id"],
                "channel": self.endpoint, "claimed": "sent"}


class ResultLoop:
    def __init__(self, channel=None, deliveries=None):
        self.channel = channel or ChannelAdapter()
        self.deliveries = deliveries if deliveries is not None else {}
        self._n = max((int(k.split("-")[1]) for k in self.deliveries
                       if k.startswith("DLV-")), default=0)

    # ---- dispatch ----
    def dispatch(self, *, case_id, facility, referrer, critical, day,
                 content_ref, urgency=None):
        """One delivery per case (idempotent). Content by reference."""
        if not referrer:
            raise LoopError(
                "a result without a named recipient is a broadcast — "
                "refused")
        if not content_ref:
            raise LoopError(
                "no content reference — the rail carries pointers, "
                "not payloads; a pointerless delivery is a rumour")
        for d in self.deliveries.values():
            if d["case_id"] == case_id:
                return dict(d)  # idempotent: the loop exists already
        self._n += 1
        did = f"DLV-{self._n}"
        urgency = urgency or ("critical" if critical else "routine")
        if urgency not in ACK_SLA_DAYS:
            raise LoopError(f"unknown urgency: {urgency}")
        d = {"delivery_id": did, "case_id": case_id,
             "facility": facility, "referrer": referrer,
             "critical": bool(critical), "urgency": urgency,
             "content_ref": content_ref, "state": "queued",
             "timeline": [("queued", day, "dispatch")],
             "acknowledged": None, "escalations": []}
        if self.channel.available:
            receipt = self.channel.send(d)
            d["state"] = "sent"
            d["timeline"].append(("sent", day, receipt["channel"]))
        self.deliveries[did] = d
        return dict(d)

    # ---- channel state moves (forward only) ----
    def _move(self, delivery_id, state, *, day, receipt_ref):
        d = self._delivery(delivery_id)
        if d["acknowledged"]:
            raise LoopError("loop already closed — a closed loop does "
                            "not move backwards")
        cur = CHANNEL_STATES.index(d["state"]) \
            if d["state"] in CHANNEL_STATES else len(CHANNEL_STATES)
        if state not in CHANNEL_STATES:
            raise LoopError(f"unknown channel state: {state}")
        if CHANNEL_STATES.index(state) <= cur:
            raise LoopError(
                f"{d['state']} → {state} is backwards or a repeat — "
                "delivery state is forward-only, append-only")
        if not receipt_ref:
            raise LoopError(f"claiming '{state}' without a receipt "
                            "reference is a claim without evidence")
        d["state"] = state
        d["timeline"].append((state, day, receipt_ref))
        return dict(d)

    def mark_sent(self, delivery_id, *, day, receipt_ref):
        return self._move(delivery_id, "sent", day=day,
                          receipt_ref=receipt_ref)

    def mark_delivered(self, delivery_id, *, day, receipt_ref):
        return self._move(delivery_id, "delivered", day=day,
                          receipt_ref=receipt_ref)

    # ---- the loop closes only on a named human ----
    def acknowledge(self, delivery_id, *, day, by, delegate_reason=None):
        d = self._delivery(delivery_id)
        if d["acknowledged"]:
            raise LoopError("already acknowledged — a second ack is a "
                            "duplicate, not a confirmation")
        flags = []
        if by != d["referrer"]:
            if not delegate_reason:
                raise LoopError(
                    f"only {d['referrer']} acknowledges, or a named "
                    "delegate WITH a reason — unaccountable acks are "
                    "how critical results die")
            flags.append("delegate-ack")
        if d["state"] != "delivered":
            flags.append("acked-off-channel")
        d["acknowledged"] = {"day": day, "by": by,
                             "off_channel": d["state"] != "delivered",
                             "delegate": by != d["referrer"],
                             "delegate_reason": delegate_reason}
        d["state"] = "acknowledged"
        d["timeline"].append(("acknowledged", day, by))
        out = dict(d)
        out["flags"] = flags
        return out

    # ---- the loop watches itself ----
    def unclosed(self, as_of):
        """Open loops, most overdue first; criticals always on top."""
        out = []
        for d in self.deliveries.values():
            if d["acknowledged"]:
                continue
            opened = d["timeline"][0][1]
            sla = ACK_SLA_DAYS[d["urgency"]]
            overdue = (as_of - opened) - sla
            out.append({"delivery_id": d["delivery_id"],
                        "case_id": d["case_id"], "urgency": d["urgency"],
                        "state": d["state"], "days_open": as_of - opened,
                        "overdue": overdue > 0,
                        "days_overdue": max(0, overdue)})
        rank = {"critical": 0, "urgent": 1, "routine": 2}
        out.sort(key=lambda x: (rank[x["urgency"]], -x["days_open"]))
        return out

    def escalate(self, as_of):
        """The ladder: overdue criticals climb referrer → facility head
        → on-call MO. One rung per day at most; once the ladder is
        exhausted the top rung re-fires daily. Drafts events; humans
        still act."""
        LADDER = ("facility-head", "on-call-mo")
        fired = []
        for u in self.unclosed(as_of):
            if not u["overdue"] or u["urgency"] != "critical":
                continue
            d = self._delivery(u["delivery_id"])
            if d["escalations"]:
                last = d["escalations"][-1]
                if as_of - last["day"] < 1:
                    continue  # one rung per day, not an alarm storm
            rung = min(len(d["escalations"]), len(LADDER) - 1)
            target = LADDER[rung]
            ev = {"day": as_of, "to": target,
                  "reason": f"critical result unacknowledged "
                            f"{u['days_overdue']}d past SLA",
                  "status": "drafted-for-human"}
            d["escalations"].append(ev)
            fired.append({"delivery_id": d["delivery_id"], **ev})
        return fired

    def loop_stats(self):
        """Denominators always: verified results vs closed loops."""
        total = len(self.deliveries)
        acked = sum(1 for d in self.deliveries.values()
                    if d["acknowledged"])
        crit = [d for d in self.deliveries.values() if d["critical"]]
        crit_acked = sum(1 for d in crit if d["acknowledged"])
        times = [d["acknowledged"]["day"] - d["timeline"][0][1]
                 for d in self.deliveries.values() if d["acknowledged"]]
        return {"dispatched": total, "acknowledged": acked,
                "open": total - acked,
                "critical": {"dispatched": len(crit),
                             "acknowledged": crit_acked},
                "median_days_to_ack": (
                    sorted(times)[len(times) // 2] if times else None),
                "note": "an unacked critical is an open loop with a "
                        "clock running; the stats never count 'sent' "
                        "as 'received'"}

    def _delivery(self, did):
        if did not in self.deliveries:
            raise LoopError(f"unknown delivery: {did}")
        return self.deliveries[did]
