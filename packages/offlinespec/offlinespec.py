"""offlinespec.py — offline specialist reading: the bundle is a
checkout, the sync is a reconciliation.

The platform's doctrine is offline-first for the edge; the specialist
side has the same problem with a sharper edge: a reader on a night
train reads from a CHECKED-OUT bundle, and what comes back must
reconcile against what happened while they were gone.

Doctrine:
  - CHECKOUT IS A CLAIM: a case in a bundle is locked to that reader
    for the bundle's life — two readers drafting the same case in two
    offline bundles is how reports collide
  - the bundle records what the case looked like at checkout (state +
    content hash): sync reconciles against THAT, not against hope
  - CONFLICTS ARE LOUD, NEVER MERGED: if the case changed while
    offline (verified by someone else, cancelled, escalated), the
    offline draft is kept on the record AND flagged — the lab
    notebook does not erase, and it does not pretend the collision
    didn't happen
  - OFFLINE IS NOT AN ALIBI: the SLA clock runs from the order, not
    from the sync; turnaround after a long flight is honestly late
"""

import hashlib
import json

OFFLINE_VERSION = "offline-reading@1.0.0"


class OfflineError(Exception):
    pass


class OfflineReader:
    def __init__(self, reading_svc):
        self.svc = reading_svc
        self.bundles = {}      # bundle_id -> bundle

    def _fingerprint(self, case):
        blob = json.dumps({k: case.get(k) for k in
                           ("case_id", "state", "assigned_to",
                            "priority", "question")},
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def checkout(self, bundle_id, reader_id, case_ids, *, day):
        """Claim cases for offline reading. Every case must exist, be
        assignable to this reader (unassigned → assigned now; assigned
        to someone else → refuse), and be credentialed for its scope —
        offline does not suspend licensure."""
        if bundle_id in self.bundles:
            raise OfflineError(f"duplicate bundle: {bundle_id}")
        if not case_ids:
            raise OfflineError("an empty bundle reads nothing")
        if not self.svc.readers.credentialed(reader_id, day):
            raise OfflineError(
                f"{reader_id} is not credentialed today — offline "
                "does not suspend licensure")
        cases = {}
        for cid in case_ids:
            t = self.svc._case(cid)
            if t["state"] == "unassigned":
                self.svc.assign(cid, day=day, prefer=reader_id)
                t = self.svc._case(cid)
            if t["assigned_to"] != reader_id:
                raise OfflineError(
                    f"{cid} is assigned to {t['assigned_to']} — two "
                    "readers drafting one case in two bundles is how "
                    "reports collide")
            if t["state"] not in ("assigned", "in-progress"):
                raise OfflineError(
                    f"{cid} is {t['state']} — only live cases check "
                    "out; a verified case does not travel")
            cases[cid] = {"fingerprint": self._fingerprint(t)}
        self.bundles[bundle_id] = {
            "bundle_id": bundle_id, "reader": reader_id, "day": day,
            "cases": cases, "drafts": {}, "synced": False,
            "offline_version": OFFLINE_VERSION}
        return dict(self.bundles[bundle_id])

    def draft_offline(self, bundle_id, case_id, *, findings, impression,
                      template_version, day):
        """A report drafted against the bundle. Only cases IN the
        bundle, only by the bundle's reader, and the template version
        is recorded at draft time — the report is interpretable only
        against the template that shaped it."""
        b = self._b(bundle_id)
        if case_id not in b["cases"]:
            raise OfflineError(f"{case_id} is not in this bundle — an "
                               "offline draft for a case never checked "
                               "out is a report written from memory")
        if case_id in b["drafts"]:
            raise OfflineError("draft exists — a correction offline is "
                               "an addendum at sync, not a rewrite")
        b["drafts"][case_id] = {"findings": findings,
                                "impression": impression,
                                "template_version": template_version,
                                "day": day}
        return dict(b["drafts"][case_id])

    def sync_back(self, bundle_id, *, day):
        """Reconcile the bundle against the live service. For each
        draft: fingerprint unchanged → land as a service draft.
        Changed → the offline draft is KEPT and flagged as a
        conflict; it is never silently merged and never silently
        dropped."""
        b = self._b(bundle_id)
        if b["synced"]:
            raise OfflineError("bundle already synced — a second sync "
                               "is a second copy, not a safety net")
        landed, conflicts = [], []
        for cid, draft in b["drafts"].items():
            t = self.svc._case(cid)
            if self._fingerprint(t) != b["cases"][cid]["fingerprint"]:
                conflicts.append({"case_id": cid, "state_now":
                                  t["state"], "offline_draft": draft,
                                  "note": "kept on the record; the "
                                          "collision is the finding"})
                continue
            rep = self.svc.draft_report(
                cid, reader_id=b["reader"], findings=draft["findings"],
                impression=draft["impression"], day=draft["day"],
                template_version=draft["template_version"])
            landed.append(rep["report_id"])
        b["synced"] = True
        return {"landed": landed, "conflicts": conflicts,
                "note": "the SLA clock ran from the order, not the "
                        "sync — offline is not an alibi; turnaround "
                        "honestly includes the flight"}

    def _b(self, bid):
        if bid not in self.bundles:
            raise OfflineError(f"unknown bundle: {bid}")
        return self.bundles[bid]
