"""Internal-threat detection over audit streams (ADR-0063).

External hardening (APISIX DDoS, TLS floor, network default-deny) stops
outsiders; this watches INSIDERS — the harder problem in health systems.
Rules are data, run over the existing audit lakes (SHR audit, consent audit,
ledger events), and produce FINDINGS (auditor questions), never auto-bans.
Exit 1 on any finding: CI/daily-cron gate.

Threats covered:
  mass-export       bulk reads beyond clinical need (data theft staging)
  off-hours-bulk    bulk access outside facility hours
  breakglass-burst  break-glass used as a habit, not an emergency
  scope-escalation  repeated 403 probing (privilege exploration)
  dormant-sudden    dormant actor suddenly exporting
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from datetime import datetime

RULES = {
    "mass_export_threshold": 50,        # distinct patients by one actor/day
    "off_hours": (22, 5),               # 22:00-05:00 suspicious for bulk
    "breakglass_burst": 3,              # >3 break-glass/day by one actor
    "scope_probe_threshold": 10,        # 403s/day by one actor
    "dormant_days": 30,                 # no activity for 30d then bulk
}

def _day(ts): return ts[:10]
def _hour(ts):
    try: return datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
    except Exception: return 12  # unparseable -> treat as daytime (honest: not off-hours)

def analyze(events: list[dict], rules: dict | None = None) -> dict:
    """events: [{ts, actor, action, patient_id?, status?}]"""
    r = dict(RULES); r.update(rules or {})
    findings = []
    by_actor_day = defaultdict(list)
    for e in events:
        by_actor_day[(e.get("actor", "?"), _day(e.get("ts", "")))].append(e)

    for (actor, day), evs in sorted(by_actor_day.items()):
        reads = [e for e in evs if e.get("action") in ("read", "export", "bulk")]
        patients = {e.get("patient_id") for e in reads if e.get("patient_id")}
        if len(patients) > r["mass_export_threshold"]:
            findings.append({"rule": "mass-export", "actor": actor, "day": day,
                             "detail": f"{len(patients)} distinct patients in one day",
                             "severity": "critical"})
        lo, hi = r["off_hours"]
        offhour_bulk = [e for e in reads
                        if lo <= _hour(e.get("ts", "")) or _hour(e.get("ts", "")) < hi]
        if len(offhour_bulk) > r["mass_export_threshold"] // 2:
            findings.append({"rule": "off-hours-bulk", "actor": actor, "day": day,
                             "detail": f"{len(offhour_bulk)} bulk reads between {lo}:00-{hi}:00",
                             "severity": "major"})
        bg = [e for e in evs if e.get("break_glass")]
        if len(bg) > r["breakglass_burst"]:
            findings.append({"rule": "breakglass-burst", "actor": actor, "day": day,
                             "detail": f"{len(bg)} break-glass uses in one day",
                             "severity": "major"})
        denials = [e for e in evs if e.get("status") == 403]
        if len(denials) >= r["scope_probe_threshold"]:
            findings.append({"rule": "scope-escalation", "actor": actor, "day": day,
                             "detail": f"{len(denials)} denied requests — privilege probing?",
                             "severity": "major"})

    # dormant-sudden: per actor, gap then bulk
    by_actor = defaultdict(list)
    for e in events:
        by_actor[e.get("actor", "?")].append(e)
    for actor, evs in sorted(by_actor.items()):
        days = sorted({_day(e.get("ts", "")) for e in evs})
        for prev, cur in zip(days, days[1:]):
            gap = (datetime.fromisoformat(cur) - datetime.fromisoformat(prev)).days
            if gap >= r["dormant_days"]:
                bulk = [e for e in evs if _day(e.get("ts", "")) == cur
                        and e.get("action") in ("export", "bulk")]
                if len(bulk) > r["mass_export_threshold"] // 2:
                    findings.append({"rule": "dormant-sudden", "actor": actor, "day": cur,
                                     "detail": f"{gap}d dormant then {len(bulk)} bulk ops",
                                     "severity": "critical"})
    return {"findings": findings, "clean": not findings,
            "events_scanned": len(events)}

def main(argv):
    doc = json.load(sys.stdin)
    out = analyze(doc.get("events", []), doc.get("rules"))
    print(json.dumps(out, indent=2))
    return 0 if out["clean"] else 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
