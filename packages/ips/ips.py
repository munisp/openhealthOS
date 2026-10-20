#!/usr/bin/env python3
"""International Patient Summary (IPS) — FHIR R4 document builder
(ADR-0013, innovation).

Builds a FHIR R4 `document` Bundle following the IPS profile's shape:
a Composition with the required sections (problems, allergies, medications,
immunizations, results) plus the patient — the summary a clinician anywhere
can read at a glance. Offline-verifiable signing lives in tools/ipsqr; this
module produces the clinical payload that gets signed.
"""
from __future__ import annotations

import uuid

REQUIRED_SECTIONS = ("problems", "allergies", "medications",
                     "immunizations", "results")


def _rid(prefix: str) -> str:
    return f"urn:uuid:{prefix}-{uuid.uuid4().hex[:12]}"


def build_ips(patient: dict, problems: list[dict] | None = None,
              allergies: list[dict] | None = None,
              medications: list[dict] | None = None,
              immunizations: list[dict] | None = None,
              observations: list[dict] | None = None,
              facility: str = "") -> dict:
    """patient: {id, name, birth_date, gender}. Clinical lists are plain
    dicts {code, display, ...}; they become minimal FHIR resources."""
    patient_url = _rid("patient")
    entries = [{
        "fullUrl": patient_url,
        "resource": {"resourceType": "Patient", "id": patient_url.split(":")[-1],
                     "name": [{"text": patient.get("name", "")}],
                     "birthDate": patient.get("birth_date", ""),
                     "gender": patient.get("gender", "unknown")},
    }]

    section_map = {
        "problems": ("Condition", problems or []),
        "allergies": ("AllergyIntolerance", allergies or []),
        "medications": ("MedicationStatement", medications or []),
        "immunizations": ("Immunization", immunizations or []),
        "results": ("Observation", observations or []),
    }
    sections = []
    for key, (rtype, items) in section_map.items():
        refs = []
        for item in items:
            url = _rid(key)
            resource = {"resourceType": rtype, "id": url.split(":")[-1],
                        "code": {"text": item.get("display", item.get("code", ""))},
                        "subject": {"reference": patient_url}}
            if rtype == "Observation":
                resource["valueString"] = item.get("value", "")
            entries.append({"fullUrl": url, "resource": resource})
            refs.append({"reference": url})
        sections.append({"title": key, "code": {"text": key}, "entry": refs})

    comp_url = _rid("composition")
    entries.insert(0, {
        "fullUrl": comp_url,
        "resource": {"resourceType": "Composition", "id": comp_url.split(":")[-1],
                     "status": "final", "type": {"text": "International Patient Summary"},
                     "title": f"IPS — {patient.get('name', 'patient')}",
                     "subject": {"reference": patient_url},
                     "author": [{"display": facility or "OpenHealth OS"}],
                     "section": sections},
    })
    return {"resourceType": "Bundle", "type": "document", "entry": entries}


def validate_ips(bundle: dict) -> list[str]:
    """Structural validation; returns [] when the IPS is well-formed."""
    errors: list[str] = []
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "document":
        return ["not a FHIR document Bundle"]
    entries = bundle.get("entry", [])
    full_urls = {e.get("fullUrl") for e in entries}
    comps = [e for e in entries if e.get("resource", {}).get("resourceType") == "Composition"]
    if len(comps) != 1:
        errors.append("exactly one Composition required")
    if not any(e.get("resource", {}).get("resourceType") == "Patient" for e in entries):
        errors.append("Patient entry required")
    if comps:
        comp = comps[0]["resource"]
        titles = {s.get("title") for s in comp.get("section", [])}
        for req in REQUIRED_SECTIONS:
            if req not in titles:
                errors.append(f"missing IPS section: {req}")
        for ref in [comp.get("subject", {}).get("reference", "")] + [
                r.get("reference", "") for s in comp.get("section", [])
                for r in s.get("entry", [])]:
            if ref and ref not in full_urls:
                errors.append(f"dangling reference: {ref}")
    return errors


def ips_summary(bundle: dict) -> dict:
    """Counts for the PWA summary card / QR preview screen."""
    counts: dict[str, int] = {}
    for e in bundle.get("entry", []):
        rtype = e.get("resource", {}).get("resourceType", "?")
        counts[rtype] = counts.get(rtype, 0) + 1
    return counts


def main(argv=None):
    """CLI: build an IPS from a JSON doc on stdin, or validate one.

      echo '{"patient": {...}, "immunizations": [...]}' | python3 ips.py build
      cat ips.json | python3 ips.py validate
    """
    import argparse
    import json as _json
    import sys as _sys

    ap = argparse.ArgumentParser(prog="ips")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("validate")
    args = ap.parse_args(argv)
    doc = _json.load(_sys.stdin)
    if args.cmd == "build":
        bundle = build_ips(
            doc.get("patient", {}),
            problems=doc.get("problems"), allergies=doc.get("allergies"),
            medications=doc.get("medications"),
            immunizations=doc.get("immunizations"),
            observations=doc.get("observations"),
            facility=doc.get("facility", ""))
        _json.dump(bundle, _sys.stdout)
        print()
        return 0
    errors = validate_ips(doc)
    _json.dump({"valid": not errors, "errors": errors}, _sys.stdout, indent=2)
    print()
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
