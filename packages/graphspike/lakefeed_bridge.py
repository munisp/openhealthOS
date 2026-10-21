"""lakefeed_bridge.py — silver-layer events -> national graph (ADR-0064).

The operational graph AGE would serve is not a new data source: it is a
projection of the lakehouse silver layer (ADR-0060). This bridge converts
silver JSONL events into the graphspike node/edge model, so the SAME
pipeline that feeds analytics also feeds relationship queries — one
contract, two projections. No event ever flows backwards: the graph is
read-only for queries, rebuilt from silver, never a system of record.

Event shapes consumed (silver contract):
  claim_filed       {claim_id, facility_id, prescriber_id, amount, paid_to}
  encounter_held    {encounter_id, facility_id, kind, attendees: [patient_ref]}
  referral_made     {from_facility, to_facility, count}
  staff_posting     {prescriber_id, facility_id}
"""
import csv
import json
import os

EVENT_BUILDERS = ("claim_filed", "encounter_held", "referral_made", "staff_posting")


def bridge(events):
    """Events (iterable of dicts) -> {nodes, edges} in graphspike shape.
    Unknown event types are skipped, not fatal — silver evolves faster
    than the graph projection and the bridge must degrade honestly."""
    nodes, edges, seen = [], [], set()

    def n(label, nid, **props):
        key = (label, nid)
        if key in seen:
            return
        seen.add(key)
        nodes.append({"label": label, "id": nid, **props})

    def e(etype, src, dst, **props):
        edges.append({"type": etype, "src": src, "dst": dst, **props})

    for ev in events:
        t = ev.get("event_type")
        if t == "claim_filed":
            c = ev["claim_id"]
            n("Claim", c, amount=ev.get("amount", 0))
            n("Facility", ev["facility_id"])
            n("Prescriber", ev["prescriber_id"])
            n("Account", ev["paid_to"])
            e("FILED_BY", c, ev["facility_id"])
            e("SIGNED_BY", c, ev["prescriber_id"])
            e("PAID_TO", c, ev["paid_to"])
        elif t == "encounter_held":
            enc = ev["encounter_id"]
            n("Encounter", enc, kind=ev.get("kind", "outpatient"))
            n("Facility", ev["facility_id"])
            e("HELD_AT", enc, ev["facility_id"])
            for p in ev.get("attendees", []):
                n("Patient", p)
                e("ATTENDED", p, enc)
        elif t == "referral_made":
            n("Facility", ev["from_facility"])
            n("Facility", ev["to_facility"])
            e("REFERRED_TO", ev["from_facility"], ev["to_facility"],
              count=int(ev.get("count", 1)))
        elif t == "staff_posting":
            n("Prescriber", ev["prescriber_id"])
            n("Facility", ev["facility_id"])
            e("WORKS_AT", ev["prescriber_id"], ev["facility_id"])
        # else: skipped honestly — see docstring.
    return {"nodes": nodes, "edges": edges}


def read_silver_jsonl(path):
    """Read a silver JSONL partition into an event list (skips bad lines)."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # partial write at flush boundary: skip, never die
    return out


def write_csvs(graph, outdir):
    """Render nodes/edges as CSV files for AGE bulk load."""
    os.makedirs(outdir, exist_ok=True)
    for key in ("nodes", "edges"):
        rows = graph[key]
        cols = sorted({k for r in rows for k in r}) or ["id"]
        with open(os.path.join(outdir, f"{key}.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    return outdir


if __name__ == "__main__":
    import sys
    events = read_silver_jsonl(sys.argv[1])
    g = bridge(events)
    write_csvs(g, sys.argv[2] if len(sys.argv) > 2 else "graph_out")
    print(f"nodes={len(g['nodes'])} edges={len(g['edges'])}")
