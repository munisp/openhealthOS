"""digest.py — the monthly improvement digest: what the platform learned,
what wants promotion, what a data steward must decide.

Packreview gave clinicians a printable packet; PromotionLog gives policy
candidates a gate. This renders the steward's monthly packet: every
candidate evaluation with its estimate, CI, ESS and verdict, every
pending promotion with its fingerprint, every refusal with its reason.
A state data steward reads ONE document and signs — or doesn't.

Generated FROM the registry and evaluation results, never editable into
them: fingerprints are rendered so a printed packet can be checked
against the live registry.
"""
from __future__ import annotations


def render_digest(period: str, evaluations: list[dict],
                  promotion_log, notes: list[str] | None = None) -> str:
    """evaluations: [{name, result}] — result as returned by evaluate().
    promotion_log: a PromotionLog whose entries may await approval."""
    L = []
    L.append(f"# Platform Improvement Digest — {period}")
    L.append("")
    L.append("> Every number below was computed from logged decisions "
             "with recorded propensities. 'Hold' is the system working, "
             "not failing. Nothing promotes without a scoped human "
             "signature — this document is where that signature lands.")
    L.append("")

    promotes = [e for e in evaluations
                if e["result"].get("verdict", {}).get("verdict") == "promote"]
    holds = [e for e in evaluations
             if e["result"].get("verdict", {}).get("verdict") == "hold"]
    L.append(f"## Summary — {len(promotes)} ready for sign-off, "
             f"{len(holds)} held")
    L.append("")

    L.append("## Candidate evaluations")
    L.append("")
    L.append("| Candidate | IPS | CI95 | ESS | State | Verdict |")
    L.append("|---|---|---|---|---|---|")
    for e in evaluations:
        r = e["result"]
        v = r.get("verdict") or {}
        L.append(f"| {e['name']} | {r['estimate_ips']:.3f} | "
                 f"[{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}] | "
                 f"{r['ess']:.0f} | {r['state']} | "
                 f"{v.get('verdict', '—')} |")
    L.append("")

    pending = [e for e in getattr(promotion_log, "entries", [])
               if e["state"] == "draft"]
    L.append(f"## Pending promotions ({len(pending)})")
    L.append("")
    if not pending:
        L.append("None awaiting signature.")
    for p in pending:
        L.append(f"### {p['candidate']}")
        L.append("")
        L.append(f"- Fingerprint: `{p['fingerprint']}`")
        L.append(f"- Author: {p['author']}")
        L.append("- Approve via:")
        L.append("")
        L.append("```python")
        L.append(f'promotion_log.approve("{p["fingerprint"]}",')
        L.append('                     approver="<you>", '
                 'role="data-steward",')
        L.append('                     note="<basis>")')
        L.append("```")
        L.append("")
        L.append("- Approver: ______________________  "
                 "Note: ____________________________________________")
        L.append("")

    if notes:
        L.append("## Notes")
        L.append("")
        for n in notes:
            L.append(f"- {n}")
        L.append("")
    L.append("*Fingerprints must match the live registry; a mismatch "
             "means the candidate changed after evaluation — re-run, "
             "do not sign.*")
    return "\n".join(L)
