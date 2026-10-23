"""flow.py — the NHIA e-claims submission lifecycle, end to end.

nhiaclaims.py builds and validates an envelope; adapters/nhia.py can POST
one claim. What was missing is everything between "validated" and "money":
the durable outbox a facility actually runs on.

Lifecycle (one ledger file per submission, hash-named, atomic writes):

    staged -> submitting -> submitted -> reconciling -> closed

Doctrine:
  - only CONFORMANT envelopes may leave staging — a submission the scheme
    will bounce wastes the one trip to town the facility makes this week;
  - idempotent by submission_hash: staging the same envelope twice returns
    the existing ledger; claims carry deterministic ids
    (submission_hash + index) so the adapter's own idempotency composes;
  - the ledger is integrity-protected (sidecar sha256): a tampered ledger
    — a claim quietly marked "paid" — fails LOUD on load, never silently;
  - reconciliation refuses unknown references: money that cannot be matched
    to a claim we submitted is reported, not absorbed;
  - live mode is one flag on the adapter; everything here is identical
    sandbox or live — the outbox does not care who answers the POST.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

from nhiaclaims import build_submission

TERMINAL = {"paid", "rejected"}


class FlowError(Exception):
    pass


def _atomic_write(path: str, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SubmissionFlow:
    def __init__(self, outbox_dir: str, adapter):
        self.dir = outbox_dir
        self.adapter = adapter  # NHIAAdapter (sandbox or live) or duck-type
        os.makedirs(outbox_dir, exist_ok=True)

    # -------------------------------------------------- ledger io

    def _paths(self, sub_hash: str):
        return (os.path.join(self.dir, f"{sub_hash}.json"),
                os.path.join(self.dir, f"{sub_hash}.sha256"))

    def _save(self, ledger: dict):
        body = json.dumps(ledger, indent=2, sort_keys=True).encode()
        path, sidecar = self._paths(ledger["submission_hash"])
        _atomic_write(path, body)
        _atomic_write(sidecar, hashlib.sha256(body).hexdigest().encode())

    def load(self, sub_hash: str) -> dict:
        path, sidecar = self._paths(sub_hash)
        if not os.path.exists(path):
            raise FlowError(f"no such submission: {sub_hash}")
        with open(path, "rb") as f:
            body = f.read()
        if os.path.exists(sidecar):
            with open(sidecar) as f:
                want = f.read().strip()
            if hashlib.sha256(body).hexdigest() != want:
                raise FlowError(
                    f"ledger {sub_hash} failed its integrity check — "
                    "refusing to trust a tampered claims record")
        return json.loads(body)

    # -------------------------------------------------- lifecycle

    def stage(self, scheme: str, facility_id: str, period: str,
              claims: list[dict], tariff_check=None) -> dict:
        """Validate and persist a submission envelope. Idempotent."""
        built = build_submission(scheme, facility_id, period, claims,
                                 tariff_check)
        sub = built["submission"]
        h = sub["submission_hash"]
        try:
            return self.load(h)  # already staged — same envelope, same hash
        except FlowError:
            pass
        ledger = {
            "submission_hash": h,
            "scheme": scheme, "facility_id": facility_id, "period": period,
            "state": "staged",
            "conformant": built["conformant"],
            "issues": built["issues"],
            "rejected_at_source": built["rejected_claims"],
            "claims": [
                {"claim_id": f"{h}-{i}", "member_id": c.get("member_id"),
                 "amount": c.get("amount", c.get("capitation_rate", 0)),
                 "reference": None, "status": "staged"}
                for i, c in enumerate(sub["claims"])
            ],
            "history": [{"at_state": "staged",
                         "note": f"{len(sub['claims'])} clean of "
                                 f"{len(claims)} received"}],
        }
        self._save(ledger)
        return ledger

    def submit(self, sub_hash: str) -> dict:
        """Push every staged claim through the adapter. Refuses
        non-conformant envelopes. Safe to re-call: only claims without a
        reference are (re)sent, and the adapter dedupes by claim_id."""
        ledger = self.load(sub_hash)
        if ledger["state"] not in ("staged", "submitting", "submitted"):
            raise FlowError(f"cannot submit from state {ledger['state']}")
        if not ledger["conformant"]:
            ledger["state"] = "staged"
            ledger["history"].append(
                {"at_state": "staged",
                 "note": "submission refused: envelope is not conformant — "
                         "fix the issues, do not ship a bounce"})
            self._save(ledger)
            raise FlowError("envelope is not conformant: "
                            + "; ".join(ledger["issues"][:3]))
        ledger["state"] = "submitting"
        decision = {"decision": "APPROVE"}
        for c in ledger["claims"]:
            if c["reference"]:
                continue
            res = self.adapter.submit_claim(
                {"claim_id": c["claim_id"], "member_id": c["member_id"],
                 "amount": c["amount"]}, decision)
            if res.status == "error":
                ledger["history"].append(
                    {"at_state": "submitting",
                     "note": f"{c['claim_id']}: {res.detail}"})
                self._save(ledger)
                raise FlowError(f"submit failed for {c['claim_id']}: "
                                f"{res.detail}")
            c["reference"] = res.reference
            c["status"] = "duplicate" if res.status == "duplicate" else "received"
        ledger["state"] = "submitted"
        ledger["history"].append(
            {"at_state": "submitted",
             "note": f"{len(ledger['claims'])} claims acknowledged"})
        self._save(ledger)
        return ledger

    def reconcile(self, sub_hash: str, remittance: list[dict]) -> dict:
        """Match the scheme's remittance advice against the ledger.
        remittance rows: {reference, outcome: paid|queried|rejected,
                          amount_paid?, reason?}.
        Unknown reference -> FlowError: unmatched money is reported,
        never absorbed."""
        ledger = self.load(sub_hash)
        if ledger["state"] not in ("submitted", "reconciling"):
            raise FlowError(
                f"cannot reconcile from state {ledger['state']}")
        by_ref = {c["reference"]: c for c in ledger["claims"]}
        report = {"paid": 0, "queried": 0, "rejected": 0,
                  "amount_claimed": sum(c["amount"] for c in ledger["claims"]),
                  "amount_paid": 0.0, "unmatched_money": []}
        for row in remittance:
            ref = row.get("reference", "")
            c = by_ref.get(ref)
            if c is None:
                report["unmatched_money"].append(
                    {"reference": ref, "amount": row.get("amount_paid", 0)})
                raise FlowError(
                    f"remittance references {ref} — no such claim in this "
                    "submission; investigate before booking this money")
            outcome = row.get("outcome")
            if outcome not in ("paid", "queried", "rejected"):
                raise FlowError(f"unknown remittance outcome: {outcome}")
            c["status"] = outcome
            report[outcome] += 1
            if outcome == "paid":
                report["amount_paid"] += row.get("amount_paid", c["amount"])
                if row.get("reason"):
                    c["reason"] = row["reason"]
            else:
                c["reason"] = row.get("reason", "")
        ledger["state"] = "reconciling"
        if all(c["status"] in TERMINAL for c in ledger["claims"]):
            ledger["state"] = "closed"
        ledger["history"].append(
            {"at_state": ledger["state"],
             "note": f"remittance: {report['paid']} paid, "
                     f"{report['queried']} queried, "
                     f"{report['rejected']} rejected"})
        ledger["report"] = report
        self._save(ledger)
        return {"ledger": ledger, "report": report}
