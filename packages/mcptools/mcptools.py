"""mcptools.py — governed agent tool surface (ADR-0060).

HealthChain exposes FHIR build/validate/read tools to agents over MCP
and LangChain. The pattern is right — agents need typed tools with
structured, inspectable errors — but an ungoverned tool surface in a
clinical platform is how a helpful agent becomes an unaccountable
writer. This module is the same surface with the platform's doctrine
bolted on (ADR-0048's gate, in miniature):

  - DENY BY DEFAULT: an unregistered tool or an unlisted actor denies.
    An unlisted tool failing open is how temporary capabilities become
    permanent shadow ones.
  - RISK CLASSES: "read" tools execute for allowlisted actors; "write"
    tools return PENDING and defer to a human — machines propose,
    people commit (fraud PEND_REVIEW, step-up elevation, agentgate's
    RequireApproval: same law).
  - STRUCTURED ERRORS AN AGENT CAN FIX: violations come back as a
    LIST, not one-at-a-time — an agent repairing one reason per retry
    is how error storms start.
  - EVERY OUTCOME IS AUDITED: allow, deny, pend, and error are
    hash-chained. A denial you can't reconstruct is a policy you
    can't defend.
  - STDIO JSON-RPC: newline-delimited JSON-RPC 2.0 (tools/list,
    tools/call) so any MCP-style client can attach; the transport is
    thin on purpose — the governance is the product.

Stdlib only (ADR-0005).
"""

from __future__ import annotations

import hashlib
import json

VERSION = "mcptools@1.0.0"

RISK_CLASSES = ("read", "write")


class ToolError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class ToolRegistry:
    def __init__(self, *, actors=()):
        self._tools = {}
        self._actors = set(actors)   # allowlisted callers
        self._audit = []
        self._prev = "genesis"

    def _log(self, entry):
        entry = dict(entry, prev=self._prev)
        entry["hash"] = hashlib.sha256(_canon(entry)).hexdigest()
        self._prev = entry["hash"]
        self._audit.append(entry)

    def register(self, name, *, description, risk, required_args, fn):
        if risk not in RISK_CLASSES:
            raise ToolError(f"unknown risk class: {risk}")
        if not callable(fn):
            raise ToolError("a tool needs a function")
        if not (description or "").strip():
            raise ToolError("an undocumented tool is an unaccountable "
                            "tool")
        if name in self._tools:
            raise ToolError(f"duplicate tool: {name}")
        self._tools[name] = {"name": name, "description": description,
                             "risk": risk,
                             "required_args": tuple(required_args),
                             "fn": fn}
        return name

    def list_tools(self, *, actor):
        self._log({"event": "list", "actor": actor})
        return [{"name": t["name"], "description": t["description"],
                 "risk": t["risk"], "required_args": t["required_args"]}
                for t in self._tools.values()]

    def call(self, name, *, actor, args):
        if actor not in self._actors:
            self._log({"event": "denied", "tool": name, "actor": actor,
                       "reason": "actor not allowlisted"})
            return {"status": "denied",
                    "errors": ["actor not allowlisted — deny by "
                               "default"]}
        tool = self._tools.get(name)
        if tool is None:
            self._log({"event": "denied", "tool": name, "actor": actor,
                       "reason": "unknown tool"})
            return {"status": "denied",
                    "errors": [f"unknown tool: {name}"]}
        missing = [k for k in tool["required_args"] if k not in args]
        if missing:
            self._log({"event": "error", "tool": name, "actor": actor,
                       "reason": f"missing args {missing}"})
            return {"status": "error",
                    "errors": [f"missing required args: {missing}"]}
        if tool["risk"] == "write":
            self._log({"event": "pending", "tool": name, "actor": actor,
                       "args": _canon(args).decode()})
            return {"status": "pending",
                    "reason": "write tools defer to a human — machines "
                              "propose, people commit"}
        try:
            result = tool["fn"](args)
        except Exception as exc:
            self._log({"event": "error", "tool": name, "actor": actor,
                       "reason": str(exc)})
            return {"status": "error", "errors": [str(exc)]}
        self._log({"event": "served", "tool": name, "actor": actor})
        return {"status": "ok", "result": result}

    def verify_audit(self):
        prev = "genesis"
        for entry in self._audit:
            body = {k: v for k, v in entry.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            if hashlib.sha256(_canon(body)).hexdigest() != entry["hash"]:
                return False
            prev = entry["hash"]
        return True


# ---------------------------------------------------- JSON-RPC seam

METHODS = ("tools/list", "tools/call")


def handle_message(registry, line):
    """One newline-delimited JSON-RPC 2.0 message -> one response dict.
    Parse failures and unknown methods are errors with codes, never
    tracebacks."""
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error"}}
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"tools": registry.list_tools(
                    actor=params.get("actor", ""))}}
    if method == "tools/call":
        outcome = registry.call(
            params.get("name", ""), actor=params.get("actor", ""),
            args=params.get("args") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": outcome}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601,
                      "message": f"method not found: {method}"}}
