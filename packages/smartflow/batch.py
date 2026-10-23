"""batch.py — batch review with exception routing.

Stocktakes, immunisation registers, campaign follow-ups arrive in piles:
40 near-identical rows. The productive pattern is not 40 form-fills —
it is ONE review where rules pre-check every row, the rows that pass
are grouped for a single confirm, and the exceptions get full human
attention. The adjudicator doctrine applied to clerical work: rules
handle the routine, humans handle the exceptions.

Fail-closed rules:
- A row failing ANY check is an exception. Exceptions are never
  suggested-approved, never hidden, and listed first.
- "suggested" means exactly that: nothing is approved until the reviewer
  explicitly confirms the batch. The confirmation records who, when,
  and the rule-set version — accountability survives the batching.
- Rule versions change verdicts; a batch computed under an old rule
  version is stale and must be re-checked.
"""


class BatchReview:
    """checks: {name: fn(row) -> bool}. A row must pass ALL to be
    suggest-approvable."""

    RULE_VERSION = 1  # bump when checks change; stale batches refuse

    def __init__(self, kind, checks, rule_version=1):
        self.kind = kind
        self.checks = checks
        self.rule_version = rule_version
        self.rows = []          # [{id, row, verdict, failed: [...]}]
        self.confirmed = None

    def load(self, rows):
        for r in rows:
            failed = [name for name, fn in self.checks.items() if not fn(r)]
            self.rows.append({
                "id": r.get("id"),
                "verdict": "exception" if failed else "suggested-approve",
                "failed": failed,
                "row": r,
            })
        return self

    def review(self):
        """What the reviewer sees: exceptions FIRST, then the approvable
        group as a count + sample — the pile has become a decision."""
        exc = [r for r in self.rows if r["verdict"] == "exception"]
        ok = [r for r in self.rows if r["verdict"] == "suggested-approve"]
        return {
            "kind": self.kind,
            "rule_version": self.rule_version,
            "total": len(self.rows),
            "exceptions": [{"id": r["id"], "failed": r["failed"]} for r in exc],
            "suggested_approve": {"count": len(ok),
                                  "ids": [r["id"] for r in ok]},
        }

    def confirm(self, reviewer, rule_version, now=None):
        """Approve the suggested group. Fail-closed: wrong rule version
        (checks changed since load) or double-confirm are refused."""
        if rule_version != self.rule_version:
            return {"ok": False, "reason": "stale-rules-recheck-required"}
        if self.confirmed:
            return {"ok": False, "reason": "already-confirmed"}
        approved = [r["id"] for r in self.rows
                    if r["verdict"] == "suggested-approve"]
        self.confirmed = {"reviewer": reviewer, "at": now,
                          "rule_version": rule_version, "approved": approved}
        return {"ok": True, "approved": approved,
                "exceptions_left": [r["id"] for r in self.rows
                                    if r["verdict"] == "exception"]}
