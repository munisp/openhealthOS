"""policyeval.py — offline policy evaluation (OPE): prove a policy is
better BEFORE it ships.

Every learning loop on the platform (nudges, batch rules, restock drafts)
already logs its decisions and their rewards — accepted/refused,
confirmed/overridden, paid/queried. That log is the raw material of
reinforcement learning. What separates medicine from ad-tech is that a
new policy must prove itself on LAST month's log before it touches THIS
month's patient. That proof is offline policy evaluation.

Method: inverse propensity scoring (IPS), the classical honest estimator:

    V(policy) ≈ mean over events of  1[policy(context) == action_logged]
                                     * reward / propensity_logged

plus the self-normalised variant (SNIPS), a bootstrap confidence
interval, and the effective sample size (ESS) — an IPS estimate built on
three well-matched events is noise wearing a suit, so ESS gates verdicts.

Doctrine (the platform's, applied to statistics):
  - FAIL CLOSED: an event whose logged propensity is missing, zero, or
    covers an action the candidate policy wants is unusable. If unusable
    events exceed the refusal budget, the EVALUATION is refused — an
    uncitable estimate is unanswered, exactly like the mentor's doctrine;
  - CONSERVATIVE PROMOTION: promote only when the candidate's lower CI
    bound beats the incumbent's point estimate. "Probably better" is not
    better;
  - GOVERNED: a promotion verdict is a signed artifact with the log and
    policy fingerprints — promotion rides packreview's lifecycle, not a
    developer's confidence.
"""
from __future__ import annotations

import hashlib
import json
import random


class EvalError(Exception):
    pass


# ------------------------------------------------------------- log shape

REQUIRED_EVENT = ("context", "action", "propensity", "reward")


def validate_log(events: list[dict]) -> list[str]:
    """Shape-check a decision log. Returns issues, never raises — the
    caller decides whether the log is usable at all."""
    issues = []
    for i, e in enumerate(events):
        for f in REQUIRED_EVENT:
            if f not in e:
                issues.append(f"event[{i}]: missing {f}")
        p = e.get("propensity")
        if p is not None and not (0 < p <= 1):
            issues.append(f"event[{i}]: propensity {p} outside (0,1]")
        r = e.get("reward")
        if r is not None and not isinstance(r, (int, float)):
            issues.append(f"event[{i}]: reward must be numeric")
    return issues


def _fp(o) -> str:
    return hashlib.sha256(
        json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------- estimator

def _weights(events, policy, refusal_budget):
    """(weights, rewards, n_off_policy). Events where the candidate
    disagrees with the logged action are NORMAL — IPS simply gives them
    weight 0 and they shrink the ESS, which is the honest cost of
    evaluating off-policy. What fails closed is methodologically BROKEN
    data: missing/zero propensities, over the refusal budget."""
    weights, rewards, off_policy, invalid = [], [], 0, 0
    for e in events:
        if not (0 < e["propensity"] <= 1):
            invalid += 1
            continue
        if policy(e["context"]) != e["action"]:
            off_policy += 1
            continue
        weights.append(1.0 / e["propensity"])
        rewards.append(float(e["reward"]))
    if len(events) and invalid / len(events) > refusal_budget:
        raise EvalError(
            f"{invalid}/{len(events)} events have unusable propensities "
            f"(budget {refusal_budget:.0%}) — the logging policy was not "
            "recorded honestly; refusing to estimate")
    return weights, rewards, off_policy


def evaluate(events: list[dict], policy, *, min_ess: float = 30.0,
             refusal_budget: float = 0.5, bootstraps: int = 400,
             seed: int = 20261024, incumbent: float | None = None) -> dict:
    """Evaluate a candidate policy (callable context -> action) on a
    logged decision history. Returns the estimate, CI, ESS, and a
    promotion verdict when an incumbent baseline is supplied."""
    issues = validate_log(events)
    if issues:
        raise EvalError("log failed validation: " + "; ".join(issues[:3]))
    if not events:
        raise EvalError("empty log — nothing to learn from")

    weights, rewards, off_policy = _weights(events, policy, refusal_budget)
    n = len(weights)
    if n == 0:
        raise EvalError("no usable events — the log never chose what this "
                        "policy chooses; refusing to estimate")

    total = len(events)  # IPS divides by ALL logged events: matched events
    # carry weight 1/propensity, unmatched carry weight 0. Dividing by the
    # matched count would double-count propensity and inflate the estimate.
    ips = sum(w * r for w, r in zip(weights, rewards)) / total
    snips = sum(w * r for w, r in zip(weights, rewards)) / sum(weights)
    ess = (sum(weights) ** 2) / sum(w * w for w in weights)

    # bootstrap CI on the IPS estimate (resample the full event stream)
    rng = random.Random(seed)
    boots = []
    pairs = list(zip(weights, rewards))
    for _ in range(bootstraps):
        s = sum(w * r for w, r in
                (pairs[rng.randrange(n)] for _ in range(n)))
        boots.append(s / total)
    boots.sort()
    lo = boots[int(0.025 * bootstraps)]
    hi = boots[min(int(0.975 * bootstraps), bootstraps - 1)]

    state = "ready" if ess >= min_ess else "insufficient-evidence"

    verdict = None
    if incumbent is not None:
        if state != "ready":
            verdict = {"verdict": "hold",
                       "reason": f"ESS {ess:.0f} below floor {min_ess:.0f} — "
                                 "collect more log, do not ship a guess"}
        elif lo > incumbent:
            verdict = {"verdict": "promote",
                       "reason": f"lower CI {lo:.3f} clears incumbent "
                                 f"{incumbent:.3f} — conservative pass"}
        else:
            verdict = {"verdict": "hold",
                       "reason": f"lower CI {lo:.3f} does not clear "
                                 f"incumbent {incumbent:.3f}"}

    return {
        "estimate_ips": ips,
        "estimate_snips": snips,
        "ci95": [lo, hi],
        "ess": ess,
        "usable_events": n,
        "off_policy_events": off_policy,
        "state": state,
        "verdict": verdict,
        "fingerprints": {
            "log": _fp(events),
            # policies are callables: fingerprint the decisions they make
            # on this log's contexts — the honest identity of a function
            "policy_decisions": _fp([policy(e["context"]) for e in events]),
        },
    }


# ------------------------------------------------- adapters from reality

def from_nudge_log(records: list[dict]) -> list[dict]:
    """NudgeRanker observations -> decision events.
    records: [{context, action, accepted, propensity}] — propensity is
    1/len(candidates) at suggest() time (uniform exploration); callers
    MUST record it, a reconstructed propensity is a fiction."""
    out = []
    for i, r in enumerate(records):
        if "propensity" not in r:
            raise EvalError(
                f"nudge record {i} has no logged propensity — a "
                "reconstructed propensity is a fiction; refusing. Log "
                "propensity at suggest() time")
        out.append({"context": r["context"], "action": r["action"],
                    "propensity": r["propensity"],
                    "reward": 1.0 if r["accepted"] else 0.0})
    return out


# ------------------------------------------------------------- promotion

class PromotionLog:
    """Governance for policy promotion, mirroring packreview: a candidate
    verdict becomes LIVE only through a scoped human approval, and the
    author of the candidate cannot approve their own. Entries are
    fingerprinted — the approved artifact is exactly what was evaluated."""

    REVIEWER_ROLES = {"medical-officer", "consultant", "nmcn-panel",
                      "data-steward"}

    def __init__(self):
        self.entries = []  # {candidate_fp, result_fp, state, history}

    def submit(self, candidate_name: str, result: dict, author: str) -> dict:
        if result.get("verdict", {}).get("verdict") != "promote":
            raise EvalError(
                "only 'promote' verdicts enter governance — a 'hold' is "
                "the system working, not a failure to route around")
        fp = _fp({"name": candidate_name, "result": result})
        entry = {"candidate": candidate_name, "fingerprint": fp,
                 "author": author, "state": "draft",
                 "history": [{"event": "submitted", "by": author}]}
        self.entries.append(entry)
        return entry

    def approve(self, fingerprint: str, approver: str, role: str,
                note: str) -> dict:
        if role not in self.REVIEWER_ROLES:
            raise EvalError(f"insufficient reviewer role: {role}")
        if not note or not note.strip():
            raise EvalError("approval note required — the reason IS the record")
        for e in self.entries:
            if e["fingerprint"] == fingerprint:
                if e["author"] == approver:
                    raise EvalError("author cannot self-approve")
                if e["state"] != "draft":
                    raise EvalError(f"cannot approve from state {e['state']}")
                e["state"] = "live"
                e["history"].append({"event": "approved", "by": approver,
                                     "role": role, "note": note})
                return e
        raise EvalError(f"no such candidate: {fingerprint}")
