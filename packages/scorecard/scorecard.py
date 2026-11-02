"""scorecard.py — per-clinician scorecards with denominators.

The gap analysis: turnaround and discrepancy existed as engine calls;
a clinician-facing dashboard did not, and peer review had no RADPEER
semantics. The engines now score peer review 1-4 with clinical
significance; this package composes the engines into one card per
clinician:

  workload       — assigned cases by state (what sits on THEIR desk)
  turnaround     — mean hours vs SLA per priority, with n
  peer review    — RADPEER distribution OF their reports, and reviews
                   BY their hand; score 3-4 are the ones that change
                   management
  critical ack   — escalations raised on their cases vs acknowledged
  capture QA     — (optional) their ungradable rate, if a CaptureQA
                   gate is in play
  concordance    — (optional) their two-grader concordance, if a
                   DuelGrader is in play

Every rate ships with its denominator. A rate without n is marketing;
a clinician measured without n is a rumour.
"""


class ScorecardError(Exception):
    pass


def clinician_card(svc, reader_id, *, as_of, qa=None, duel=None):
    """One clinician's card from a teleread-family reading service.
    Duck-typed: anything with .cfg, .cases, .reports, .escalations."""
    if not reader_id:
        raise ScorecardError("a card for nobody measures nobody")
    mine = [t for t in svc.cases.values()
            if t.get("assigned_to") == reader_id]
    by_state = {}
    for t in mine:
        by_state[t["state"]] = by_state.get(t["state"], 0) + 1

    tat = {}
    for t in mine:
        if t["state"] != "reported-verified":
            continue
        rep = svc.reports.get(t["case_id"])
        if not rep or rep["reader"] != reader_id:
            continue
        hours = (rep["verified_day"] - t["ordered_day"]) * 24
        b = tat.setdefault(t["priority"],
                           {"n": 0, "within_sla": 0, "hours": 0})
        b["n"] += 1
        b["hours"] += hours
        b["within_sla"] += int(hours <= svc.cfg.sla_hours[t["priority"]])
    for b in tat.values():
        b["mean_hours"] = round(b["hours"] / b["n"], 1)
        del b["hours"]

    of_me = [pr for r in svc.reports.values()
             if r["reader"] == reader_id
             for pr in r.get("peer_reviews", [])]
    dist = {s: 0 for s in (1, 2, 3, 4)}
    for pr in of_me:
        if pr.get("score") in dist:
            dist[pr["score"]] += 1
    significant = sum(1 for pr in of_me if pr.get("clinically_significant"))
    by_me = sum(1 for r in svc.reports.values()
                for pr in r.get("peer_reviews", [])
                if pr.get("reviewer") == reader_id)

    my_cases = {t["case_id"] for t in mine}
    crit = [e for e in svc.escalations if e["case_id"] in my_cases]
    acked = [e for e in crit if e.get("acknowledged")]

    card = {
        "reader_id": reader_id, "domain": svc.cfg.domain, "as_of": as_of,
        "workload": {"assigned_total": len(mine), "by_state": by_state},
        "turnaround": tat,
        "peer_review": {
            "reviews_of_my_reports": len(of_me),
            "score_distribution": dist,
            "clinically_significant": significant,
            "significant_rate": (round(significant / len(of_me), 3)
                                 if of_me else None),
            "reviews_by_me": by_me},
        "critical": {"raised_on_my_cases": len(crit),
                     "acknowledged": len(acked),
                     "open": len(crit) - len(acked)}}
    if qa is not None:
        card["capture_qa"] = qa.report()["by_assessor"].get(
            reader_id, {"n": 0, "ungradable": 0, "ungradable_rate": None})
    if duel is not None:
        card["grading_concordance"] = duel.agreement_report()[
            "per_grader"].get(
            reader_id, {"paired": 0, "concordant": 0, "concordance": None})
    return card


def scoreboard(svc, *, as_of, qa=None, duel=None):
    """The department view: every reader who touched the service,
    carded. Nobody is measured by their absence."""
    readers = set()
    for t in svc.cases.values():
        if t.get("assigned_to"):
            readers.add(t["assigned_to"])
    for r in svc.reports.values():
        readers.add(r["reader"])
        readers.update(pr["reviewer"] for pr in r.get("peer_reviews", []))
    return {"domain": svc.cfg.domain, "as_of": as_of,
            "readers": {rid: clinician_card(svc, rid, as_of=as_of,
                                            qa=qa, duel=duel)
                        for rid in sorted(readers)},
            "note": "every rate ships with its denominator; a rate "
                    "without n is marketing"}
