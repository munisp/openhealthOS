"""OpenHealth OS — deterministic claim replay (integration guarantee #2).

Any past claim decision must be re-derivable from the same events. The
adjudicator is deterministic (registries in-file, rules pure), so replay is:
read the recorded `claim_received` events, re-adjudicate, compare against
the recorded decisions. Divergence means either the rules changed (then the
log tells you exactly which claims would flip) or someone tampered — both
are things an auditor needs to know, loudly.

CLI:
    python3 replay.py --events events.jsonl [--adjudicator path]
                      [--report divergences.json]
Exit 0 when every replayed decision matches, 1 on any divergence.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass, field


def load_adjudicator(path: str):
    """Import adjudicate.py by path (packages aren't installed modules)."""
    spec = importlib.util.spec_from_file_location("adjudicate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Divergence:
    claim_id: str
    recorded: str
    replayed: str
    recorded_reasons: list = field(default_factory=list)
    replayed_reasons: list = field(default_factory=list)


def replay(events: list[dict], adjudicator) -> tuple[int, list[Divergence]]:
    """Re-adjudicate every claim_received event; compare to claim_decided."""
    received: dict[str, dict] = {}
    decided: dict[str, dict] = {}
    for ev in events:
        etype = ev.get("type")
        payload = ev.get("payload") or {}
        cid = payload.get("claim_id") or ev.get("claim_id")
        if not cid:
            continue
        if etype == "claim_received":
            received[cid] = payload
        elif etype == "claim_decided":
            decided[cid] = payload

    divergences: list[Divergence] = []
    replayed = 0
    for cid, claim in sorted(received.items()):
        result = adjudicator.adjudicate(dict(claim))
        replayed += 1
        rec = decided.get(cid)
        if rec is None:
            divergences.append(Divergence(cid, recorded="MISSING",
                                          replayed=result["decision"],
                                          replayed_reasons=result.get("reasons", [])))
            continue
        if rec.get("decision") != result["decision"]:
            divergences.append(Divergence(cid, recorded=rec.get("decision", "?"),
                                          replayed=result["decision"],
                                          recorded_reasons=rec.get("reasons", []),
                                          replayed_reasons=result.get("reasons", [])))
    return replayed, divergences


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="deterministic claim replay verifier")
    ap.add_argument("--events", required=True, help="JSONL of claim events")
    ap.add_argument("--adjudicator", default="packages/adjudicator/adjudicate.py")
    ap.add_argument("--report", help="write divergences JSON here")
    args = ap.parse_args(argv)

    events = []
    with open(args.events) as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    adj = load_adjudicator(args.adjudicator)
    replayed, divergences = replay(events, adj)
    print(f"replayed {replayed} claims; {len(divergences)} divergences")
    for d in divergences[:20]:
        print(f"  {d.claim_id}: recorded={d.recorded} replayed={d.replayed}")
    if args.report:
        with open(args.report, "w") as fh:
            json.dump([d.__dict__ for d in divergences], fh, indent=2)
    return 1 if divergences else 0


if __name__ == "__main__":
    sys.exit(main())
