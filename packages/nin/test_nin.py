import json, subprocess, sys, os, pytest
from nin import NinAdapter, NinError, TTL_SECONDS

NOW = 1792497600
def mk(verifier=None):
    return NinAdapter(verifier=verifier, now=lambda: NOW)

def test_malformed_nin():
    with pytest.raises(NinError):
        mk().verify("123")

def test_no_channel_fail_soft():
    r = mk().verify("12345678901")
    assert r["state"] == "unverified" and not r["verified"]

def test_verified_then_cached():
    calls = []
    def v(n): calls.append(n); return {"verified": True}
    ad = mk(v)
    r1 = ad.verify("12345678901")
    r2 = ad.verify("12345678901")
    assert r1["state"] == "verified" and r2["state"] == "verified-cached"
    assert len(calls) == 1

def test_unreachable_uses_stale_cache():
    clock = {"t": NOW}
    state = {"fail": False}
    def v(n):
        if state["fail"]: raise ConnectionError("timeout")
        return {"verified": True}
    ad = NinAdapter(verifier=v, now=lambda: clock["t"])
    ad.verify("12345678901")
    state["fail"] = True
    clock["t"] = NOW + TTL_SECONDS + 1  # cache expired -> tries NIMC -> falls back stale
    r = ad.verify("12345678901")
    assert r["state"] == "verified-stale" and r["verified"]

def test_unreachable_no_cache_unverified():
    def v(n): raise ConnectionError("down")
    r = mk(v).verify("12345678901")
    assert r["state"] == "unverified" and not r["verified"]

def test_rejected():
    r = mk(lambda n: {"verified": False}).verify("12345678901")
    assert r["state"] == "rejected"

def test_privacy_no_raw_nin():
    r = mk(lambda n: {"verified": True}).verify("12345678901")
    assert "12345678901" not in json.dumps(r)

def test_cli():
    p = subprocess.run([sys.executable, "nin.py"], input=json.dumps({"nin": "12345678901"}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["state"] == "unverified"
