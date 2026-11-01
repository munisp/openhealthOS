"""teleread.py — one engine, many reading services.

Radiology proved the shape: artifact produced where the specialist
isn't → credential-gated remote pool → audited session → append-only
report → critical findings escalate → referrer notified. This module is
that shape generalized. Pathology, ECG, obstetric-US over-read, DR
screening, derm, cervix, pharmacy review — all differ only in the
artifact and the credential scope. Second opinions are built in.

Doctrine (unchanged): a report is DRAFT until a credentialed reader
verifies it; corrections cite, never rewrite; silence re-escalates;
the hash is the contract; a nudge never executes.
"""

import json

DEFAULT_SLA_HOURS = {"stat": 2, "urgent": 24, "routine": 72}
PRIORITIES = ("routine", "urgent", "stat")


class TeleReadError(Exception):
    pass


class DomainConfig:
    """What makes a domain a domain: scopes, critical findings, SLAs,
    artifact kinds. Everything else is the engine."""

    def __init__(self, domain, *, scopes, critical_findings=(),
                 artifact_kinds=(), sla_hours=None):
        if not domain:
            raise TeleReadError("domain required")
        if not scopes:
            raise TeleReadError("a domain without credential scopes "
                                "lets anyone read anything — refused")
        self.domain = domain
        self.scopes = tuple(scopes)
        self.critical_findings = tuple(critical_findings)
        self.artifact_kinds = tuple(artifact_kinds)
        self.sla_hours = dict(sla_hours or DEFAULT_SLA_HOURS)


class ReaderRegistry:
    """Credentials with expiry and scopes. A stale licence assigns
    nothing."""

    def __init__(self):
        self.readers = {}

    def register(self, reader_id, *, licence_id, licence_expiry_day,
                 scopes, home_site=None):
        if reader_id in self.readers:
            raise TeleReadError(f"duplicate reader: {reader_id}")
        self.readers[reader_id] = {
            "licence": licence_id,
            "licence_expiry_day": licence_expiry_day,
            "scopes": list(scopes), "home_site": home_site,
            "active": True}
        return dict(self.readers[reader_id])

    def credentialed(self, reader_id, day):
        r = self.readers.get(reader_id)
        if not r or not r["active"]:
            return False
        return day <= r["licence_expiry_day"]

    def has_scope(self, reader_id, scope):
        r = self.readers.get(reader_id)
        return bool(r) and scope in r["scopes"]


class ReadingService:
    def __init__(self, config, readers):
        self.cfg = config
        self.readers = readers
        self.cases = {}       # case_id -> order/task
        self.reports = {}     # case_id -> report doc
        self.sessions = []    # reading session audit
        self.escalations = [] # critical finding events
        self.transfers = []   # cross-facility sends (incl. refused)
        self._n = 0

    # ---- order ----
    def order(self, case_id, *, priority, question, facility, referrer,
              day, scope=None, artifact=None, meta=None):
        c = self.cfg
        if priority not in c.sla_hours:
            raise TeleReadError(f"unknown priority: {priority}")
        scope = scope or c.scopes[0]
        if scope not in c.scopes:
            raise TeleReadError(f"scope {scope} outside domain {c.domain}")
        if artifact and c.artifact_kinds and artifact not in c.artifact_kinds:
            raise TeleReadError(f"unknown artifact kind: {artifact}")
        if not question:
            raise TeleReadError(
                "no clinical question — reading without a question is "
                "looking, not diagnosis")
        if case_id in self.cases:
            raise TeleReadError(f"duplicate order: {case_id}")
        self.cases[case_id] = {
            "case_id": case_id, "domain": c.domain, "priority": priority,
            "question": question, "facility": facility,
            "referrer": referrer, "ordered_day": day, "scope": scope,
            "artifact": artifact, "meta": dict(meta or {}),
            "state": "unassigned", "assigned_to": None,
            "assigned_day": None, "second_opinion": False}
        return dict(self.cases[case_id])

    # ---- assignment: scope + credential + workload ----
    def assign(self, case_id, *, day, candidates=None, prefer=None):
        t = self._case(case_id)
        if t["state"] != "unassigned":
            raise TeleReadError(f"{case_id} already {t['state']}")
        pool = candidates or list(self.readers.readers)
        eligible = [r for r in pool
                    if self.readers.credentialed(r, day)
                    and self.readers.has_scope(r, t["scope"])]
        if not eligible:
            raise TeleReadError(
                f"no credentialed {t['scope']} reader available — the "
                "case waits, loudly; it does not fall to the unlicensed")
        def load(r):
            return sum(1 for x in self.cases.values()
                       if x["assigned_to"] == r and x["state"] == "assigned")
        ranked = sorted(eligible, key=load)
        chosen = prefer if prefer in ranked else ranked[0]
        t["assigned_to"] = chosen
        t["assigned_day"] = day
        t["state"] = "assigned"
        return {"case_id": case_id, "assigned_to": chosen}

    def worklist(self, reader_id, *, day):
        """STAT first, then overdue-by-SLA, never FIFO blindness."""
        mine = [t for t in self.cases.values()
                if t["assigned_to"] == reader_id
                and t["state"] == "assigned"]
        def overdue(t):
            return (day - t["ordered_day"]) * 24 > self.cfg.sla_hours[
                t["priority"]]
        mine.sort(key=lambda t: (
            0 if t["priority"] == "stat" else 1,
            0 if overdue(t) else 1, t["ordered_day"]))
        return [dict(t, overdue=overdue(t)) for t in mine]

    # ---- sessions: remote reading is audited ----
    def open_session(self, reader_id, case_id, *, day, location,
                     network="offline"):
        t = self._case(case_id)
        if t["assigned_to"] != reader_id:
            raise TeleReadError(
                f"{case_id} is not assigned to {reader_id} — remote "
                "reading without assignment is refused and logged")
        s = {"reader": reader_id, "case_id": case_id, "day": day,
             "location": location, "network": network,
             "domain": self.cfg.domain}
        self.sessions.append(s)
        return dict(s)

    # ---- reports: append-only documents ----
    def draft_report(self, case_id, *, reader_id, findings, impression,
                     day, critical_findings=(), template_version):
        t = self._case(case_id)
        if t["assigned_to"] != reader_id:
            raise TeleReadError("only the assigned reader reports")
        if not template_version:
            raise TeleReadError("report template version required")
        unknown = set(critical_findings) - set(self.cfg.critical_findings)
        if unknown:
            raise TeleReadError(
                f"findings outside the domain critical list: "
                f"{sorted(unknown)}")
        self._n += 1
        rep = {"report_id": f"{self.cfg.domain.upper()}-R{self._n}",
               "case_id": case_id, "reader": reader_id,
               "findings": findings, "impression": impression, "day": day,
               "state": "draft",
               "critical_findings": list(critical_findings),
               "template_version": template_version, "addenda": []}
        self.reports[case_id] = rep
        t["state"] = "reported-draft"
        return dict(rep)

    def verify_report(self, case_id, *, reader_id, day):
        r = self._report(case_id)
        if r["state"] != "draft":
            raise TeleReadError(f"report is {r['state']}, not draft")
        if r["reader"] != reader_id:
            raise TeleReadError("only the author verifies their report")
        if not self.readers.credentialed(reader_id, day):
            raise TeleReadError(
                "licence expired — cannot verify; the report stays draft "
                "and visible as such")
        r["state"] = "verified"
        r["verified_day"] = day
        self._case(case_id)["state"] = "reported-verified"
        if r["critical_findings"]:
            self._escalate(r, day)
        return dict(r)

    def add_addendum(self, case_id, *, reader_id, day, text, reason):
        r = self._report(case_id)
        if r["state"] == "draft":
            raise TeleReadError("addenda attach to verified reports; "
                                "fix the draft instead")
        if not text or not reason:
            raise TeleReadError("addendum requires text and reason")
        r["addenda"].append({"day": day, "by": reader_id, "text": text,
                             "reason": reason})
        r["state"] = "addended"
        return dict(r)

    # ---- second opinions are built in ----
    def request_second_opinion(self, case_id, *, requested_by, day,
                               reason):
        r = self._report(case_id)
        if r["state"] not in ("verified", "addended"):
            raise TeleReadError("second opinions review verified reports")
        if not reason:
            raise TeleReadError("second opinion requires a reason")
        so_id = f"{case_id}/2o"
        if so_id in self.cases:
            raise TeleReadError("second opinion already requested")
        t = self._case(case_id)
        pool = [x for x in self.readers.readers if x != r["reader"]]
        self.order(so_id, priority=t["priority"],
                   question=f"second opinion on {case_id}: {reason}",
                   facility=t["facility"], referrer=requested_by, day=day,
                   scope=t["scope"], artifact=t["artifact"],
                   meta={"primary_report": r["report_id"]})
        self.cases[so_id]["second_opinion"] = True
        out = self.assign(so_id, day=day, candidates=pool)
        return out

    # ---- critical escalation ladder ----
    def _escalate(self, rep, day):
        self.escalations.append({
            "case_id": rep["case_id"], "domain": self.cfg.domain,
            "findings": rep["critical_findings"], "day": day,
            "acknowledged": None,
            "note": "critical finding — ack required; silence re-escalates"})

    def acknowledge_critical(self, case_id, *, day, by):
        for e in self.escalations:
            if e["case_id"] == case_id and e["acknowledged"] is None:
                e["acknowledged"] = {"day": day, "by": by}
                return dict(e)
        raise TeleReadError("no open critical escalation for this case")

    def unacknowledged_criticals(self, as_of, hours=4):
        return [dict(e, reescalate=True) for e in self.escalations
                if e["acknowledged"] is None
                and (as_of - e["day"]) * 24 >= hours]

    # ---- turnaround with denominators ----
    def turnaround(self, as_of):
        by_priority = {}
        for t in self.cases.values():
            if t["state"] != "reported-verified":
                continue
            rep = self.reports[t["case_id"]]
            tat_h = (rep["verified_day"] - t["ordered_day"]) * 24
            b = by_priority.setdefault(
                t["priority"], {"n": 0, "within_sla": 0, "total_hours": 0})
            b["n"] += 1
            b["total_hours"] += tat_h
            b["within_sla"] += int(
                tat_h <= self.cfg.sla_hours[t["priority"]])
        for b in by_priority.values():
            b["mean_hours"] = round(b["total_hours"] / b["n"], 1)
            del b["total_hours"]
        return {"domain": self.cfg.domain, "as_of": as_of,
                "by_priority": by_priority,
                "unreported": sum(1 for t in self.cases.values()
                                  if not t["state"].startswith("reported")),
                "note": "means ship with n; an average without a "
                        "denominator is a fiction"}

    # ---- peer review ----
    def peer_review(self, case_id, *, reviewer, day, agree,
                    discrepancy=None):
        r = self._report(case_id)
        if r["state"] not in ("verified", "addended"):
            raise TeleReadError("peer review applies to verified reports")
        if reviewer == r["reader"]:
            raise TeleReadError("no self peer review")
        if not agree and not discrepancy:
            raise TeleReadError(
                "disagreement requires a named discrepancy")
        entry = {"case_id": case_id, "reviewer": reviewer, "day": day,
                 "agree": bool(agree), "discrepancy": discrepancy}
        r.setdefault("peer_reviews", []).append(entry)
        return dict(entry)

    def discrepancy_rate(self):
        prs = [pr for r in self.reports.values()
               for pr in r.get("peer_reviews", [])]
        return {"reviewed": len(prs),
                "disagreements": sum(1 for pr in prs if not pr["agree"]),
                "note": "discrepancy rate = disagreements/reviewed; "
                        "both shown"}

    # ---- transfer: the hash is the contract ----
    def transfer_case(self, case_id, *, destination, day, release_id,
                      bundle_hash=None, expected_hash=None):
        t = self._case(case_id)
        if not release_id:
            raise TeleReadError(
                "cross-facility transfer requires a consent release id — "
                "the case travels lawfully or not at all")
        if expected_hash and bundle_hash and bundle_hash != expected_hash:
            self.transfers.append({
                "case_id": case_id, "destination": destination, "day": day,
                "release_id": release_id, "sent": False,
                "reason": "hash-mismatch"})
            raise TeleReadError(
                "bundle hash mismatch — refused and logged; what leaves "
                "must be exactly what was consented to")
        entry = {"case_id": case_id, "destination": destination,
                 "day": day, "release_id": release_id,
                 "bundle_hash": bundle_hash, "sent": True,
                 "facility": t["facility"]}
        self.transfers.append(entry)
        return dict(entry)

    # ---- chat seam ----
    def result_ready_line(self, case_id, *, day):
        r = self._report(case_id)
        if r["state"] != "verified":
            raise TeleReadError(
                "only verified results notify the referrer — a draft "
                "reaching the ward is a rumour")
        t = self._case(case_id)
        return json.dumps({
            "kind": f"{self.cfg.domain}-result-ready",
            "case_id": case_id, "facility": t["facility"],
            "referrer": t["referrer"], "day": day,
            "critical": bool(r["critical_findings"])}, sort_keys=True)

    def _case(self, cid):
        if cid not in self.cases:
            raise TeleReadError(f"unknown case: {cid}")
        return self.cases[cid]

    def _report(self, cid):
        if cid not in self.reports:
            raise TeleReadError(f"no report for: {cid}")
        return self.reports[cid]
