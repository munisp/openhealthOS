"""OpenHealth OS — fhirguard: write-side validation for AI-authored FHIR (ADR-0046).

Doctrine adopted clean-room from HealthChain's core insight (Apache-2.0,
healthchainai/HealthChain): agents READ clinical data fine; WRITING it
back correctly is where harm happens. Three rules, enforced before any
AI/agent-authored resource touches the SHR:

  1. Validated write — code comes from a recognised system (ICD-10-CM,
     SNOMED CT, LOINC), the subject reference resolves to a Patient we
     hold, and clinical status fields are spec-legal values, not prose.
  2. No invented facts — the resource may not claim timestamps, statuses
     or performers the caller did not explicitly supply; fhirguard never
     auto-fills clinical fields. (Structural ids/recordedDate set by the
     STORE are store metadata, not clinical claims.)
  3. Provenance tag — every accepted AI write carries
     meta.tag[system=.../provenance, code=ai-authored]; downstream
     consumers (SHR viewers, claims, surveillance) can always
     distinguish machine-written content. Mirrors the synthetic-hospital
     tag from ADR-0045.

Usage (library):
    from fhirguard import validate_ai_write, FHIRGuardError
    validate_ai_write(resource, known_patient_ids={"patient-abc"}, author="triage-llm")

Usage (CLI, for pipes / pre-commit checks on generated fixtures):
    python3 fhirguard.py check RESOURCE.json --patients patients.txt --author triage-llm

Stdlib only (ADR-0005). This guards WRITES; it is not a FHIR server.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

PROVENANCE_SYSTEM = "https://openhealthos.org/provenance"
AI_TAG = "ai-authored"

# Recognised clinical code systems. A code from anywhere else in a
# clinical field is either a typo or an invention — both rejected.
KNOWN_SYSTEMS = {
    "http://hl7.org/fhir/sid/icd-10-cm",
    "http://hl7.org/fhir/sid/icd-10",
    "http://snomed.info/sct",
    "http://loinc.org",
}

# Spec-legal clinical statuses per resource type (FHIR R4).
LEGAL_STATUS = {
    "Condition": {"active", "recurrence", "relapse", "inactive",
                  "remission", "resolved", "unknown"},
    "Observation": {"registered", "preliminary", "final", "amended",
                    "corrected", "cancelled", "entered-in-error", "unknown"},
    "MedicationRequest": {"active", "on-hold", "cancelled", "completed",
                          "entered-in-error", "stopped", "draft", "unknown"},
    "Encounter": {"planned", "arrived", "triaged", "in-progress",
                  "onleave", "finished", "cancelled", "entered-in-error",
                  "unknown"},
}

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$")


class FHIRGuardError(ValueError):
    pass


def _fail(problems: list[str], msg: str):
    problems.append(msg)


def validate_ai_write(resource: dict, known_patient_ids: set[str],
                      author: str) -> dict:
    """Validate an AI-authored FHIR resource. Returns a COPY carrying the
    provenance tag; raises FHIRGuardError listing every problem found
    (fail-loud, all reasons at once — an agent fixing one error at a
    time is how retries multiply).
    """
    problems: list[str] = []
    rtype = resource.get("resourceType")
    if rtype not in LEGAL_STATUS and rtype not in ("Patient", "DocumentReference", "Bundle"):
        _fail(problems, f"resourceType {rtype!r} not in the AI-write allowlist "
                        f"{sorted(LEGAL_STATUS)} — novel types need human review")

    # Rule 1a: clinical codes from recognised systems.
    for codable_path in ("code", "type"):
        codable = resource.get(codable_path)
        if isinstance(codable, dict):
            for coding in codable.get("coding", []):
                sys_uri = coding.get("system", "")
                if sys_uri and sys_uri not in KNOWN_SYSTEMS:
                    _fail(problems, f"{codable_path}.coding system {sys_uri!r} not recognised "
                                    f"(allowed: ICD-10-CM, SNOMED CT, LOINC)")

    # Rule 1b: subject must reference a patient we hold.
    subj = resource.get("subject", {})
    if rtype not in ("Patient", "Bundle"):
        ref = subj.get("reference", "") if isinstance(subj, dict) else ""
        if not ref.startswith("Patient/"):
            _fail(problems, f"subject.reference {ref!r} must be Patient/<id>")
        else:
            pid = ref.split("/", 1)[1]
            if pid not in known_patient_ids:
                _fail(problems, f"subject Patient/{pid} not in the SHR — "
                                f"an AI write may not invent a patient")

    # Rule 1c: status fields are spec-legal, not prose.
    if rtype in LEGAL_STATUS:
        if rtype == "Condition":
            status = (resource.get("clinicalStatus", {}).get("coding") or [{}])[0].get("code")
        else:
            status = resource.get("status")
        if status is not None and status not in LEGAL_STATUS[rtype]:
            _fail(problems, f"{rtype} status {status!r} not in {sorted(LEGAL_STATUS[rtype])}")

    # Rule 2: no invented facts — any dateTime present must be real
    # ISO-8601 (catch model-generated garbage like "last Tuesday"), and
    # entered-in-error is forbidden for AI writes (that is a human
    # adjudication, not a model output).
    for field in ("recordedDate", "authoredOn", "effectiveDateTime", "occurrenceDateTime"):
        v = resource.get(field)
        if v is not None and not ISO_DATE.match(str(v)):
            _fail(problems, f"{field} {v!r} is not ISO-8601 — no invented or fuzzy dates")
    if rtype in LEGAL_STATUS:
        cur = (resource.get("clinicalStatus", {}).get("coding") or [{}])[0].get("code") \
            if rtype == "Condition" else resource.get("status")
        if cur == "entered-in-error":
            _fail(problems, "AI writes may not assert entered-in-error — "
                            "corrections route through human review")

    # Rule 3: provenance — tag a copy; never mutate the caller's object.
    out = json.loads(json.dumps(resource))
    meta = out.setdefault("meta", {})
    tags = meta.setdefault("tag", [])
    if not any(t.get("system") == PROVENANCE_SYSTEM and t.get("code") == AI_TAG
               for t in tags):
        tags.append({"system": PROVENANCE_SYSTEM, "code": AI_TAG,
                     "display": f"AI-authored ({author})"})

    if problems:
        raise FHIRGuardError("; ".join(problems))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="validate an AI-authored FHIR write")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("resource")
    c.add_argument("--patients", required=True,
                   help="file with one known Patient id per line")
    c.add_argument("--author", required=True)
    args = ap.parse_args(argv)

    resource = json.load(open(args.resource))
    known = {l.strip() for l in open(args.patients) if l.strip()}
    try:
        out = validate_ai_write(resource, known, args.author)
    except FHIRGuardError as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
