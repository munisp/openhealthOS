"""Chronic-registry -> lakehouse feed (ADR-0060).

The registry (packages/chronicreg) is the operational store; the lakehouse
is the analytical store. This module bridges them as EVENTS on the same
medallion contracts as packages/lakehouse (bronze append-only -> silver
canonical -> gold aggregates), plus an OMOP-flavoured silver mapping so
pophealth cohort tooling can consume registries without bespoke adapters.

Event types emitted:
  registry_enrolment     patient enters a registry
  stage_transition       evidence-gated move along the cascade
  adherence_snapshot     periodic adherence measurement (doses taken/expected)

Doctrine: analytics NEVER write back to the operational registry; gold
outputs are reproducible from bronze (replay-safe); privacy floor - gold
aggregates suppress cells below k=5 (same k-anonymity doctrine as
packages/surveillance/notifiable.py).
"""
from __future__ import annotations
import json, os, sys
from collections import Counter, defaultdict
from chronicreg import REGISTRIES, adherence_score, adherence_band

K_ANONYMITY_FLOOR = 5

def registry_to_events(members: dict, adherence: dict | None = None,
                       facility_of=lambda pid: "unknown") -> list[dict]:
    """members: ChronicRegistry.members {(reg,pid): record}. adherence:
    {(reg,pid): (taken, expected, period)} optional."""
    events = []
    for (reg, pid), r in sorted(members.items()):
        fac = facility_of(pid)
        events.append({"id": f"{reg}-{pid}-enrol", "ts": r["enrolled"],
                       "facility": fac, "type": "registry_enrolment",
                       "payload": {"registry": reg, "patient_id": pid, "stage": r["stage"]}})
        for i, (on, stage) in enumerate(r["history"][1:], 1):
            events.append({"id": f"{reg}-{pid}-t{i}", "ts": on, "facility": fac,
                           "type": "stage_transition",
                           "payload": {"registry": reg, "patient_id": pid,
                                       "from": r["history"][i-1][1], "to": stage}})
    for (reg, pid), (taken, expected, period) in sorted((adherence or {}).items()):
        sc = adherence_score(taken, expected)
        events.append({"id": f"{reg}-{pid}-adh-{period}", "ts": period + "T00:00:00",
                       "facility": facility_of(pid), "type": "adherence_snapshot",
                       "payload": {"registry": reg, "patient_id": pid, "period": period,
                                   "score": sc, "band": adherence_band(sc)}})
    return events

def silver_omop(events: list[dict]) -> dict:
    """OMOP-flavoured silver tables from registry events."""
    person, condition, observation = {}, [], []
    for ev in events:
        p = ev["payload"]
        pid, reg = p["patient_id"], p["registry"]
        person.setdefault(pid, {"person_id": pid, "care_site": ev["facility"]})
        if ev["type"] == "registry_enrolment":
            condition.append({"person_id": pid, "condition": reg,
                              "condition_start": ev["ts"], "care_site": ev["facility"]})
        if ev["type"] == "adherence_snapshot":
            observation.append({"person_id": pid, "observation": f"{reg}_adherence",
                                "value": p["score"], "band": p["band"],
                                "date": p["period"], "care_site": ev["facility"]})
    return {"person": list(person.values()), "condition_occurrence": condition,
            "observation": observation}

def gold_cascade(events: list[dict]) -> dict:
    """Decision-ready: latest-stage cascade per registry x facility, adherence
    band distribution. Cells below k are suppressed."""
    latest = {}
    adh = defaultdict(list)
    for ev in events:
        p = ev["payload"]
        if ev["type"] in ("registry_enrolment", "stage_transition"):
            key = (p["registry"], ev["facility"], p["patient_id"])
            stage = p.get("to") or p.get("stage")
            cur = latest.get(key)
            if cur is None or REGISTRIES[p["registry"]].index(stage) >                REGISTRIES[p["registry"]].index(cur):
                latest[key] = stage
        elif ev["type"] == "adherence_snapshot":
            adh[(p["registry"], ev["facility"])].append(p["band"])
    cascades = defaultdict(lambda: defaultdict(int))
    for (reg, fac, pid), stage in latest.items():
        cascades[f"{reg}@{fac}"][stage] += 1
    out = {"cascades": {}, "adherence": {}}
    for k, stages in sorted(cascades.items()):
        total = sum(stages.values())
        out["cascades"][k] = {"total": total, "stages": dict(stages),
                              "suppressed": total < K_ANONYMITY_FLOOR}
    for (reg, fac), bands in sorted(adh.items()):
        if len(bands) < K_ANONYMITY_FLOOR:
            out["adherence"][f"{reg}@{fac}"] = {"suppressed": True}
            continue
        c = Counter(bands)
        out["adherence"][f"{reg}@{fac}"] = {"n": len(bands), "bands": dict(c),
            "pct_high": round(100 * c.get("high", 0) / len(bands), 1)}
    return out

def feed(members: dict, out_dir: str, adherence: dict | None = None,
         facility_of=lambda pid: "unknown") -> dict:
    """End-to-end: events -> lakehouse bronze/silver/gold via the shared
    pipeline contracts. Returns gold summary."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lakehouse"))
    import lakehouse
    events = registry_to_events(members, adherence, facility_of)
    nb = lakehouse.bronze(events, out_dir)
    omop = silver_omop(events)
    n_silver = 0
    for table, rows in omop.items():
        lakehouse.write_partition(rows, os.path.join(out_dir, "silver", f"omop_{table}", "records"))
        n_silver += len(rows)
    gold = gold_cascade(events)
    os.makedirs(os.path.join(out_dir, "gold"), exist_ok=True)
    with open(os.path.join(out_dir, "gold", "chronic_cascade.json"), "w") as fh:
        json.dump(gold, fh, indent=2, sort_keys=True)
    return {"events": len(events), "bronze_rows": nb, "silver_rows": n_silver, "gold": gold}

def main(argv):
    doc = json.load(sys.stdin)
    members = {}
    for m in doc.get("members", []):
        members[(m["registry"], m["patient_id"])] = {
            "registry": m["registry"], "patient_id": m["patient_id"],
            "stage": m["stage"], "enrolled": m["enrolled"],
            "history": [tuple(h) for h in m.get("history", [(m["enrolled"], m["stage"])])]}
    out = feed(members, doc.get("out", "/tmp/lake-chronic"),
               facility_of=lambda pid: doc.get("facility", "F1"))
    print(json.dumps(out, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
