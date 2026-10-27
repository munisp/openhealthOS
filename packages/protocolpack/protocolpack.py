"""protocolpack.py — Standing Orders as code: scope-safe task-shifting.

CHWs are the majority of the PHC workforce and most have no doctor on
site. The national answer is protocol-guided care — but a protocol in a
binder relies on judgment at the exact moment judgment is scarcest.
Here the scope boundary is CODE: a CHW stepping outside their protocol
doesn't get a warning, they get a mandatory escalation.

Doctrine:
- Protocols are DATA (versioned packs), the engine is code. A pack
  version is pinned in every session record — accountability survives.
- Scope is enforced structurally: each step declares who may perform it
  (chw / nurse / doctor). perform() with the wrong cadre RAISES, and
  the session records the escalation — never a silent override.
- Red flags are evaluated on every answer; any hit ends the session
  with escalate=True, no matter how many steps remain.
"""

class ScopeError(Exception):
    pass

CADRE_RANK = {"chw": 1, "nurse": 2, "doctor": 3}


class Session:
    def __init__(self, pack, cadre):
        if cadre not in CADRE_RANK:
            raise ScopeError(f"unknown cadre: {cadre}")
        if not pack.get("version"):
            raise ValueError("packs must be versioned")
        self.pack = pack
        self.cadre = cadre
        self.step_id = pack["start"]
        self.path = []
        self.done = False
        self.escalate = False
        self.escalation_reason = None

    def _step(self):
        return self.pack["steps"][self.step_id]

    def question(self):
        s = self._step()
        return {"id": self.step_id, "text": s["text"],
                "options": list(s.get("options", {}))}

    def answer(self, option_id, now=None):
        s = self._step()
        if option_id not in s.get("options", {}):
            raise ValueError(f"unknown option: {option_id}")
        # red flags checked on EVERY answer, before anything else
        flags = set(self.pack.get("red_flags", {}).get(self.step_id, []))
        if option_id in flags:
            self.done = True
            self.escalate = True
            self.escalation_reason = f"red-flag: {option_id}"
            # a red-flagged option may still name its classification —
            # record it so the escalation carries the protocol outcome
            nxt = s["options"][option_id]
            if isinstance(nxt, dict) and nxt.get("outcome"):
                self.outcome = nxt["outcome"]
            self.path.append((self.step_id, option_id))
            return self._state(now)
        nxt = s["options"][option_id]
        self.path.append((self.step_id, option_id))
        if isinstance(nxt, dict) and nxt.get("outcome"):
            self.done = True
            self.outcome = nxt["outcome"]
            self.escalate = bool(nxt.get("escalate"))
            if self.escalate:
                self.escalation_reason = "protocol-outcome"
        else:
            self.step_id = nxt
        return self._state(now)

    def perform(self, action, now=None):
        """Scope gate: the action's minimum cadre must be met. A CHW
        attempting a doctor action triggers mandatory escalation —
        recorded, not just refused."""
        need = self.pack["actions"][action]["min_cadre"]
        if CADRE_RANK[self.cadre] < CADRE_RANK[need]:
            self.done = True
            self.escalate = True
            self.escalation_reason = f"scope-boundary: {action} needs {need}"
            self.path.append(("ACTION-DENIED", action))
            raise ScopeError(f"{action} requires {need}; escalating")
        self.path.append(("ACTION", action))
        return {"ok": True, "action": action}

    def _state(self, now):
        return {"done": self.done, "escalate": self.escalate,
                "reason": self.escalation_reason,
                "outcome": getattr(self, "outcome", None),
                "path": list(self.path), "pack_version": self.pack["version"]}
