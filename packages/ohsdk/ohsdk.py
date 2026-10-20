"""OpenHealth third-party SDK (stdlib Python) — the integration surface for
payers, insurers, labs, pharmacies and health-tech vendors (ADR-0061).

Auth: SMART Backend Services (OAuth2 client_credentials, Keycloak realm
`openhealth`, client profile per ADR-0054). Scopes are least-privilege per
stakeholder class:
  payer/insurer : system/Claim.read system/Claim.write system/Coverage.read
  laboratory    : system/ServiceRequest.read system/DiagnosticReport.write
  pharmacy      : system/MedicationRequest.read system/MedicationDispense.write
  analytics     : bulk-export only (aggregate cohorts, consent-scoped)

Fail-soft doctrine applies to the SDK too: offline queues are the EDGE's job;
the SDK surfaces honest errors, never retries silently into duplicates —
every mutating call requires a caller-supplied idempotency key.
"""
from __future__ import annotations
import json, time, urllib.request, urllib.error, urllib.parse, uuid

class SdkError(Exception):
    pass
class AuthError(SdkError):
    pass
class ConflictError(SdkError):
    """Duplicate idempotency key — the original request already landed."""

class Client:
    def __init__(self, base_url: str, token_url: str, client_id: str, client_secret: str,
                 scope: str, timeout: int = 10, opener=None):
        self.base, self.token_url = base_url.rstrip("/"), token_url
        self.client_id, self.client_secret, self.scope = client_id, client_secret, scope
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._token = None
        self._token_exp = 0.0

    # ---- auth ----
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": self.scope}).encode()
        try:
            resp = self._opener(urllib.request.Request(self.token_url, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=self.timeout)
            out = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise AuthError(f"token request failed: {e.code}")
        self._token = out["access_token"]
        self._token_exp = time.time() + out.get("expires_in", 300)
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None,
              idem_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._get_token()}",
                   "Content-Type": "application/fhir+json"}
        if method in ("POST", "PUT"):
            headers["X-Idempotency-Key"] = idem_key or str(uuid.uuid4())
        req = urllib.request.Request(self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method=method)
        try:
            resp = self._opener(req, timeout=self.timeout)
            return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                raise ConflictError("idempotency key replay — original request landed")
            raise SdkError(f"{method} {path} -> {e.code}: {e.read()[:200]}")

    # ---- payer / insurer surface ----
    def eligibility_check(self, member_id: str, service_type: str = "general") -> dict:
        return self._call("POST", "/api/claims/eligibility/check",
                          {"member_id": member_id, "service_type": service_type})

    def preauth(self, member_id: str, tariff_code: str, est_amount: float,
                idem_key: str) -> dict:
        return self._call("POST", "/api/claims/preauth",
                          {"member_id": member_id, "tariff_code": tariff_code,
                           "est_amount": est_amount}, idem_key)

    def submit_claim(self, claim: dict, idem_key: str) -> dict:
        return self._call("POST", "/api/claims/submit", claim, idem_key)

    def claim_status(self, claim_id: str) -> dict:
        return self._call("GET", f"/api/claims/status/{claim_id}")

    # ---- laboratory surface ----
    def lab_orders(self, since: str = "") -> dict:
        q = f"?_since={urllib.parse.quote(since)}" if since else ""
        return self._call("GET", f"/api/fhir/ServiceRequest{q}")

    def post_diagnostic_report(self, report: dict, idem_key: str) -> dict:
        return self._call("POST", "/api/fhir/DiagnosticReport", report, idem_key)

    # ---- pharmacy surface ----
    def get_prescription(self, order_id: str) -> dict:
        return self._call("GET", f"/api/fhir/MedicationRequest/{order_id}")

    def post_dispense(self, dispense: dict, idem_key: str) -> dict:
        return self._call("POST", "/api/fhir/MedicationDispense", dispense, idem_key)

    # ---- analytics surface (bulk export) ----
    def bulk_export(self, types: list[str]) -> dict:
        return self._call("POST", "/api/fhir/$export",
                          {"_type": ",".join(types)})

    def bulk_status(self, job_url: str) -> dict:
        return self._call("GET", job_url.replace(self.base, ""))
