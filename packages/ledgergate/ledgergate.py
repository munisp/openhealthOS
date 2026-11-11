"""ledgergate.py — README ledger integrity gate (ADR-0087).

The README feature ledger is the estate's public census of WHAT shipped:
one numbered row per feature wave, cited by boards, ADRs, and memory.
Wave-23 recon found the ledger had silently decayed: row 30 was claimed
twice (ADR-0032 in sequence, ADR-0031 displaced), rows 155-160 were
claimed twice (travelpass engine rows and the demo block), row 36 was
never allocated, five rows sat displaced after row 145 instead of in
numeric position, two rows were swapped in the modules table, and the
newest-first descending tail collided with the ascending body. Nothing
watched the ledger.

Doctrine:
  * a row number is a CONTRACT — two rows claiming one number is a
    defect, red, with every claimant named (line and excerpt)
  * the ledger is ONE ascending sequence — an out-of-order row is a
    defect, red. The wave-18..22 newest-first descending tail was an
    insertion habit, not a contract; wave 23 normalised it
  * a gap in the numbering is NOT automatically a defect — a row can be
    retired — but a gap is STATED, amber, never silent
  * sub-rows (`8a`, `71a`) attach to their parent row and must follow it
    directly; an orphan sub-row is red, a suffix sequence that does not
    start at `a` is amber, stated
  * the reader is INJECTED (read) — the gate never touches the
    filesystem directly, so fixtures and the real tree run the exact
    same code path
"""

import os
import re

VERSION = "ledgergate@1.0.0"

README = "README.md"

# A ledger row is a table row whose first cell is an integer with an
# optional lowercase suffix (sub-row). No other table in the README
# leads with a bare number — verified by wave-23 recon.
ROW_RE = re.compile(r"^\|\s*(\d+)([a-z]+)?\s*\|")


def _default_read(root, path):
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        return fh.read()


def _excerpt(line, limit=60):
    """First text cell of a ledger row, trimmed — enough to name a claimant."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    text = cells[1] if len(cells) > 1 else line.strip()
    return text[:limit]


def audit(root, *, readme=README, read=None):
    """Audit the README ledger under ``root``.

    ``read(root, path)`` is injectable; the default reads the real tree.
    Never raises on a malformed ledger — a malformed ledger is a verdict,
    not an exception.
    """
    read = read or _default_read
    try:
        text = read(root, readme)
    except OSError as exc:
        return {
            "gate": VERSION,
            "readme": readme,
            "error": f"unreadable: {exc}",
            "entries": [],
            "verdicts": [{"severity": "red", "kind": "unreadable",
                          "detail": f"{readme}: {exc}"}],
            "summary": {"rows": 0, "sub_rows": 0, "max_number": 0,
                        "duplicates": [], "gaps": [], "out_of_order": [],
                        "orphan_subrows": [], "suffix_anomalies": [],
                        "red_verdicts": 1, "amber_verdicts": 0,
                        "ledger_ok": False, "error": True},
        }

    entries = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = ROW_RE.match(line)
        if m:
            entries.append({
                "line": lineno,
                "number": int(m.group(1)),
                "suffix": m.group(2),
                "excerpt": _excerpt(line),
            })

    verdicts = []

    # --- duplicates: one number, one row (sub-rows share the parent's) ---
    by_number = {}
    for e in entries:
        if e["suffix"] is None:
            by_number.setdefault(e["number"], []).append(e)
    duplicates = []
    for n in sorted(by_number):
        if len(by_number[n]) > 1:
            claimants = [f"line {e['line']} ({e['excerpt']})" for e in by_number[n]]
            duplicates.append({"number": n, "claimants": claimants})
            verdicts.append({
                "severity": "red", "kind": "duplicate",
                "detail": f"row {n} claimed {len(by_number[n])} times: "
                          + "; ".join(claimants),
            })

    # --- gaps: stated, amber, never silent ---
    numbers = sorted(by_number)
    gaps = []
    if numbers:
        claimed = set(numbers)
        gaps = [n for n in range(1, numbers[-1] + 1) if n not in claimed]
        if gaps:
            verdicts.append({
                "severity": "amber", "kind": "gap",
                "detail": f"unallocated row numbers (retired or never "
                          f"allocated), stated: {gaps}",
            })

    # --- order: one ascending sequence; sub-rows ride with the parent ---
    out_of_order = []
    orphan_subrows = []
    suffix_anomalies = []
    seq = 0          # last parent number accepted
    parent = None    # parent number current sub-rows attach to
    seen_suffixes = []
    for e in entries:
        if e["suffix"] is None:
            if e["number"] <= seq:
                out_of_order.append({"line": e["line"], "number": e["number"],
                                     "after": seq})
                verdicts.append({
                    "severity": "red", "kind": "out_of_order",
                    "detail": f"row {e['number']} at line {e['line']} "
                              f"follows row {seq} — the ledger is one "
                              f"ascending sequence",
                })
            seq = max(seq, e["number"])
            parent = e["number"]
            seen_suffixes = []
        else:
            if e["number"] != parent:
                orphan_subrows.append({"line": e["line"],
                                       "label": f"{e['number']}{e['suffix']}"})
                verdicts.append({
                    "severity": "red", "kind": "orphan_subrow",
                    "detail": f"sub-row {e['number']}{e['suffix']} at line "
                              f"{e['line']} does not follow its parent row",
                })
            else:
                expected = chr(ord("a") + len(seen_suffixes))
                if e["suffix"] != expected:
                    suffix_anomalies.append({
                        "line": e["line"],
                        "label": f"{e['number']}{e['suffix']}",
                        "expected": f"{e['number']}{expected}",
                    })
                    verdicts.append({
                        "severity": "amber", "kind": "suffix_sequence",
                        "detail": f"sub-row {e['number']}{e['suffix']} at "
                                  f"line {e['line']} breaks the suffix "
                                  f"sequence (expected {e['number']}"
                                  f"{expected}), stated",
                    })
                seen_suffixes.append(e["suffix"])

    reds = sum(1 for v in verdicts if v["severity"] == "red")
    ambers = sum(1 for v in verdicts if v["severity"] == "amber")
    summary = {
        "rows": sum(1 for e in entries if e["suffix"] is None),
        "sub_rows": sum(1 for e in entries if e["suffix"] is not None),
        "max_number": numbers[-1] if numbers else 0,
        "duplicates": sorted(d["number"] for d in duplicates),
        "gaps": gaps,
        "out_of_order": sorted(o["number"] for o in out_of_order),
        "orphan_subrows": [o["label"] for o in orphan_subrows],
        "suffix_anomalies": [s["label"] for s in suffix_anomalies],
        "red_verdicts": reds,
        "amber_verdicts": ambers,
        "ledger_ok": reds == 0,
    }
    return {
        "gate": VERSION,
        "readme": readme,
        "entries": entries,
        "verdicts": verdicts,
        "summary": summary,
    }


if __name__ == "__main__":
    import json
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(audit(root)["summary"], indent=2, sort_keys=True))
