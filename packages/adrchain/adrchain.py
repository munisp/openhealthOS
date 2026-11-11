"""adrchain.py — ADR chain integrity gate (ADR-0086).

The architecture-decision record chain is the estate's memory of WHY.
Wave 22 recon found the chain had silently collided: two waves allocated
ADR-0060/0061/0062 to different decisions, and every inbound reference —
code docstrings, infra comments, README rows, other ADRs — was then
ambiguous about which decision it cited. Nothing watched the chain.

Doctrine:
  * a decision number is a CONTRACT — two files claiming one number is
    a defect, red, with every claimant named
  * filename number and the H1 inside the file must agree — a file that
    says a different number than its name is a mismatch, red
  * a gap in the chain is NOT automatically a defect — numbers can be
    retired — but a gap is STATED, amber, never silent
  * every ADR declares a Status — a decision without a stated status is
    unfinished business, amber
  * tree readers are INJECTED (listdir/read) — the gate never touches
    the filesystem directly, so fixtures and the real tree run the
    exact same code path
"""

import os
import re

VERSION = "adrchain@1.0.0"

ADR_DIR = "docs/architecture"

FILE_RE = re.compile(r"^adr-(\d{4})-([a-z0-9-]+)\.md$")
# The headline contract is canonical: '# ADR-NNNN: title'. A second
# variant is a defect (ADR-0086) — wave-22 recon found 4 em-dash
# headlines and normalised them rather than teach the gate a variant.
H1_RE = re.compile(r"^#\s+ADR-(\d{4}):\s*(.+?)\s*$", re.MULTILINE)
# Status is a declared FACT, decoration is not: accept 'Status:',
# '**Status**:', '**Status:**', '- Status:', and a '## Status' heading.
STATUS_RE = re.compile(
    r"^[\s*\-]*status\**\s*:|·\s*status\s*:|^#+\s*status\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_HEAD_LINES = 12  # status must be declared near the top


def _default_listdir(root, adr_dir):
    return sorted(os.listdir(os.path.join(root, adr_dir)))


def _default_read(root, path):
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        return fh.read()


def audit(root, *, adr_dir=ADR_DIR, listdir=None, read=None):
    """Audit the ADR chain under ``root/adr_dir``.

    ``listdir(root, adr_dir)`` and ``read(root, path)`` are injectable;
    defaults read the real tree. Never raises on a malformed chain —
    a malformed chain is a verdict, not an exception.
    """
    listdir = listdir or _default_listdir
    read = read or _default_read

    entries = []
    try:
        names = listdir(root, adr_dir)
    except (OSError, FileNotFoundError) as exc:  # the dir itself missing
        return {
            "gate": VERSION,
            "adr_dir": adr_dir,
            "error": f"adr directory unreadable: {exc}",
            "files": [],
            "summary": {"chain_ok": False, "error": True},
        }

    non_adr = []
    for name in sorted(names):
        m = FILE_RE.match(name)
        if not m:
            if name.startswith("adr-") or name.endswith(".md"):
                non_adr.append(name)
            continue
        number = int(m.group(1))
        slug = m.group(2)
        path = f"{adr_dir}/{name}"
        entry = {
            "file": path,
            "number": number,
            "slug": slug,
            "verdicts": [],
        }
        try:
            text = read(root, path)
        except (OSError, FileNotFoundError):
            entry["verdicts"].append({"kind": "unreadable", "severity": "red"})
            entries.append(entry)
            continue

        h1 = H1_RE.search(text)
        if h1 is None:
            entry["verdicts"].append({"kind": "no-title", "severity": "red",
                                      "detail": "no '# ADR-NNNN:' headline found"})
            entry["title"] = None
        else:
            h1_number = int(h1.group(1))
            entry["title"] = h1.group(2)
            if h1_number != number:
                entry["verdicts"].append({
                    "kind": "number-mismatch", "severity": "red",
                    "detail": f"filename says {number:04d} but headline says {h1_number:04d}",
                })

        head = "\n".join(text.splitlines()[:_HEAD_LINES])
        if STATUS_RE.search(head):
            entry["status"] = "declared"
        else:
            entry["status"] = None
            entry["verdicts"].append({"kind": "no-status", "severity": "amber",
                                      "detail": "no Status declaration near the top"})
        entries.append(entry)

    # Duplicate numbers: a number claimed by more than one file.
    by_number = {}
    for e in entries:
        by_number.setdefault(e["number"], []).append(e["file"])
    duplicates = [
        {"number": n, "claimants": sorted(files)}
        for n, files in sorted(by_number.items())
        if len(files) > 1
    ]
    for dup in duplicates:
        for e in entries:
            if e["number"] == dup["number"]:
                e["verdicts"].append({
                    "kind": "duplicate-number", "severity": "red",
                    "detail": f"number {dup['number']:04d} claimed by {len(dup['claimants'])} files",
                })

    # Gaps: numbers below the maximum that no file claims. Stated, amber.
    numbers = sorted(by_number)
    gaps = []
    if numbers:
        claimed = set(numbers)
        gaps = [n for n in range(1, numbers[-1] + 1) if n not in claimed]

    reds = sum(1 for e in entries for v in e["verdicts"] if v["severity"] == "red")
    ambers = sum(1 for e in entries for v in e["verdicts"] if v["severity"] == "amber")
    chain_ok = reds == 0

    return {
        "gate": VERSION,
        "adr_dir": adr_dir,
        "files": entries,
        "duplicates": duplicates,
        "gaps": gaps,
        "non_adr_files": non_adr,
        "summary": {
            "files": len(entries),
            "distinct_numbers": len(by_number),
            "max_number": numbers[-1] if numbers else 0,
            "duplicates": len(duplicates),
            "gaps": len(gaps),
            "red_verdicts": reds,
            "amber_verdicts": ambers,
            "chain_ok": chain_ok,
        },
    }
