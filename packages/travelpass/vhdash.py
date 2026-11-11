"""vhdash — dashboard payloads for the demo surfaces (vhc-dash@1.0.0).

The demo board links scenes to surfaces; a surface needs data. These
exporters read the *pilot registries* — never the narrative dict — so a
dashboard shows what the system holds, not what the script claims. A
dashboard you cannot reconcile against the registry is a poster.

Three payloads, three surfaces:
  fund_payload     -> vhc-fund.html      (the five buckets, denominators)
  br10_payload     -> vhc-br10.html      (alerts, acks, response days)
  helpdesk_payload -> vhc-helpdesk.html  (tickets, SLA, breaches)

Every payload carries its own integrity stamp: a sha256 over the
canonical content, so the page can show the checksum it rendered — the
checksum is the contract, on the dashboard too.
"""

from __future__ import annotations

import os
import sys
import hashlib
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

DASH_VERSION = "vhc-dash@1.0.0"


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def fund_payload(fund, *, currency_symbol: str = "€") -> dict:
    """The five buckets with denominators: allocated, spent, balance per
    bucket, and the backstop state. The money is five monies — the page
    shows five, not one."""
    rep = fund.report()
    buckets = [{"bucket": b,
                "balance_minor": rep["balances_minor"][b]}
               for b in sorted(rep["balances_minor"])]
    return _stamp({
        "version": DASH_VERSION,
        "currency_symbol": currency_symbol,
        "mode": rep["mode"],
        "rules_version": rep["rules_version"],
        "allocated_minor": rep["allocated_minor"],
        "spent_minor": rep["spent_minor"],
        "entries": rep["entries"],
        "buckets": buckets,
        # the ledger keeps None for an inactive backstop — the payload
        # normalizes to a dict so the page never subscripts None
        "backstop": rep["backstop"] or {"active": False},
    })


def br10_payload(vreg) -> dict:
    """The BR-10 alert list: every alert named with facility, week,
    case count, and acknowledgement state with response days. An
    unacknowledged alert is a finding — the page sorts it first."""
    alerts = []
    for a in vreg.alerts.values():
        alerts.append({
            "alert_id": a["alert_id"],
            "facility": a.get("facility", "—"),
            "week": a.get("week", "—"),
            "cases": a.get("cases", 0),
            "acknowledged": a["acknowledged"],
        })
    acked = {a["alert_id"]: a for a in vreg.acks}
    for a in alerts:
        if a["alert_id"] in acked:
            a["ack_by"] = acked[a["alert_id"]].get("by", "—")
            a["response_days"] = acked[a["alert_id"]].get("response_days")
    alerts.sort(key=lambda a: (a["acknowledged"], -a["cases"]))
    rep = vreg.report()
    return _stamp({
        "version": DASH_VERSION,
        "alerts": alerts,
        "incidents": rep["incidents"],
        "alerts_open": rep["alerts_open"],
        "alerts_acknowledged": rep["alerts_acknowledged"],
        "ack_response_days": rep["ack_response_days"],
    })


def helpdesk_payload(desk) -> dict:
    """The helpdesk console: tickets by state, first-response SLA with
    its denominator, and open breaches. A late response is a named
    breach with an owner — the page lists them, not hides them."""
    rep = desk.report()
    tickets = []
    for t in desk.tickets.values():
        tickets.append({
            "ticket_id": t["ticket_id"],
            "channel": t.get("channel", "—"),
            "category": t.get("category", "—"),
            "priority": t.get("priority", "—"),
            "state": t["state"],
            "breaches": [b["clock"] for b in t.get("breaches", [])],
            "subject": t.get("subject", ""),
        })
    tickets.sort(key=lambda t: (t["state"] == "closed",
                                -len(t["breaches"])))
    return _stamp({
        "version": DASH_VERSION,
        "tickets_total": rep["tickets"],
        "by_state": rep["by_state"],
        "first_response_sla": rep["first_response_sla"],
        "open_breaches": rep["open_breaches"],
        "tickets": tickets,
    })


def export_dashboards(registries: dict, out_dir: str, *,
                      currency_symbol: str = "€") -> dict:
    """Write the three payloads into out_dir (the PWA directory, so the
    pages fetch them same-origin). Returns the paths written."""
    import os
    jobs = (
        ("vhc-fund.json", fund_payload(registries["fund"],
                                       currency_symbol=currency_symbol)),
        ("vhc-br10.json", br10_payload(registries["vreg"])),
        ("vhc-helpdesk.json", helpdesk_payload(registries["desk"])),
    )
    written = {}
    for name, payload in jobs:
        path = os.path.join(out_dir, name)
        payloadio.write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
        written[name] = path
    return written
