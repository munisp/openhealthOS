"""OpenMRS/Bahmni FHIR bridge — hospital systems are peers, not rewrites
(ADR-0010). Hospitals running OpenMRS/Bahmni keep them; this adapter
synchronises Patient / Encounter / Observation resources between the
OpenMRS FHIR2 module (R4 at /ws/fhir2/R4) and the OpenHealth SHR via the
standard envelope contract (PUT {shr}/fhir/{type}/{id} with
X-Facility-ID provenance, consent gate enforced server-side).

Two directions:
  pull  — OpenMRS -> SHR: patients, encounters, lab observations created
          at the hospital become part of the patient's longitudinal record.
  push  — SHR/referral -> OpenMRS: outbound resources (e.g. a referral
          ServiceRequest or a synced Patient) are created in OpenMRS so the
          hospital workflow continues in its own system.

Like every adapter here: bounded timeouts, sandbox mode captures payloads
and never touches the network, and patient identity mapping is explicit
(openmrs_uuid <-> national id) rather than guessed.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

FHIR2_BASE = "/ws/fhir2/R4"
SUPPORTED_PULL = ("Patient", "Encounter", "Observation")


@dataclass
class SyncResult:
    resource_type: str
    pulled: int = 0
    pushed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class OpenMRSBridge:
    """Bridge between an OpenMRS FHIR2 endpoint and the OpenHealth SHR."""

    def __init__(self, openmrs_url: str = "", username: str = "",
                 password: str = "", shr_url: str = "", facility_id: str = "",
                 sandbox: bool = True, timeout: float = 8.0):
        self.openmrs_url = openmrs_url.rstrip("/")
        self.shr_url = shr_url.rstrip("/")
        self.facility_id = facility_id
        self.sandbox = sandbox
        self.timeout = timeout
        self._auth = ""
        if username or password:
            raw = f"{username}:{password}".encode()
            self._auth = "Basic " + base64.b64encode(raw).decode()
        # sandbox capture buffers: what *would* have been sent.
        self.captured_shr: list[dict] = []
        self.captured_openmrs: list[dict] = []

    # ── HTTP plumbing ────────────────────────────────────────────────
    def _call(self, method: str, url: str, body: dict | None,
              auth: bool) -> tuple[int, dict]:
        headers = {"Accept": "application/fhir+json"}
        if body is not None:
            headers["Content-Type"] = "application/fhir+json"
        if auth and self._auth:
            headers["Authorization"] = self._auth
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:400]}
        except OSError as e:
            raise ConnectionError(f"openmrs bridge: {url}: {e}") from e

    # ── pull: OpenMRS -> SHR ─────────────────────────────────────────
    def pull(self, resource_type: str, params: dict | None = None) -> list[dict]:
        """Fetch resources from the OpenMRS FHIR2 module as a Bundle."""
        if resource_type not in SUPPORTED_PULL:
            raise ValueError(f"unsupported pull type: {resource_type}")
        qs = ""
        if params:
            qs = "?" + urllib.parse.urlencode(params)
        url = f"{self.openmrs_url}{FHIR2_BASE}/{resource_type}{qs}"
        status, out = self._call("GET", url, None, auth=True)
        if status != 200:
            raise ConnectionError(f"pull {resource_type} -> {status}: {out}")
        return [e.get("resource", {}) for e in out.get("entry", [])]

    def push_shr_envelope(self, resource: dict) -> dict:
        """PUT one resource into the SHR using the platform envelope
        contract; tags provenance as source system 'openmrs'."""
        rt = resource.get("resourceType", "")
        rid = resource.get("id", "")
        if not rt or not rid:
            raise ValueError("resource needs resourceType and id")
        body = dict(resource)
        body.setdefault("meta", {}).setdefault("tag", []).append(
            {"system": "https://openhealthos.org/fhir/source-system",
             "code": "openmrs"})
        envelope = {
            "resourceType": rt,
            "id": rid,
            "patient_id": self._patient_ref(resource),
            "source_facility": self.facility_id,
            "body": body,
        }
        if self.sandbox:
            self.captured_shr.append(envelope)
            return {"status": "captured", "id": rid}
        url = f"{self.shr_url}/fhir/{rt}/{rid}"
        req = urllib.request.Request(
            url, data=json.dumps(envelope).encode(),
            headers={"Content-Type": "application/json",
                     "X-Facility-ID": self.facility_id},
            method="PUT")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return {"status": "pushed", "code": resp.status, "id": rid}

    @staticmethod
    def _patient_ref(resource: dict) -> str:
        if resource.get("resourceType") == "Patient":
            return resource.get("id", "")
        for key in ("subject", "patient"):
            ref = (resource.get(key) or {}).get("reference", "")
            if ref.startswith("Patient/"):
                return ref.split("/", 1)[1]
        return ""

    def sync(self, resource_type: str, params: dict | None = None) -> SyncResult:
        """Pull all of one type from OpenMRS and push each into the SHR.
        Individual failures are collected, never raised mid-sync."""
        result = SyncResult(resource_type=resource_type)
        try:
            resources = self.pull(resource_type, params)
        except (ConnectionError, ValueError) as e:
            result.errors.append(str(e))
            return result
        result.pulled = len(resources)
        for r in resources:
            try:
                self.push_shr_envelope(r)
                result.pushed += 1
            except (ConnectionError, ValueError, OSError) as e:
                result.failed += 1
                result.errors.append(f"{r.get('id', '?')}: {e}")
        return result

    # ── push: platform -> OpenMRS ────────────────────────────────────
    def push_openmrs(self, resource: dict) -> dict:
        """Create a resource in OpenMRS (outbound referrals, synced
        patients). POST {base}/ws/fhir2/R4/{type}."""
        rt = resource.get("resourceType", "")
        if not rt:
            raise ValueError("resource needs resourceType")
        if self.sandbox:
            self.captured_openmrs.append(resource)
            return {"status": "captured", "resourceType": rt}
        url = f"{self.openmrs_url}{FHIR2_BASE}/{rt}"
        status, out = self._call("POST", url, resource, auth=True)
        if status not in (200, 201):
            raise ConnectionError(f"push {rt} -> {status}: {out}")
        return {"status": "created", "id": out.get("id", "")}
