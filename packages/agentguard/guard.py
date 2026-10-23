"""guard.py — the agent guardrail envelope (all 7 agents travel in this).

Agent doctrine, codified:
- An agent is a process with SCOPES, like a user. Declared at
  construction, enforced on every output. An agent holding no
  "prescribe" scope can never emit a prescription draft — not by
  prompt, not by bug.
- Every agent output is a SIGNED DRAFT. It becomes real only when a
  human with the required cadre/scope confirms it. The confirmation
  binds: the draft's signature, the confirmer, the timestamp.
- An agent can never widen a human's scope or narrow an escalation.
  "The model said so" is not an authority.
- provider=None is a first-class regime: agents fall back to
  deterministic behaviour and SAY SO ("llm": "unavailable") — never
  fake fluency.
"""
import hashlib
import hmac
import json


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sign(body, key):
    return hmac.new(key, _canon(body), hashlib.sha256).hexdigest()


class ScopeViolation(Exception):
    pass


class Agent:
    """Base envelope. Subclasses implement _work() and declare scopes."""

    NAME = "agent"
    SCOPES = frozenset()          # what this agent may produce
    REQUIRES_HUMAN_SCOPE = None   # scope the confirming human must hold

    def __init__(self, key, provider=None):
        self.key = key
        self.provider = provider  # None => deterministic fallback

    def _check_output_scope(self, kind):
        if kind not in self.SCOPES:
            raise ScopeViolation(
                f"{self.NAME} holds no scope for {kind!r} — refused")

    def draft(self, kind, payload, now=None):
        """The ONLY way an agent emits anything: signed, scoped, marked
        with its intelligence regime."""
        self._check_output_scope(kind)
        llm_active = bool(self.provider and getattr(self.provider, "available", False))
        body = {"type": f"agentdraft.{self.NAME}", "kind": kind,
                "payload": payload,
                "llm": "active" if llm_active else "unavailable",
                "at": now}
        return {**body, "sig": _sign(body, self.key),
                "confirmed": None}

    def confirm(self, draft, human, human_scopes, now=None):
        """Human confirmation binds the draft into effect. Fail-closed:
        tampered drafts, wrong human scope, double-confirm all refuse."""
        if draft.get("confirmed"):
            return {"ok": False, "reason": "already-confirmed"}
        body = {k: v for k, v in draft.items() if k not in ("sig", "confirmed")}
        if not hmac.compare_digest(_sign(body, self.key), draft.get("sig", "")):
            return {"ok": False, "reason": "draft-tampered"}
        if self.REQUIRES_HUMAN_SCOPE and self.REQUIRES_HUMAN_SCOPE not in human_scopes:
            return {"ok": False, "reason": "human-scope-insufficient"}
        draft["confirmed"] = {"by": human, "scopes": sorted(human_scopes), "at": now}
        return {"ok": True, "confirmed": draft["confirmed"]}
