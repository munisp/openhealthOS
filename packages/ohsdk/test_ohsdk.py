import json, urllib.error
import pytest
from ohsdk import Client, AuthError, ConflictError, SdkError

class FakeHTTP:
    """Scriptable opener: queues (status, body) responses; records requests."""
    def __init__(self):
        self.responses = []
        self.requests = []
    def push(self, status, body):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.responses.append((status, body))
    def __call__(self, req, timeout=10):
        self.requests.append(req)
        status, body = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "", {}, None)
        class R:
            def read(self): return body
        return R()

def token_ok(fh, token="tok-1"):
    fh.push(200, {"access_token": token, "expires_in": 300})

def make(fh):
    return Client("https://gw.example", "https://kc.example/token", "cid", "sec",
                  "system/Claim.read system/Claim.write", opener=fh)

def test_token_cached():
    fh = FakeHTTP(); token_ok(fh)
    fh.push(200, {"eligible": True})
    c = make(fh)
    c.eligibility_check("NG-1"); 
    fh.push(200, {"status": "APPROVED"})
    c.claim_status("C1")
    # only ONE token request for two calls
    assert sum(1 for r in fh.requests if "token" in r.full_url) == 1

def test_bearer_header_and_scope():
    fh = FakeHTTP(); token_ok(fh); fh.push(200, {})
    make(fh).eligibility_check("NG-1")
    auth = fh.requests[1].headers.get("Authorization")
    assert auth == "Bearer tok-1"

def test_idempotency_key_on_mutations():
    fh = FakeHTTP(); token_ok(fh); fh.push(200, {"id": "PA-1"})
    make(fh).preauth("NG-1", "CONS-GEN", 1500, idem_key="K-42")
    assert fh.requests[1].headers.get("X-idempotency-key") == "K-42"

def test_conflict_surfaces_not_retried():
    fh = FakeHTTP(); token_ok(fh); fh.push(409, b"")
    with pytest.raises(ConflictError):
        make(fh).submit_claim({"id": "C1"}, idem_key="K-1")

def test_auth_error_honest():
    fh = FakeHTTP(); fh.push(401, b"")
    with pytest.raises(AuthError):
        make(fh).eligibility_check("NG-1")

def test_http_error_honest():
    fh = FakeHTTP(); token_ok(fh); fh.push(500, b"boom")
    with pytest.raises(SdkError):
        make(fh).claim_status("C1")

def test_lab_and_pharmacy_surfaces():
    fh = FakeHTTP(); token_ok(fh)
    fh.push(200, {"entry": []}); fh.push(200, {"id": "DR-1"})
    c = make(fh)
    c.lab_orders(since="2026-10-01")
    assert "ServiceRequest?_since=2026-10-01" in fh.requests[1].full_url
    c.post_diagnostic_report({"resourceType": "DiagnosticReport"}, idem_key="K-9")
    assert fh.requests[2].full_url.endswith("/api/fhir/DiagnosticReport")

def test_bulk_export():
    fh = FakeHTTP(); token_ok(fh)
    fh.push(200, {"job": "j1"}); fh.push(200, {"status": "complete"})
    c = make(fh)
    out = c.bulk_export(["Patient", "Observation"])
    assert out["job"] == "j1"
