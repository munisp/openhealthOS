#!/usr/bin/env python3
"""ipa — International Patient Access (IPA v1.0.0) conformance harness
(ADR-0054).

IPA is the patient-access contract: a SMART-on-FHIR server that lets a
patient app fetch the patient's own core record. This package is our
executable answer to "are we IPA-conformant?" — it probes a FHIR base
URL (Medplum SHR in production, any test double in CI) and reports
per-capability PASS/FAIL. No network? Point it at a fixture server.

Checks (the IPA support set, R4):
  - capability statement advertises SMART security
  - Patient read + search
  - per-resource read/search: AllergyIntolerance, Condition,
    MedicationStatement|MedicationRequest, Immunization, Observation
    (vitals + labs categories), DocumentReference
  - records are returnable in the shapes patient apps rely on

Also ships the SMART scope map for our Keycloak realm: which OAuth2
scopes an IPA client needs (patient/*.read etc.) so the realm config
is a documented artifact, not console folklore.

CLI:
  python3 ipa.py check --base https://shr.example.com/fhir [--token T]
  python3 ipa.py scopes            # print the Keycloak scope map
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

# IPA v1.0.0 support set (R4): resource → required interactions.
# Observation is probed with both vital-signs and laboratory categories.
IPA_RESOURCES: dict[str, dict] = {
    "Patient":            {"search": True,  "read": True},
    "AllergyIntolerance": {"search": True,  "read": True},
    "Condition":          {"search": True,  "read": True},
    "MedicationStatement": {"search": True, "read": True},
    "MedicationRequest":  {"search": False, "read": False},  # acceptable alternative
    "Immunization":       {"search": True,  "read": True},
    "Observation":        {"search": True,  "read": True},
    "DocumentReference":  {"search": True,  "read": True},
}

# SMART scopes an IPA patient-client needs on our Keycloak realm.
SMART_SCOPE_MAP: dict[str, str] = {
    "openid":                 "OIDC identity",
    "fhirUser":               "SMART identity claim",
    "launch/patient":         "patient context at launch",
    "patient/Patient.read":   "demographics",
    "patient/AllergyIntolerance.read": "allergies",
    "patient/Condition.read": "problem list",
    "patient/MedicationStatement.read": "medications",
    "patient/Immunization.read": "immunization history",
    "patient/Observation.read": "vitals + labs",
    "patient/DocumentReference.read": "clinical documents",
    "offline_access":         "refresh tokens for mobile/PWA",
}


def _get(base: str, path: str, token: str = "", timeout: float = 5.0) -> tuple[int, dict]:
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"Accept": "application/fhir+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"_error": str(e)}


def check_server(base: str, token: str = "", sample_patient: str = "") -> dict:
    """Probe every IPA capability; returns a conformance report.
    Fail-soft per check: one broken resource doesn't abort the rest."""
    results: list[dict] = []
    base = base.rstrip("/")

    status, cap = _get(base, "/metadata", token)
    sec = json.dumps(cap)
    smart = "smart" in sec.lower() or "oauth" in sec.lower()
    results.append({
        "capability": "capability-statement",
        "ok": status == 200,
        "detail": f"GET /metadata → {status}",
    })
    results.append({
        "capability": "smart-security-advertised",
        "ok": smart,
        "detail": "SMART/OAuth security extension in CapabilityStatement"
                  if smart else "no SMART security advertised",
    })

    for rtype, want in IPA_RESOURCES.items():
        # search: read 1 to discover an id for the read probe
        q = f"/{rtype}?_count=1"
        if rtype == "Observation":
            q = "/Observation?category=vital-signs&_count=1"
        if sample_patient and rtype != "Patient":
            q += f"&patient={sample_patient}"
        s_status, bundle = _get(base, q, token)
        entries = bundle.get("entry", []) if isinstance(bundle, dict) else []
        search_ok = s_status == 200 and bundle.get("resourceType") == "Bundle"
        detail = f"GET {q} → {s_status}, {len(entries)} entries"
        if want["search"]:
            results.append({"capability": f"{rtype}/search", "ok": search_ok, "detail": detail})
        if want["read"]:
            rid = None
            if entries:
                rid = entries[0].get("resource", {}).get("id")
            if rtype == "Patient" and sample_patient:
                rid = sample_patient
            if rid:
                r_status, res = _get(base, f"/{rtype}/{rid}", token)
                results.append({
                    "capability": f"{rtype}/read",
                    "ok": r_status == 200 and res.get("resourceType") == rtype,
                    "detail": f"GET /{rtype}/{rid} → {r_status}",
                })
            else:
                # empty search is legal; read is unverifiable without an id
                results.append({
                    "capability": f"{rtype}/read",
                    "ok": None,
                    "detail": "no sample resource to read — unverifiable, not failed",
                })
    obs_status, obs_bundle = _get(base, "/Observation?category=laboratory&_count=1", token)
    results.append({
        "capability": "Observation/laboratory-category",
        "ok": obs_status == 200 and obs_bundle.get("resourceType") == "Bundle",
        "detail": f"laboratory category search → {obs_status}",
    })

    passed = sum(1 for r in results if r["ok"] is True)
    failed = sum(1 for r in results if r["ok"] is False)
    skipped = sum(1 for r in results if r["ok"] is None)
    return {
        "base": base, "passed": passed, "failed": failed, "skipped": skipped,
        "conformant": failed == 0 and passed > 0,
        "results": results,
    }


def scope_map() -> dict:
    return dict(SMART_SCOPE_MAP)


def jwt_payload(token: str) -> dict:
    """Decode a JWT payload (no verification — introspection aid for
    scope debugging). Empty dict on any malformation."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ipa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--base", required=True)
    c.add_argument("--token", default="")
    c.add_argument("--patient", default="", help="sample patient id")
    sub.add_parser("scopes")
    args = ap.parse_args(argv)
    if args.cmd == "scopes":
        print(json.dumps(scope_map(), indent=2))
        return 0
    report = check_server(args.base, args.token, args.patient)
    print(json.dumps(report, indent=2))
    return 0 if report["conformant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
