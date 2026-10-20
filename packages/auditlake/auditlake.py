"""OpenHealth OS — audit lake (NDPA 2023: immutable audit logs).

Every sensitive action (consent issued/revoked/checked, SHR access incl.
break-glass, claim decisions, pre-auth) already emits audit events in its
service. The lake collects them into one append-only, hash-chained log:
each entry carries the hash of its predecessor, so any edit, deletion or
reordering upstream breaks the chain detectably. This is the Wazuh-style
integrity guarantee, implemented stdlib (ADR-0005).

Chain law:
    hash(entry) = sha256(canonical_json(entry without hash) + prev_hash)

CLI:
    python3 auditlake.py append LOG --actor consent-service --action issue ...
    python3 auditlake.py collect LOG --from http://consent:8082/audit ...
    python3 auditlake.py verify LOG          # exit 1 on any broken link
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request

GENESIS = "0" * 64


def canonical(entry: dict) -> bytes:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical(entry) + entry["prev_hash"].encode()).hexdigest()


def read_log(path: str) -> list[dict]:
    entries = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        pass
    return entries


def append(path: str, actor: str, action: str, subject: str,
           details: dict | None = None, source_id: str | None = None) -> dict:
    entries = read_log(path)
    prev = entries[-1]["hash"] if entries else GENESIS
    entry = {
        "seq": len(entries) + 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": actor, "action": action, "subject": subject,
        "details": details or {},
        "source_id": source_id or f"{actor}-{len(entries)+1}",
        "prev_hash": prev,
    }
    entry["hash"] = entry_hash(entry)
    with open(path, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def verify(path: str) -> tuple[int, list[str]]:
    """Returns (count, problems). A sound log has zero problems."""
    problems: list[str] = []
    entries = read_log(path)
    prev = GENESIS
    seen_seq = 0
    for e in entries:
        label = f"seq {e.get('seq')}"
        if e.get("seq") != seen_seq + 1:
            problems.append(f"{label}: sequence gap or reorder")
        seen_seq = e.get("seq", seen_seq + 1)
        if e.get("prev_hash") != prev:
            problems.append(f"{label}: prev_hash link broken (entry deleted or reordered)")
        if e.get("hash") != entry_hash(e):
            problems.append(f"{label}: content hash mismatch (entry tampered)")
        prev = e.get("hash", prev)
    return len(entries), problems


def collect(path: str, sources: list[str]) -> int:
    """Pull audit arrays from service endpoints into the chain.

    Dedup by (actor, source_id): re-collection is a no-op, so the collector
    can run on a cron without double-chaining.
    """
    existing = {(e["actor"], e.get("source_id")) for e in read_log(path)}
    added = 0
    for url in sources:
        with urllib.request.urlopen(url, timeout=10) as resp:
            events = json.loads(resp.read())
        for ev in events:
            actor = ev.get("actor", url)
            sid = ev.get("id") or ev.get("source_id") or json.dumps(ev, sort_keys=True)
            if (actor, sid) in existing:
                continue
            append(path, actor=actor, action=ev.get("action", "event"),
                   subject=ev.get("subject", ""), details=ev, source_id=sid)
            existing.add((actor, sid))
            added += 1
    return added



# --- Merkle anchoring (innovation 10) ---------------------------------------
#
# The hash chain proves the log is internally consistent, but an attacker
# with write access could rewrite the WHOLE chain and recompute every
# link. Anchoring closes that: periodically fold the entry hashes into a
# Merkle root and commit the root to an append-only anchors file (which
# can be printed, emailed, or published off-box — a root you no longer
# control the history of). Any later rewrite changes the root and is
# caught by verify-anchor.


def merkle_root(hashes: list[str]) -> str:
    """Binary Merkle root over hex hashes; odd layers duplicate the last
    leaf (standard Bitcoin-style rule). Empty set anchors GENESIS."""
    if not hashes:
        return GENESIS
    layer = [bytes.fromhex(h) for h in hashes]
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [
            hashlib.sha256(layer[i] + layer[i + 1]).digest()
            for i in range(0, len(layer), 2)
        ]
    return layer[0].hex()


def anchor(log_path: str, anchors_path: str) -> dict:
    """Fold every entry hash in the log into one root and append it.

    The anchor records how many entries it covers, so partial rewrites
    after the fact are as detectable as content edits."""
    entries = read_log(log_path)
    count, problems = verify(log_path)
    if problems:
        raise SystemExit(f"refusing to anchor a broken chain: {problems[0]}")
    root = merkle_root([e["hash"] for e in entries])
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": count,
        "root": root,
    }
    with open(anchors_path, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def verify_anchor(log_path: str, anchors_path: str) -> tuple[bool, str]:
    """Recompute the root over the first N entries for each anchor and
    confirm the log still matches every anchor on record."""
    try:
        with open(anchors_path) as fh:
            anchors = [json.loads(l) for l in fh if l.strip()]
    except FileNotFoundError:
        return False, "no anchors on record"
    entries = read_log(log_path)
    # Content/chain integrity first: a tampered entry whose stale stored
    # hash still matches the anchor must not pass.
    _, problems = verify(log_path)
    if problems:
        return False, f"chain integrity failure: {problems[0]}"
    for a in anchors:
        n = a["entries"]
        if len(entries) < n:
            return False, f"anchor@{a['ts']}: log has {len(entries)} entries, anchor covers {n} (tail deleted)"
        root = merkle_root([e["hash"] for e in entries[:n]])
        if root != a["root"]:
            return False, f"anchor@{a['ts']}: root mismatch over first {n} entries (history rewritten)"
    return True, f"{len(anchors)} anchor(s) hold"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="tamper-evident audit lake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("log")
    a.add_argument("--actor", required=True)
    a.add_argument("--action", required=True)
    a.add_argument("--subject", default="")
    c = sub.add_parser("collect")
    c.add_argument("log")
    c.add_argument("--from", dest="sources", nargs="+", required=True)
    v = sub.add_parser("verify")
    v.add_argument("log")
    an = sub.add_parser("anchor")
    an.add_argument("log")
    an.add_argument("anchors")
    va = sub.add_parser("verify-anchor")
    va.add_argument("log")
    va.add_argument("anchors")
    args = ap.parse_args(argv)

    if args.cmd == "append":
        e = append(args.log, args.actor, args.action, args.subject)
        print(f"chained seq={e['seq']} hash={e['hash'][:16]}…")
        return 0
    if args.cmd == "collect":
        print(f"collected {collect(args.log, args.sources)} new events")
        return 0
    if args.cmd == "anchor":
        rec = anchor(args.log, args.anchors)
        print(f"anchored {rec['entries']} entries root={rec['root'][:16]}…")
        return 0
    if args.cmd == "verify-anchor":
        ok, msg = verify_anchor(args.log, args.anchors)
        print(msg)
        return 0 if ok else 1
    count, problems = verify(args.log)
    if problems:
        print(f"INTEGRITY FAILURE — {count} entries, {len(problems)} problems:")
        for p in problems[:20]:
            print(f"  {p}")
        return 1
    print(f"chain intact: {count} entries verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
