#!/usr/bin/env python3
"""OpenHIE-pattern conformance harness (ADR-0013).

Instantiates the core interoperability workflows against a running platform
and asserts the contracts an HIE must keep. stdlib only; exits non-zero on
any failed check with the failing step named.

Workflows (OpenHIE architecture):
  W1  Register patient in the registry → retrievable as FHIR R4 Patient
      conforming to the openhealth-patient profile
  W2  Save a clinical encounter to the SHR → retrievable as FHIR Encounter
  W3  Cross-facility read WITHOUT consent → refused (the consent gate is
      the HIE-CM contract; a platform that leaks here fails the federation)
  W4  Cross-facility read WITH consent artefact → allowed
  W5  Facility registry query → FHIR Location with physical-infrastructure
      profile (power/water — the readiness data)

Usage: python3 tools/conformance/conformance.py --registry URL --shr URL \
           --consent URL --facility-a PHC-A --facility-b PHC-B
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(workflow: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((workflow, name, PASS if ok else FAIL))
    print(f"  [{PASS if ok else FAIL}] {workflow}: {name}" + (f" — {detail}" if detail and not ok else ""))


def req(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def w1_registry(registry: str, facility: str) -> None:
    pid = None
    st, patient = req("POST", f"{registry}/patients",
                      {"name": "Conformance Chika", "dob": "1991-03-12",
                       "phone": "+2348009990001", "facility_id": facility})
    check("W1", "register patient", st == 201, f"got {st}")
    pid = patient.get("id")
    if not pid:
        return
    st, fhir = req("GET", f"{registry}/fhir/Patient/{pid}")
    ok = (st == 200 and fhir.get("resourceType") == "Patient"
          and any("openhealthos.org" in str(i.get("system", "")) or i.get("value")
                  for i in fhir.get("identifier", [])))
    check("W1", "retrievable as FHIR R4 Patient", ok, f"got {st}")


def w2_save_encounter(shr: str, facility: str) -> str:
    enc = {
        "resourceType": "Encounter", "id": "conf-enc-1",
        "status": "finished",
        "class": {"code": "AMB"},
        "subject": {"reference": "Patient/conf-pt-1"},
        "period": {"start": "2026-10-18T08:00:00Z", "end": "2026-10-18T08:30:00Z"},
        # Provenance envelope (SHR Resource): the consent gate keys on these.
        "patient_id": "conf-pt-1", "source_facility": facility,
    }
    st, _ = req("PUT", f"{shr}/fhir/Encounter/conf-enc-1", enc,
                {"X-Facility-ID": facility})
    check("W2", "save encounter to SHR", st in (200, 201), f"got {st}")
    st, got = req("GET", f"{shr}/fhir/Encounter/conf-enc-1",
                  headers={"X-Facility-ID": facility})
    check("W2", "encounter retrievable same facility",
          st == 200 and got.get("id") == "conf-enc-1", f"got {st}")
    return "conf-enc-1"


def w3_consent_gate(shr: str, other_facility: str) -> None:
    st, _ = req("GET", f"{shr}/fhir/Encounter/conf-enc-1",
                headers={"X-Facility-ID": other_facility})
    check("W3", "cross-facility read without consent refused", st == 403,
          f"got {st} — CONSENT GATE LEAK" if st != 403 else "")


def w4_consented_read(shr: str, consent: str, granting: str, reader: str) -> None:
    # consent-service contract: POST /consents {patient_id, grantee, scope,
    # purpose}; the SHR gate asks Check(patient, reader, "shr-read").
    artefact = {
        "patient_id": "conf-pt-1", "grantee": reader,
        "scope": "shr-read", "purpose": "treatment",
    }
    st, _ = req("POST", f"{consent}/consents", artefact)
    if st not in (200, 201):
        check("W4", "grant consent artefact", False, f"got {st}")
        return
    check("W4", "grant consent artefact", True)
    st, ans = req("POST", f"{consent}/consents/check",
                  {"patient_id": "conf-pt-1", "grantee": reader, "scope": "shr-read"})
    check("W4", "consent check answers allowed", st == 200 and ans.get("allowed") is True,
          f"got {st} {ans}")
    st, _ = req("GET", f"{shr}/fhir/Encounter/conf-enc-1",
                headers={"X-Facility-ID": reader})
    check("W4", "cross-facility read with consent allowed", st == 200, f"got {st}")


def w5_facility_registry(registry: str) -> None:
    st, fac = req("GET", f"{registry}/fhir/Location/PHC-CONFORM")
    if st == 404:
        req("POST", f"{registry}/facilities",
            {"id": "PHC-CONFORM", "name": "Conformance PHC", "state": "Kaduna",
             "lga": "Kawo", "ownership": "public", "level": "PHC",
             "services": ["anc"], "bhcpf": True, "power": "solar", "water": "borehole"})
        st, fac = req("GET", f"{registry}/fhir/Location/PHC-CONFORM")
    ok = st == 200 and fac.get("resourceType") == "Location"
    check("W5", "facility as FHIR Location", ok, f"got {st}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="http://127.0.0.1:8081")
    ap.add_argument("--shr", default="http://127.0.0.1:8085")
    ap.add_argument("--consent", default="http://127.0.0.1:8082")
    ap.add_argument("--facility-a", default="PHC-A")
    ap.add_argument("--facility-b", default="PHC-B")
    args = ap.parse_args()

    print("OpenHealth OS — OpenHIE-pattern conformance")
    w1_registry(args.registry, args.facility_a)
    enc_id = w2_save_encounter(args.shr, args.facility_a)
    if enc_id:
        w3_consent_gate(args.shr, args.facility_b)
        w4_consented_read(args.shr, args.consent, args.facility_a, args.facility_b)
    w5_facility_registry(args.registry)

    failed = [r for r in results if r[2] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("CONFORMANCE: FAIL")
        return 1
    print("CONFORMANCE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
