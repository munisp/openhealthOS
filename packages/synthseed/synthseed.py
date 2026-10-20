"""OpenHealth OS — Synthetic Hospital seed pipeline (ADR-0045).

Converts the Synthetic Hospital benchmark's released corpus
(`patient_profiles.jsonl`: 1,268 fully synthetic longitudinal patients,
5,602 clinical notes — zero PHI, openly redistributable) into FHIR R4
transaction bundles that load straight into shr-service.

Why this exists:
  - Demos, CI fixtures, and pilot training need realistic LONGITUDINAL
    charts. Hand-written fixtures are thin; real charts are unlawful
    (NDPA 2023). This corpus is physician-validated as
    indistinguishable-from-real (53% ≈ chance) and carries no PHI.
  - Every record is ontology-grounded (ICD-10-CM / SNOMED CT / LOINC),
    so seeded data exercises our coding paths (claims tariffs,
    surveillance syndromes) with real code systems.
  - The benchmark's four scored tasks give us a standing eval harness
    for any clinical-LLM feature we add (summarisation, evidence
    retrieval) — run before shipping, not after an incident.

Input shape (patient_profiles.jsonl, one patient per line):
    {"patient_id": ..., "demographics": {...}, "encounters"/"notes":
     [{"date"/"encounter_date": ..., "note_type"/"type": ...,
       "text"/"note": ..., "diagnoses": [{"icd10"/"code": ...}]}]}
The converter is field-tolerant: the corpus has evolved across releases,
so we probe several aliases rather than pinning one schema version.

CLI:
    python3 synthseed.py convert PROFILES.jsonl OUT_DIR \
        [--facility PHC-DEMO] [--limit N]
    python3 synthseed.py load OUT_DIR --shr http://localhost:8086 \
        [--token TOKEN]

Stdlib only (ADR-0005).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request

# Deterministic namespace: patient identity must be stable across
# re-runs so seeding is idempotent (re-load = same IDs, upsert not dup).
NS = "openhealth-synthseed-v1"


def stable_id(kind: str, *parts) -> str:
    h = hashlib.sha256("\x00".join([kind, *(str(p) for p in parts)]).encode())
    return f"{kind}-{h.hexdigest()[:16]}"


def pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def patient_resource(profile: dict) -> dict:
    pid = str(pick(profile, "patient_id", "id"))
    demo = profile.get("demographics", {}) or {}
    res = {
        "resourceType": "Patient",
        "id": stable_id("patient", pid),
        "identifier": [{
            "system": "https://openhealthos.org/synthetic-hospital",
            "value": pid,
        }],
        "active": True,
        "meta": {"tag": [{
            "system": "https://openhealthos.org/provenance",
            "code": "synthetic-hospital-v1.3",
            "display": "Fully synthetic — no PHI (arXiv:2609.30027)",
        }]},
    }
    if demo.get("gender"):
        res["gender"] = str(demo["gender"]).lower()
    if demo.get("age") is not None:
        # Age → approximate birth year relative to first encounter later;
        # here we carry it as an extension to stay deterministic.
        res.setdefault("extension", []).append({
            "url": "https://openhealthos.org/fhir/StructureDefinition/age-at-seed",
            "valueInteger": int(demo["age"]),
        })
    return res


def encounter_resources(profile: dict, facility: str) -> list[dict]:
    pid = str(pick(profile, "patient_id", "id"))
    pat_ref = {"reference": f"Patient/{stable_id('patient', pid)}"}
    out = []
    notes = pick(profile, "encounters", "notes", default=[]) or []
    for i, note in enumerate(notes):
        date = str(pick(note, "date", "encounter_date", "admission_date",
                        default="2024-01-01"))[:10]
        ntype = str(pick(note, "note_type", "type", default="progress"))
        enc_id = stable_id("enc", pid, i, date)
        out.append({
            "resourceType": "Encounter",
            "id": enc_id,
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                      "code": "AMB" if "inpatient" not in ntype.lower() else "IMP"},
            "subject": pat_ref,
            "period": {"start": date},
            "serviceProvider": {"reference": f"Organization/{facility}"},
        })
        text = pick(note, "text", "note", "content")
        if text:
            out.append({
                "resourceType": "DocumentReference",
                "id": stable_id("doc", pid, i, date),
                "status": "current",
                "type": {"text": ntype},
                "subject": pat_ref,
                "context": {"encounter": [{"reference": f"Encounter/{enc_id}"}],
                            "period": {"start": date}},
                "content": [{"attachment": {"contentType": "text/plain",
                                          "data": None, "title": text[:80],
                                          "url": f"inline:{enc_id}"}}],
                # The full note rides in a contained narrative so the SHR
                # DocumentReference stays queryable without blob storage.
                "description": text,
            })
        for dx in pick(note, "diagnoses", "conditions", default=[]) or []:
            code = str(pick(dx, "icd10", "code", "icd10_code"))
            if not code:
                continue
            out.append({
                "resourceType": "Condition",
                "id": stable_id("cond", pid, i, code),
                "clinicalStatus": {"coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active"}]},
                "code": {"coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": code}],
                    "text": str(pick(dx, "display", "name", "text",
                                     default=code))},
                "subject": pat_ref,
                "encounter": {"reference": f"Encounter/{enc_id}"},
                "recordedDate": date,
            })
    return out


def to_bundle(profile: dict, facility: str) -> dict:
    resources = [patient_resource(profile)] + encounter_resources(profile, facility)
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{
            "resource": r,
            "request": {"method": "PUT",  # upsert: re-seeding is idempotent
                        "url": f"{r['resourceType']}/{r['id']}"},
        } for r in resources],
    }


def convert(profiles_path: str, out_dir: str, facility: str, limit: int | None) -> int:
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    with open(profiles_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            profile = json.loads(line)
            bundle = to_bundle(profile, facility)
            pid = str(pick(profile, "patient_id", "id"))
            with open(os.path.join(out_dir, f"{stable_id('patient', pid)}.json"), "w") as out:
                json.dump(bundle, out, indent=1)
            n += 1
            if limit and n >= limit:
                break
    return n


def medplum_token(base: str, client_id: str, client_secret: str) -> str:
    """Medplum OAuth2 client-credentials → access token. Medplum is the
    production FHIR core (ADR-0010); its API lives at {base}/fhir/R4."""
    body = json.dumps({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/oauth2/token",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


def load(out_dir: str, shr: str, token: str | None,
         medplum: bool = False) -> int:
    """PUT each resource to the FHIR endpoint. --medplum targets a Medplum
    server ({base}/fhir/R4/..., client-credentials auth via
    MEDPLUM_CLIENT_ID / MEDPLUM_CLIENT_SECRET); otherwise the lightweight
    shr-service envelope. Fail-loud on the first error: a half-seeded
    demo is worse than none."""
    base = shr.rstrip("/")
    fhir_base = f"{base}/fhir/R4" if medplum else f"{base}/fhir"
    if medplum and not token:
        token = medplum_token(base, os.environ["MEDPLUM_CLIENT_ID"],
                              os.environ["MEDPLUM_CLIENT_SECRET"])
    loaded = 0
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json"):
            continue
        bundle = json.load(open(os.path.join(out_dir, name)))
        for entry in bundle["entry"]:
            r = entry["resource"]
            req = urllib.request.Request(
                f"{fhir_base}/{r['resourceType']}/{r['id']}",
                data=json.dumps(r).encode(), method="PUT",
                headers={"Content-Type": "application/fhir+json"})
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status not in (200, 201):
                    raise SystemExit(f"load failed at {name}: HTTP {resp.status}")
                loaded += 1
    return loaded


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic Hospital → FHIR seed pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("profiles")
    c.add_argument("out_dir")
    c.add_argument("--facility", default="PHC-DEMO")
    c.add_argument("--limit", type=int)
    l = sub.add_parser("load")
    l.add_argument("out_dir")
    l.add_argument("--shr", required=True, help="base URL of shr-service or Medplum")
    l.add_argument("--token", default=os.environ.get("SHR_TOKEN"))
    l.add_argument("--medplum", action="store_true",
                   help="target Medplum (/fhir/R4, client-credentials via env)")
    args = ap.parse_args(argv)

    if args.cmd == "convert":
        n = convert(args.profiles, args.out_dir, args.facility, args.limit)
        print(f"converted {n} patients → {args.out_dir}")
        return 0
    n = load(args.out_dir, args.shr, args.token, medplum=args.medplum)
    print(f"loaded {n} resources into {args.shr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
