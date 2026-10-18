#!/usr/bin/env python3
"""CMMS — equipment assets, preventive maintenance, downtime (ADR-0022).

The oxygen plant that dies unnoticed is a clinical incident, not a
maintenance footnote. This package tracks the assets a facility
depends on — oxygen plants, generators, cold-chain fridges, autoclaves,
ambulances as maintainable assets — with three guarantees:

  - preventive maintenance is scheduled, not remembered: every asset
    has an interval; overdue work orders surface as a worklist;
  - downtime is counted: assets report down/up with reasons, and
    availability per asset is a number the LGA can see;
  - every state change names who (named-actor doctrine).

ERPNext Assets remains the composed book of record for depreciation
and procurement (ADR-0005) — this package is the operational hot path
at the facility: what is broken, what is due, what is down right now.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

CRITICALITY = ("critical", "essential", "standard")


class CMMSError(Exception):
    pass


@dataclass
class WorkOrder:
    wo_id: str
    asset_id: str
    kind: str            # preventive | repair
    due: str             # ISO date
    status: str = "open"  # open | done | cancelled
    note: str = ""
    done_at: str = ""
    actor: str = ""


@dataclass
class Asset:
    asset_id: str
    name: str
    kind: str            # oxygen-plant | generator | fridge | autoclave | vehicle | ...
    facility: str
    criticality: str = "standard"
    pm_interval_days: int = 90
    status: str = "up"   # up | down
    down_since: str = ""
    down_reason: str = ""
    downtime_days: float = 0.0
    last_pm: str = ""


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


class CMMS:
    def __init__(self):
        self.assets: dict[str, Asset] = {}
        self.work_orders: dict[str, WorkOrder] = {}
        self._seq = 0

    def register(self, a: Asset, actor: str) -> None:
        if not a.asset_id or not a.name or not a.facility:
            raise CMMSError("asset_id, name and facility required")
        if a.criticality not in CRITICALITY:
            raise CMMSError(f"criticality must be one of {CRITICALITY}")
        if not actor:
            raise CMMSError("actor required")
        if a.asset_id in self.assets:
            raise CMMSError("asset already registered")
        self.assets[a.asset_id] = a

    def _next_id(self) -> str:
        self._seq += 1
        return f"WO-{self._seq:05d}"

    def schedule_pm(self, asset_id: str, from_date: str, actor: str) -> WorkOrder:
        """Next preventive work order from last PM (or registration)."""
        a = self._get(asset_id)
        base = _d(a.last_pm) if a.last_pm else _d(from_date)
        wo = WorkOrder(self._next_id(), asset_id, "preventive",
                       (base + timedelta(days=a.pm_interval_days)).isoformat(),
                       note=f"scheduled PM for {a.name}", actor=actor)
        self.work_orders[wo.wo_id] = wo
        return wo

    def report_down(self, asset_id: str, as_of: str, reason: str, actor: str) -> Asset:
        if not reason or not actor:
            raise CMMSError("downtime reports name why and who")
        a = self._get(asset_id)
        if a.status == "down":
            raise CMMSError("asset already down")
        a.status, a.down_since, a.down_reason = "down", as_of, reason
        # a down critical asset auto-opens a repair work order
        if a.criticality == "critical":
            wo = WorkOrder(self._next_id(), asset_id, "repair", as_of,
                           note=f"URGENT: {a.name} down — {reason}", actor=actor)
            self.work_orders[wo.wo_id] = wo
        return a

    def report_up(self, asset_id: str, as_of: str, actor: str) -> Asset:
        a = self._get(asset_id)
        if a.status != "down":
            raise CMMSError("asset is not down")
        a.downtime_days += (_d(as_of) - _d(a.down_since)).days
        a.status, a.down_since, a.down_reason = "up", "", ""
        return a

    def complete(self, wo_id: str, as_of: str, actor: str, note: str = "") -> WorkOrder:
        wo = self.work_orders.get(wo_id)
        if not wo:
            raise CMMSError("unknown work order")
        if wo.status != "open":
            raise CMMSError(f"work order is {wo.status}")
        wo.status, wo.done_at, wo.actor = "done", as_of, actor
        if note:
            wo.note += " | done: " + note
        if wo.kind == "preventive":
            self._get(wo.asset_id).last_pm = as_of
        return wo

    def overdue(self, as_of: str) -> list[WorkOrder]:
        return sorted((w for w in self.work_orders.values()
                       if w.status == "open" and _d(w.due) < _d(as_of)),
                      key=lambda w: w.due)

    def availability(self, facility: str, as_of: str, period_days: int = 90) -> list[dict]:
        """Availability % per asset over the period — downtime counts,
        including currently-down assets measured to `as_of`."""
        out = []
        for a in sorted(self.assets.values(), key=lambda x: x.asset_id):
            if a.facility != facility:
                continue
            down = a.downtime_days
            if a.status == "down":
                down += (_d(as_of) - _d(a.down_since)).days
            avail = max(0.0, 1.0 - down / period_days)
            out.append({"asset_id": a.asset_id, "name": a.name,
                        "criticality": a.criticality, "status": a.status,
                        "availability": round(avail, 4)})
        return out

    def _get(self, asset_id: str) -> Asset:
        if asset_id not in self.assets:
            raise CMMSError("unknown asset")
        return self.assets[asset_id]
