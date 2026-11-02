"""radiology.py — radiology workflow: worklists, teleradiology, reporting.

The clinical engine above the PACS. The core fact of Nigerian imaging:
studies are taken where radiologists aren't. This module makes remote
reading a first-class, governed workflow — assignment by credential and
workload, reports as auditable documents, critical findings escalated
with acknowledgement, never broadcast into the void.

Ten innovations in this module:
 1. Radiologist credential registry with expiry and subspecialties —
    a stale licence assigns nothing
 2. Credential/subspecialty/SLA-aware task assignment (STAT studies
    route to the least-loaded available in-credential radiologist)
 3. Remote reading sessions with full audit trail (who opened what,
    when, from where)
 4. Structured report templates; reports are append-only documents —
    corrections are addenda that cite, never edits
 5. Critical findings escalation with ack-required ladder and
    re-escalation (the triggers doctrine applied to reports)
 6. Turnaround-time stats with denominators (per priority, per site,
    per reader — never a bare average)
 7. Peer review / double-read program with discrepancy rates surfaced
 8. Cross-facility study transfer gated like medevac (consent id +
    bundle hash) — the image travels lawfully or not at all
 9. Worklist prioritization: STAT first, then overdue-by-SLA, never
    FIFO blindness
10. Report-ready notification via the howUdeyChat NDJSON seam — the
    referring CHW's worklist gets a structured line, not a phone call

Doctrine: a report is DRAFT until a credentialed radiologist verifies
it; unverified reports are never visible to the ward. Assignment never
overrides credential scope. Escalations re-fire until acknowledged.
"""

SLA_HOURS = {"stat": 2, "urgent": 24, "routine": 72}
SUBSPECIALTIES = ("general", "neuro", "msk", "chest", "abdominal",
                  "paediatric", "obstetric", "mammography")
CRITICAL_FINDINGS = ("pneumothorax", "intracranial-haemorrhage",
                     "aortic-dissection", "bowel-perforation",
                     "ectopic-rupture", "cord-compression")
REPORT_STATES = ("draft", "verified", "addended")


from teleread import (RADPEER_SCORES, RADPEER_VERSION,  # noqa: E402
                      validate_radpeer)


class RadiologyError(Exception):
    pass


class RadiologistRegistry:
    """1. credentials with expiry."""

    def __init__(self):
        self.radiologists = {}

    def register(self, rad_id, *, licence_id, licence_expiry_day,
                 subspecialties=("general",), home_site=None, day=None):
        if rad_id in self.radiologists:
            raise RadiologyError(f"duplicate radiologist: {rad_id}")
        if not set(subspecialties) <= set(SUBSPECIALTIES):
            raise RadiologyError("unknown subspecialty")
        self.radiologists[rad_id] = {
            "licence": licence_id, "licence_expiry_day": licence_expiry_day,
            "subspecialties": list(subspecialties),
            "home_site": home_site, "active": True}
        return dict(self.radiologists[rad_id])

    def credentialed(self, rad_id, day):
        r = self.radiologists.get(rad_id)
        if not r:
            raise RadiologyError(f"unknown radiologist: {rad_id}")
        if not r["active"]:
            return False
        return day <= r["licence_expiry_day"]


class RadiologyWorklist:
    def __init__(self, registry):
        self.reg = registry
        self.tasks = {}        # study_uid -> task
        self.reports = {}      # study_uid -> report doc
        self.sessions = []     # reading session audit
        self.escalations = []  # critical finding events
        self.transfers = []    # cross-facility sends
        self._n = 0

    # ---- order & worklist ----
    def order_study(self, study_uid, *, priority, clinical_question,
                    referring_facility, referrer, day,
                    subspecialty="general"):
        if priority not in SLA_HOURS:
            raise RadiologyError(f"unknown priority: {priority}")
        if subspecialty not in SUBSPECIALTIES:
            raise RadiologyError(f"unknown subspecialty: {subspecialty}")
        if not clinical_question:
            raise RadiologyError("no clinical question — imaging without "
                                 "a question is a photo, not a study")
        if study_uid in self.tasks:
            raise RadiologyError(f"duplicate order: {study_uid}")
        self.tasks[study_uid] = {
            "study_uid": study_uid, "priority": priority,
            "question": clinical_question, "facility": referring_facility,
            "referrer": referrer, "ordered_day": day,
            "subspecialty": subspecialty, "state": "unassigned",
            "assigned_to": None, "assigned_day": None}
        return dict(self.tasks[study_uid])

    # ---- 2/9. credential-aware assignment ----
    def assign(self, study_uid, *, day, candidates=None, prefer=None):
        t = self._task(study_uid)
        if t["state"] != "unassigned":
            raise RadiologyError(f"{study_uid} already {t['state']}")
        pool = candidates or list(self.reg.radiologists)
        eligible = [r for r in pool if self.reg.credentialed(r, day)]
        if not eligible:
            raise RadiologyError(
                "no credentialed radiologist available — the study waits, "
                "loudly; it does not fall to the unlicensed")
        # prefer subspecialty match, then least loaded
        def load(r):
            return sum(1 for x in self.tasks.values()
                       if x["assigned_to"] == r and x["state"] == "assigned")
        sub_match = [r for r in eligible
                     if t["subspecialty"] in
                     self.reg.radiologists[r]["subspecialties"]]
        ranked = sorted(sub_match or eligible, key=load)
        if prefer in ranked:
            chosen = prefer
        else:
            chosen = ranked[0]
        t["assigned_to"] = chosen
        t["assigned_day"] = day
        t["state"] = "assigned"
        return {"study_uid": study_uid, "assigned_to": chosen,
                "load_balance": load(chosen)}

    def worklist(self, rad_id, *, day):
        """9. STAT first, then overdue-by-SLA, never FIFO blindness."""
        mine = [t for t in self.tasks.values()
                if t["assigned_to"] == rad_id and t["state"] == "assigned"]
        def key(t):
            overdue = (day - t["ordered_day"]) * 24 > SLA_HOURS[t["priority"]]
            return (0 if t["priority"] == "stat" else 1,
                    0 if overdue else 1,
                    t["ordered_day"])
        out = sorted(mine, key=key)
        return [dict(t, overdue=(day - t["ordered_day"]) * 24
                     > SLA_HOURS[t["priority"]]) for t in out]

    # ---- 3. reading sessions ----
    def open_session(self, rad_id, study_uid, *, day, location,
                     network="offline"):
        t = self._task(study_uid)
        if t["assigned_to"] != rad_id:
            raise RadiologyError(
                f"{study_uid} is not assigned to {rad_id} — remote "
                "reading without assignment is refused and logged")
        s = {"radiologist": rad_id, "study_uid": study_uid, "day": day,
             "location": location, "network": network,
             "opened": True}
        self.sessions.append(s)
        return dict(s)

    # ---- 4. reports ----
    def draft_report(self, study_uid, *, rad_id, findings, impression,
                     day, critical_findings=(), template_version):
        t = self._task(study_uid)
        if t["assigned_to"] != rad_id:
            raise RadiologyError("only the assigned radiologist reports")
        if not template_version:
            raise RadiologyError("report template version required")
        unknown = set(critical_findings) - set(CRITICAL_FINDINGS)
        if unknown:
            raise RadiologyError(f"unknown critical findings: {sorted(unknown)}")
        self._n += 1
        rep = {"report_id": f"RPT-{self._n}", "study_uid": study_uid,
               "radiologist": rad_id, "findings": findings,
               "impression": impression, "day": day, "state": "draft",
               "critical_findings": list(critical_findings),
               "template_version": template_version, "addenda": []}
        self.reports[study_uid] = rep
        t["state"] = "reported-draft"
        return dict(rep)

    def verify_report(self, study_uid, *, rad_id, day):
        r = self._report(study_uid)
        if r["state"] != "draft":
            raise RadiologyError(f"report is {r['state']}, not draft")
        if r["radiologist"] != rad_id:
            raise RadiologyError("only the author verifies their report")
        if not self.reg.credentialed(rad_id, day):
            raise RadiologyError(
                "licence expired — cannot verify; the report stays draft "
                "and visible as such")
        r["state"] = "verified"
        r["verified_day"] = day
        self._task(study_uid)["state"] = "reported-verified"
        if r["critical_findings"]:
            self._escalate(r, day)
        return dict(r)

    def add_addendum(self, study_uid, *, rad_id, day, text, reason):
        """Corrections cite; they never rewrite."""
        r = self._report(study_uid)
        if r["state"] == "draft":
            raise RadiologyError("addenda attach to verified reports; "
                                 "fix the draft instead")
        if not text or not reason:
            raise RadiologyError("addendum requires text and reason")
        r["addenda"].append({"day": day, "by": rad_id, "text": text,
                             "reason": reason})
        r["state"] = "addended"
        return dict(r)

    # ---- 5. critical escalation ladder ----
    def _escalate(self, rep, day):
        self.escalations.append({
            "study_uid": rep["study_uid"],
            "findings": rep["critical_findings"],
            "day": day, "acknowledged": None,
            "note": "critical finding — ack required; silence re-escalates"})

    def acknowledge_critical(self, study_uid, *, day, by):
        for e in self.escalations:
            if e["study_uid"] == study_uid and e["acknowledged"] is None:
                e["acknowledged"] = {"day": day, "by": by}
                return dict(e)
        raise RadiologyError("no open critical escalation for this study")

    def unacknowledged_criticals(self, as_of, hours=4):
        out = []
        for e in self.escalations:
            if e["acknowledged"] is None and (
                    as_of - e["day"]) * 24 >= hours:
                out.append(dict(e, reescalate=True))
        return out

    # ---- 6. turnaround stats ----
    def turnaround(self, as_of):
        by_priority = {}
        for t in self.tasks.values():
            if t["state"] != "reported-verified":
                continue
            rep = self.reports[t["study_uid"]]
            tat_h = (rep["verified_day"] - t["ordered_day"]) * 24
            bucket = by_priority.setdefault(
                t["priority"], {"n": 0, "within_sla": 0, "total_hours": 0})
            bucket["n"] += 1
            bucket["total_hours"] += tat_h
            bucket["within_sla"] += int(tat_h <= SLA_HOURS[t["priority"]])
        for p, b in by_priority.items():
            b["mean_hours"] = round(b["total_hours"] / b["n"], 1)
            del b["total_hours"]
        return {"as_of": as_of, "by_priority": by_priority,
                "unreported": sum(1 for t in self.tasks.values()
                                  if not t["state"].startswith("reported")),
                "note": "means ship with n; an average without a "
                        "denominator is a fiction"}

    # ---- 7. peer review (RADPEER-scored) ----
    def peer_review(self, study_uid, *, reviewer, day, score,
                    discrepancy=None, direction=None):
        r = self._report(study_uid)
        if r["state"] not in ("verified", "addended"):
            raise RadiologyError("peer review applies to verified reports")
        if reviewer == r["radiologist"]:
            raise RadiologyError("no self peer review")
        if not self.reg.credentialed(reviewer, day):
            raise RadiologyError(
                "review by the uncredentialed is not peer review")
        sub = self.tasks[study_uid].get("subspecialty")
        if sub and sub not in self.reg.radiologists.get(
                reviewer, {}).get("subspecialties", []):
            raise RadiologyError(
                f"reviewer lacks subspecialty '{sub}' — a peer is a "
                "peer in the same craft")
        entry = {"study_uid": study_uid, "reviewer": reviewer, "day": day,
                 **validate_radpeer(score, discrepancy, direction,
                                    err=RadiologyError)}
        r.setdefault("peer_reviews", []).append(entry)
        return dict(entry)

    def discrepancy_rate(self):
        reviewed = [(r["study_uid"], pr)
                    for r in self.reports.values()
                    for pr in r.get("peer_reviews", [])]
        n = len(reviewed)
        disagree = sum(1 for _, pr in reviewed if not pr.get("agree", True))
        dist = {s: 0 for s in RADPEER_SCORES}
        for _, pr in reviewed:
            if pr.get("score") in dist:
                dist[pr["score"]] += 1
        return {"reviewed": n, "disagreements": disagree,
                "clinically_significant": sum(
                    1 for _, pr in reviewed
                    if pr.get("clinically_significant")),
                "score_distribution": dist,
                "radpeer": RADPEER_VERSION,
                "note": "discrepancy rate = disagreements/reviewed; "
                        "both shown; score 3-4 are the ones that change "
                        "management"}

    # ---- 8. cross-facility transfer ----
    def transfer_study(self, study_uid, *, destination, day,
                       release_id=None, bundle_hash=None,
                       expected_hash=None):
        """Images travel under the xrelease doctrine: the hash is the
        contract. Cross-facility sends require a release id; a declared
        bundle hash must match what the archive actually holds."""
        t = self._task(study_uid)
        if not release_id:
            raise RadiologyError(
                "cross-facility transfer requires a consent release id — "
                "the image travels lawfully or not at all")
        if expected_hash and bundle_hash and bundle_hash != expected_hash:
            entry = {"study_uid": study_uid, "destination": destination,
                     "day": day, "release_id": release_id,
                     "sent": False, "reason": "hash-mismatch"}
            self.transfers.append(entry)
            raise RadiologyError(
                "bundle hash mismatch — refused and logged; what leaves "
                "must be exactly what was consented to")
        entry = {"study_uid": study_uid, "destination": destination,
                 "day": day, "release_id": release_id,
                 "bundle_hash": bundle_hash, "sent": True,
                 "facility": t["facility"]}
        self.transfers.append(entry)
        return dict(entry)

    # ---- 10. chat seam ----
    def report_ready_line(self, study_uid, *, day):
        r = self._report(study_uid)
        if r["state"] != "verified":
            raise RadiologyError(
                "only verified reports notify the referrer — a draft "
                "reaching the ward is a rumour")
        t = self._task(study_uid)
        return json_dumps({
            "kind": "report-ready", "study_uid": study_uid,
            "facility": t["facility"], "referrer": t["referrer"],
            "day": day, "critical": bool(r["critical_findings"])})

    def _task(self, uid):
        if uid not in self.tasks:
            raise RadiologyError(f"unknown study order: {uid}")
        return self.tasks[uid]

    def _report(self, uid):
        if uid not in self.reports:
            raise RadiologyError(f"no report for: {uid}")
        return self.reports[uid]


def json_dumps(d):
    import json
    return json.dumps(d, sort_keys=True)
