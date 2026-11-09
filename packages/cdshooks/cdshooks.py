"""cdshooks.py — clean-room CDS Hooks service layer (ADR-0060).

HealthChain (Apache-2.0) proved the demand: decision support reaches
clinicians through CDS Hooks, the one protocol Epic, Cerner, and
Medplum all speak. We are the EHR, so we don't buy their gateway —
but our decision logic (NEWS2, interaction checks, renal dosing) had
NO standard exposure: it computed in packages and surfaced nowhere a
viewer could consume. This module is that exposure, clean-room,
stdlib only.

CDS Hooks 2.0 conventions honoured:
  - hook names are declared data (patient-view, order-select,
    order-sign); an unknown hook is refused, not defaulted
  - prefetch is a CONTRACT: a service declares what it needs; a call
    missing a prefetch key is refused with the missing keys named —
    a card computed from half a record is a wrong card wearing a
    confident summary
  - cards carry summary + indicator + source; a card without a
    summary is not a card
  - services are four-eyes RATIFIED before discovery: an unratified
    service is invisible to discovery and refuses to run — decision
    support nobody approved is decision support nobody owns
  - every call is audit-logged with a hash chain: who asked, which
    service, what came back. Silence and refusal are both recorded
  - adapters never fabricate: if the underlying package raises, the
    call fails with the reason — a 502 with a reason, never an
    invented card
"""

from __future__ import annotations

import hashlib
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "earlywarn"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "drugkb"))

import drugkb
import earlywarn

VERSION = "cdshooks@1.0.0"

HOOKS = ("patient-view", "order-select", "order-sign",
         "encounter-discharge")
INDICATORS = ("info", "warning", "critical")


class CDSError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# ------------------------------------------------------------- cards

def make_card(*, summary, indicator, source_label, detail=None,
              suggestions=None):
    if not (summary or "").strip():
        raise CDSError("a card without a summary is not a card")
    if indicator not in INDICATORS:
        raise CDSError(f"unknown indicator: {indicator}")
    if not (source_label or "").strip():
        raise CDSError("a card without a source is an opinion")
    card = {"summary": summary.strip(), "indicator": indicator,
            "source": {"label": source_label.strip()}}
    if detail:
        card["detail"] = detail
    if suggestions:
        card["suggestions"] = list(suggestions)
    return card


# ---------------------------------------------------------- services

def _news2_service(context, prefetch):
    vitals = prefetch["vitals"]
    result = earlywarn.news2(
        rr=vitals["rr"], spo2=vitals["spo2"], temp_c=vitals["temp_c"],
        sbp=vitals["sbp"], hr=vitals["hr"], avpu=vitals["avpu"],
        on_oxygen=vitals.get("on_oxygen", False),
        spo2_scale2=vitals.get("spo2_scale2", False))
    indicator = {"low": "info", "medium": "warning",
                 "high": "critical"}[result["band"]]
    cards = [make_card(
        summary=f"NEWS2 {result['score']} — {result['band']} risk"
                + (" (single-parameter 3: "
                   + ", ".join(result["single_param_3"]) + ")"
                   if result["single_param_3"] else ""),
        indicator=indicator, source_label=result["version"],
        detail="Escalation criteria met." if result["escalate"] else
               "No escalation criterion met.")]
    return cards


def _interaction_service(context, prefetch):
    order = prefetch["order"]
    result = drugkb.check_order(
        order["drug"], dose_mg_per_day=order.get("dose_mg_per_day"),
        current_meds=order.get("current_meds", ()),
        allergies=order.get("allergies", ()))
    cards = []
    for f in result["findings"]:
        sev = f.get("severity", "info")
        indicator = {"contraindicated": "critical", "major": "warning",
                     "moderate": "warning"}.get(sev, "info")
        cards.append(make_card(
            summary=f"{f['kind']}: {f.get('note', '')}".strip(),
            indicator=indicator,
            source_label=f"drugkb ({f.get('severity', 'info')})"))
    return cards


def _renal_service(context, prefetch):
    order = prefetch["order"]
    verdict = drugkb.renal_verdict(order["drug"], prefetch["egfr"])
    if verdict is None:
        # unrated is not clearance — a stated empty hand, not silence
        return [make_card(
            summary=f"No renal rule on file for {order['drug']} — "
                    "unrated is not clearance",
            indicator="info", source_label="drugkb renal table")]
    indicator = {"avoid": "critical", "caution": "warning",
                 "clear": "info"}.get(verdict["verdict"], "warning")
    return [make_card(
        summary=f"Renal dosing ({verdict['verdict']}): "
                f"{verdict['note']}",
        indicator=indicator, source_label=verdict["rule_version"])]


BUILTIN_SERVICES = [
    {"id": "news2-early-warning", "hook": "patient-view",
     "title": "NEWS2 early-warning score on patient open",
     "prefetch": {"vitals": "latest seven NEWS2 parameters"},
     "fn": _news2_service},
    {"id": "drug-interaction-check", "hook": "order-sign",
     "title": "Allergy/duplication/interaction check on order",
     "prefetch": {"order": "drug, dose, current meds, allergies"},
     "fn": _interaction_service},
    {"id": "renal-dosing-check", "hook": "order-sign",
     "title": "Renal dosing verdict on order",
     "prefetch": {"order": "drug", "egfr": "latest eGFR"},
     "fn": _renal_service},
]


# ---------------------------------------------------------- registry

class CDSRegistry:
    def __init__(self):
        self._services = {}
        self._audit = []
        self._prev_hash = "genesis"

    def _log(self, entry):
        entry = dict(entry, prev=self._prev_hash)
        entry["hash"] = hashlib.sha256(_canon(entry)).hexdigest()
        self._prev_hash = entry["hash"]
        self._audit.append(entry)

    def propose(self, service, *, proposer):
        if not proposer:
            raise CDSError("a service needs a proposer")
        sid = service.get("id")
        if not sid:
            raise CDSError("a service needs an id")
        if service.get("hook") not in HOOKS:
            raise CDSError(f"unknown hook: {service.get('hook')}")
        if not service.get("prefetch"):
            raise CDSError("a service without a prefetch contract is "
                           "a service that improvises")
        if not callable(service.get("fn")):
            raise CDSError("a service needs a function")
        if sid in self._services:
            raise CDSError(f"duplicate service: {sid}")
        self._services[sid] = dict(service, status="proposed",
                                   proposer=proposer)
        return sid

    def ratify(self, sid, *, approver):
        svc = self._services.get(sid)
        if svc is None:
            raise CDSError(f"unknown service: {sid}")
        if approver == svc["proposer"]:
            raise CDSError("the hand that proposes never ratifies")
        svc["status"] = "ratified"
        return sid

    def load_builtins(self, *, proposer, approver):
        for svc in BUILTIN_SERVICES:
            self.propose(svc, proposer=proposer)
            self.ratify(svc["id"], approver=approver)

    def discovery(self):
        """CDS Hooks discovery: ratified services only."""
        return {"services": [
            {"id": s["id"], "hook": s["hook"], "title": s["title"],
             "prefetch": s["prefetch"]}
            for s in self._services.values()
            if s["status"] == "ratified"]}

    def call(self, sid, *, hook, context, prefetch, caller=None):
        svc = self._services.get(sid)
        if svc is None:
            raise CDSError(f"unknown service: {sid}")
        if svc["status"] != "ratified":
            self._log({"event": "call-refused", "service": sid,
                       "reason": f"status={svc['status']}",
                       "caller": caller})
            raise CDSError(
                f"service {sid} is {svc['status']} — decision support "
                "nobody approved is decision support nobody owns")
        if hook != svc["hook"]:
            raise CDSError(f"hook mismatch: service is on "
                           f"{svc['hook']}, called as {hook}")
        missing = [k for k in svc["prefetch"] if k not in prefetch]
        if missing:
            self._log({"event": "call-refused", "service": sid,
                       "reason": f"prefetch missing {missing}",
                       "caller": caller})
            raise CDSError(
                f"prefetch contract unmet: missing {missing} — "
                "a card from half a record is a wrong card")
        try:
            cards = svc["fn"](context, prefetch)
        except Exception as exc:
            self._log({"event": "call-failed", "service": sid,
                       "reason": str(exc), "caller": caller})
            raise CDSError(f"service {sid} failed: {exc}") from exc
        self._log({"event": "call-served", "service": sid,
                   "hook": hook, "cards": len(cards), "caller": caller})
        return {"cards": cards}

    def audit_trail(self):
        return list(self._audit)

    def verify_audit(self):
        prev = "genesis"
        for entry in self._audit:
            body = {k: v for k, v in entry.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            if hashlib.sha256(_canon(body)).hexdigest() != entry["hash"]:
                return False
            prev = entry["hash"]
        return True
