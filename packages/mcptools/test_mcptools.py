"""test_mcptools.py — governed agent tool surface."""

import json

import pytest

from mcptools import ToolError, ToolRegistry, handle_message


def make_registry():
    reg = ToolRegistry(actors={"chart-reader", "admin"})
    reg.register("get-vitals", description="read latest vitals",
                 risk="read", required_args=("patient",),
                 fn=lambda a: {"patient": a["patient"], "hr": 88})
    reg.register("write-note", description="write a clinical note",
                 risk="write", required_args=("patient", "text"),
                 fn=lambda a: {"written": True})
    return reg


def test_deny_by_default():
    reg = make_registry()
    out = reg.call("get-vitals", actor="rogue-agent",
                   args={"patient": "p1"})
    assert out["status"] == "denied"
    assert "deny by default" in out["errors"][0]
    out2 = reg.call("no-such-tool", actor="admin", args={})
    assert out2["status"] == "denied"
    assert reg.verify_audit()


def test_read_serves():
    reg = make_registry()
    out = reg.call("get-vitals", actor="chart-reader",
                   args={"patient": "p1"})
    assert out == {"status": "ok", "result": {"patient": "p1",
                                              "hr": 88}}


def test_write_pends():
    reg = make_registry()
    out = reg.call("write-note", actor="admin",
                   args={"patient": "p1", "text": "stable"})
    assert out["status"] == "pending"
    assert "machines propose" in out["reason"]
    assert reg._audit[-1]["event"] == "pending"


def test_missing_args_listed():
    reg = make_registry()
    out = reg.call("get-vitals", actor="admin", args={})
    assert out["status"] == "error"
    assert "patient" in out["errors"][0]


def test_tool_contract():
    reg = ToolRegistry()
    with pytest.raises(ToolError, match="unaccountable"):
        reg.register("x", description="", risk="read",
                     required_args=(), fn=lambda a: None)
    with pytest.raises(ToolError, match="unknown risk"):
        reg.register("y", description="d", risk="administer-drug",
                     required_args=(), fn=lambda a: None)


def test_jsonrpc_roundtrip():
    reg = make_registry()
    listed = handle_message(reg, json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
         "params": {"actor": "admin"}}))
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"get-vitals", "write-note"}
    called = handle_message(reg, json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get-vitals", "actor": "chart-reader",
                    "args": {"patient": "p9"}}}))
    assert called["result"]["status"] == "ok"
    assert called["id"] == 2


def test_jsonrpc_errors_are_structured():
    reg = make_registry()
    bad = handle_message(reg, "not json{")
    assert bad["error"]["code"] == -32700
    unknown = handle_message(reg, json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "system/exec"}))
    assert unknown["error"]["code"] == -32601


def test_audit_chain_tamper_detected():
    reg = make_registry()
    reg.call("get-vitals", actor="admin", args={"patient": "p1"})
    reg.call("get-vitals", actor="rogue", args={})
    assert reg.verify_audit()
    reg._audit[0]["event"] = "served-anyway"
    assert not reg.verify_audit()
