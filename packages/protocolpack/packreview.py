"""packreview.py — clinical sign-off lifecycle for protocol packs.

A pack is medical guidance executed by non-doctors; its lifecycle is
therefore clinical governance, not code deployment:

  draft -> under-review -> live          (or -> rejected, with reasons)

Doctrine:
- Only LIVE packs may start sessions in production. Seed packs run
  only where explicitly allowed (training, sandboxes) and every session
  record carries the pack status — nobody can later claim they didn't
  know it was unreviewed.
- Sign-off is attributable: reviewer id, role, and note. A reviewer
  cannot approve their own pack (author != reviewer, fail-closed).
- Any edit after sign-off returns the pack to draft — silently edited
  live protocols are how patients get hurt.
- Rejection requires a reason; approval requires a note. Governance
  without a paper trail is theatre.
"""
import hashlib
import hmac
import json

from protocolpack import Session


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode()


class PackRegistry:
    def __init__(self, key):
        self.key = key
        self.packs = {}  # name -> {pack, author, status, history[], fingerprint}

    def _fp(self, pack):
        return hashlib.sha256(_canon(pack)).hexdigest()

    def register(self, name, pack, author, now=None):
        fp = self._fp(pack)
        existing = self.packs.get(name)
        if existing and existing["fingerprint"] != fp \
                and existing["status"] == "live":
            # edited after sign-off: back to draft, honestly recorded
            existing["status"] = "draft"
            existing["history"].append({"event": "edit-after-signoff",
                                        "at": now})
        self.packs[name] = {"pack": pack, "author": author,
                            "status": self.packs.get(name, {}).get("status", "draft")
                            if existing and existing["fingerprint"] == fp else "draft",
                            "history": (existing["history"] if existing else [])
                            + [{"event": "registered", "by": author, "at": now}],
                            "fingerprint": fp}
        return self.packs[name]

    def review(self, name, reviewer, role, approve, note, now=None):
        p = self.packs.get(name)
        if not p:
            return {"ok": False, "reason": "unknown-pack"}
        if reviewer == p["author"]:
            return {"ok": False, "reason": "author-cannot-self-approve"}
        if not note:
            return {"ok": False, "reason": "note-required"}
        if role not in ("medical-officer", "consultant", "nmcn-panel"):
            return {"ok": False, "reason": "insufficient-reviewer-role"}
        p["status"] = "live" if approve else "rejected"
        p["history"].append({"event": "approved" if approve else "rejected",
                             "by": reviewer, "role": role, "note": note,
                             "at": now})
        return {"ok": True, "status": p["status"]}

    def session(self, name, cadre, allow_seed=False):
        """Start a protocol session. Fail-closed on pack status."""
        p = self.packs.get(name)
        if not p:
            raise KeyError(f"unknown pack: {name}")
        if p["status"] == "rejected":
            raise PermissionError(f"pack {name} was rejected — unusable")
        if p["status"] != "live" and not allow_seed:
            raise PermissionError(
                f"pack {name} is {p['status']} — clinical sign-off required")
        s = Session(p["pack"], cadre)
        s.pack_status = p["status"]  # recorded on the session, honestly
        return s
