"""aar.py — the after-action review: outbreaks end; the learning must not.

Assembles the review document from the systems that lived the event:
the contact graph (follow-up completeness, generations reached), the lab
tracker (TAT percentiles), the signal ledger (precision per source),
the case-definition registry (version timeline, stale-facility audit),
the capacity board and ring campaigns.

The AAR is GENERATED from records, never hand-written into them: every
number is recomputed from the source object at render time. A pleasant
after-action review that contradicts the data is propaganda.
"""
from __future__ import annotations


def render_aar(outbreak_name: str, period: tuple[str, str], *,
               graph=None, labs=None, ledger=None, casedefs=None,
               casedef_name: str | None = None, rings=None,
               notes: list[str] | None = None) -> str:
    L = []
    L.append(f"# After-Action Review — {outbreak_name}")
    L.append(f"Period: {period[0]} → {period[1]}")
    L.append("")
    L.append("> Every figure below was recomputed from the system of "
             "record at generation time. Sections marked ABSENT were not "
             "instrumented during this event — that absence is itself a "
             "finding.")
    L.append("")

    if graph is not None:
        s = graph.stats()
        L.append("## Contact tracing")
        L.append("")
        L.append(f"- Cases: {s['cases']} · Contacts line-listed: "
                 f"{s['contacts']}")
        L.append(f"- Deepest transmission generation reached: "
                 f"{s['max_generation']}")
        L.append(f"- Follow-up completeness at close: "
                 f"{s['followup_completeness']:.1%}")
        L.append(f"- Final states: "
                 + ", ".join(f"{k} {v}" for k, v in
                             sorted(s["by_state"].items())))
        L.append("")
    else:
        L.append("## Contact tracing — ABSENT\n")

    if labs is not None:
        L.append("## Laboratory turnaround")
        L.append("")
        labs_seen = sorted({s["lab"] for s in labs.samples.values()})
        for lab in labs_seen:
            b = labs.baseline(lab)
            if b["state"] == "ready":
                L.append(f"- {lab}: median {b['median_hours']}h, "
                         f"p90 {b['p90_hours']}h "
                         f"({b['completed']} completed)")
            else:
                L.append(f"- {lab}: {b['completed']} completed — "
                         "insufficient for a baseline")
        L.append("")
    else:
        L.append("## Laboratory turnaround — ABSENT\n")

    if ledger is not None and period[1]:
        r = ledger.precision_report(period[1])
        L.append("## Signal precision")
        L.append("")
        for src, b in sorted(r["by_source"].items()):
            L.append(f"- {src}: precision {b['precision']:.1%} "
                     f"({b['verified']}/{b['total']})")
        if r["unresolved_count"]:
            L.append(f"- ⚠ {r['unresolved_count']} signals never "
                     "received outcome labels — precision above excludes "
                     "them")
        L.append("")
    else:
        L.append("## Signal precision — ABSENT\n")

    if casedefs is not None and casedef_name:
        a = casedefs.adoption_audit(casedef_name)
        L.append("## Case definition adoption")
        L.append("")
        L.append(f"- Current version: {a['current_version']}")
        L.append(f"- Screenings by version: "
                 + ", ".join(f"{k}: {v}" for k, v in
                             sorted(a["screenings_by_version"].items())))
        if a["stale_facilities"]:
            L.append(f"- Facilities that screened under stale versions: "
                     f"{', '.join(a['stale_facilities'])}")
        L.append("")
    else:
        L.append("## Case definition adoption — ABSENT\n")

    if rings:
        L.append("## Ring campaigns")
        L.append("")
        for ring in rings:
            c = ring.completeness()
            L.append(f"- {c['ring']}: {c['completeness']:.0%} accounted "
                     f"({c['dosed_or_complete']} dosed, "
                     f"{c['declined']} declined, "
                     f"{c['aefi_reports']} AEFI)")
        L.append("")

    L.append("## Improvement candidates")
    L.append("")
    if notes:
        for n in notes:
            L.append(f"- {n}")
    else:
        L.append("- (to be filled at the review session)")
    L.append("")
    L.append("*Feed the candidates into the monthly improvement digest "
             "(policyeval) — an AAR finding that cannot become a promoted "
             "policy is a finding that will recur.*")
    return "\n".join(L)
