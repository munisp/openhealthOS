"""review_packet.py — turn a protocol pack into a printable clinical
review form.

packreview.py enforces that no pack goes live without a scoped human
sign-off. But a medical officer should not have to read Python to
discharge that duty. This module renders a pack as a self-contained
Markdown review packet: every step, every option, every red flag, every
cadre-gated action, plus the sign-off block packreview.review() expects
(approver, role, note) and the fingerprint the registry will check.

Doctrine: the packet is generated FROM the pack, never edited into it —
a reviewer who wants a change edits the pack source and re-registers,
which re-fingerprints and (per packreview) drops any prior sign-off.
"""
from __future__ import annotations

import hashlib
import json


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"),
                      default=str).encode()


def fingerprint(pack: dict) -> str:
    return hashlib.sha256(_canon(pack)).hexdigest()


def render_packet(name: str, pack: dict, author: str) -> str:
    fp = fingerprint(pack)
    L = []
    L.append(f"# Clinical Review Packet — {name}")
    L.append("")
    L.append(f"- **Pack version**: `{pack.get('version', '?')}`")
    L.append(f"- **Fingerprint (sha256)**: `{fp[:16]}…`")
    L.append(f"- **Author**: {author}")
    L.append("- **Status**: DRAFT — not usable in sessions until approved")
    L.append("")
    L.append("> Review duty: read every step. Approving signs that a CHW "
             "following this pack, within their cadre scope, is safe for "
             "the conditions named. Any edit after your signature returns "
             "the pack to draft automatically.")
    L.append("")
    L.append("## Flow")
    L.append("")
    L.append(f"Start step: `{pack.get('start', '?')}`")
    L.append("")
    red = pack.get("red_flags", {})
    for sid, step in pack.get("steps", {}).items():
        L.append(f"### Step `{sid}`")
        L.append("")
        L.append(f"**Q:** {step.get('text', '?')}")
        L.append("")
        flags = red.get(sid, [])
        for opt, nxt in step.get("options", {}).items():
            flag = " 🚩 **RED FLAG**" if opt in flags else ""
            if isinstance(nxt, dict):
                esc = " — *mandatory escalation*" if nxt.get("escalate") else ""
                L.append(f"- `{opt}` → outcome **{nxt.get('outcome')}**{esc}{flag}")
            else:
                L.append(f"- `{opt}` → step `{nxt}`{flag}")
        L.append("")
    actions = pack.get("actions", {})
    if actions:
        L.append("## Cadre-gated actions")
        L.append("")
        L.append("| Action | Minimum cadre |")
        L.append("|---|---|")
        for a, spec in sorted(actions.items()):
            L.append(f"| `{a}` | {spec.get('min_cadre', '?')} |")
        L.append("")
    L.append("## Sign-off")
    L.append("")
    L.append("Complete by calling the registry (or returning this form):")
    L.append("")
    L.append("```python")
    L.append(f'registry.review({name!r}, approver="<your name>",')
    L.append('             role="medical-officer"  # or consultant / nmcn-panel')
    L.append('             decision="approve",     # or "reject"')
    L.append('             note="<clinical basis for the decision>")')
    L.append("```")
    L.append("")
    L.append("- Approver: ______________________  Role: ______________________")
    L.append("- Decision:  ☐ approve   ☐ reject")
    L.append("- Note (required): ")
    L.append("")
    L.append("  __________________________________________________________________")
    L.append("")
    L.append("  __________________________________________________________________")
    L.append("")
    L.append(f"*Fingerprint `{fp[:16]}…` must match the pack under review; "
             "if it does not, the pack changed after this packet was "
             "printed — re-export before signing.*")
    return "\n".join(L)
