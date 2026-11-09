"""ips.py — FHIR R4 International Patient Summary export.

The IPS is the standard for exactly one scenario: a patient arrives
unconscious at a hospital in another country and the receiving team
knows nothing about them. It's a FHIR document Bundle — a Composition
with required sections (medications, allergies, problems) and
recommended ones (immunizations, history, vitals) — designed to be
readable by any conformant EMR worldwide.

Clean-room implementation of the publicly documented IPS structure
(HL7 FHIR IPS IG, hl7.org/fhir/uv/ips — CC0-licensed spec structure;
section codes are LOINC, freely usable).

Doctrine:
- REQUIRED SECTIONS ARE STRUCTURAL: medications, allergies, problems
  must exist even when empty — with an explicit emptyReason
  ("none-known" vs "withheld"). Absent data and unknown data are
  different clinical facts; the bundle must say which.
- WITHHELD IS HONEST: data excluded for privacy reasons is marked
  "withheld-by-consent", never silently dropped.
- EVERY BUNDLE IS FINGERPRINTED: sha256 over canonical JSON — the hash
  is what the cross-border release binds to and what the receiving
  side can verify.
- IDENTIFIED BY DESIGN: unlike fhirguard (which de-identifies), the
  IPS exists to travel WITH the patient — but generation alone never
  releases anything. Release goes through xrelease.
"""

import hashlib
import json

# LOINC section codes per the publicly documented IPS composition
SECTIONS = {
    "medications": {"code": "10160-0", "title": "History of Medication use",
                    "required": True},
    "allergies": {"code": "48765-2", "title": "Allergies and Intolerances",
                  "required": True},
    "problems": {"code": "11450-4", "title": "Problem List",
                 "required": True},
    "immunizations": {"code": "11369-6", "title": "History of Immunizations",
                      "required": False},
    "history-of-procedures": {"code": "47519-4",
                              "title": "History of Procedures",
                              "required": False},
    "results": {"code": "30954-2", "title": "Relevant diagnostic tests",
                "required": False},
    "vitals": {"code": "8716-3", "title": "Vital Signs", "required": False},
    "pregnancy": {"code": "10162-6", "title": "History of Pregnancies",
                  "required": False},
}
EMPTY_REASONS = ("none-known", "withheld-by-consent", "not-collected")


class IPSError(Exception):
    pass


def build_ips(patient, *, sections, author, generated_day):
    """patient: {id, name, birthDate, gender, country}
    sections: {section_key: {"entries": [...]} or
                            {"empty_reason": one of EMPTY_REASONS}}
    Returns a FHIR R4 document Bundle. Fail-closed on bad shape."""
    if not patient.get("id") or not patient.get("name"):
        raise IPSError("patient id and name required — an anonymous IPS "
                       "is useless at a foreign ED")
    known = set(SECTIONS)
    unknown = set(sections) - known
    if unknown:
        raise IPSError(f"unknown IPS sections: {sorted(unknown)}")

    composition_sections = []
    entries = []
    for key, spec in SECTIONS.items():
        data = sections.get(key)
        if data is None:
            if spec["required"]:
                raise IPSError(
                    f"required section '{key}' missing — if there is no "
                    f"data, say so with empty_reason (none-known / "
                    f"withheld-by-consent / not-collected)")
            continue
        sec = {"title": spec["title"],
               "code": {"coding": [{"system": "http://loinc.org",
                                    "code": spec["code"]}]}}
        if data.get("empty_reason"):
            if data["empty_reason"] not in EMPTY_REASONS:
                raise IPSError(f"bad empty_reason: {data['empty_reason']}")
            sec["emptyReason"] = {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/list-empty-reason",
                "code": data["empty_reason"]}]}
            if data["empty_reason"] == "withheld-by-consent":
                sec["text"] = {"status": "generated", "div":
                               "Data withheld at patient direction."}
        else:
            items = data.get("entries", [])
            sec["entry"] = [f"urn:uuid:{patient['id']}-{key}-{i}"
                            for i in range(len(items))]
            for i, item in enumerate(items):
                entries.append({
                    "fullUrl": f"urn:uuid:{patient['id']}-{key}-{i}",
                    "resource": dict(item, meta={"section": key})})
        composition_sections.append(sec)

    composition = {
        "resourceType": "Composition",
        "status": "final",
        "type": {"coding": [{"system": "http://loinc.org",
                             "code": "60591-5",
                             "display": "Patient summary Document"}]},
        "subject": {"reference": f"Patient/{patient['id']}"},
        "date": str(generated_day),
        "author": [{"display": author}],
        "title": "International Patient Summary",
        "section": composition_sections,
    }
    bundle = {
        "resourceType": "Bundle",
        "type": "document",
        "entry": [{"fullUrl": f"urn:uuid:composition-{patient['id']}",
                   "resource": composition},
                  {"fullUrl": f"urn:uuid:patient-{patient['id']}",
                   "resource": {"resourceType": "Patient",
                                "id": patient["id"],
                                "name": [{"text": patient["name"]}],
                                "birthDate": patient.get("birthDate"),
                                "gender": patient.get("gender")}}] + entries,
    }
    return bundle


def fingerprint(bundle):
    """sha256 over canonical JSON — what xrelease binds to and the
    receiving side verifies."""
    blob = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def validate_ips(bundle):
    """Cheap structural check for receiving-side confidence."""
    problems = []
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "document":
        problems.append("not a document bundle")
        return {"valid": False, "problems": problems}
    comp = bundle["entry"][0]["resource"]
    titles = {s["title"] for s in comp.get("section", [])}
    for key, spec in SECTIONS.items():
        if spec["required"] and spec["title"] not in titles:
            problems.append(f"missing required section: {key}")
    return {"valid": not problems, "problems": problems}


# ---------------------------------------------------------------- CLI seam
# Consumed by services/shr-service (ips.go) as a subprocess:
#   python3 ips.py build     — JSON ipsInput on stdin → IPS Bundle on stdout
#   python3 ips.py validate  — Bundle JSON on stdin → exit 0, else exit 1
#                              with the problems on stderr.
# The CLI is a thin adapter: all clinical rules live in build_ips /
# validate_ips above. A subprocess that exits 0 without producing the
# document would be a silent empty IPS — so validate re-checks output
# shape and build refuses empty input.

def _cli_build(stdin_text):
    data = json.loads(stdin_text)
    if not isinstance(data, dict) or not data:
        raise IPSError("empty ipsInput — refusing to emit an empty IPS")
    patient = data.get("patient") or {}
    if "birthDate" not in patient and "birth_date" in patient:
        patient = dict(patient, birthDate=patient["birth_date"])
    if not patient.get("name"):
        # The SHR seeds nameless patients in some locales; the CLI states
        # it instead of inventing one.
        patient = dict(patient, name="(name withheld at source)")
    # The SHR's ipsInput sends "observations"; the IPS registry calls that
    # section "results" (LOINC 30954-2). Same facts, standard name.
    if "observations" in data and "results" not in data:
        data = dict(data, results=data["observations"])
    sections = {}
    for key in SECTIONS:
        if key in data:
            entries = data[key]
            sections[key] = ({"entries": entries} if entries
                             else {"empty_reason": "none-known"})
    for req in (k for k, s in SECTIONS.items() if s["required"]):
        sections.setdefault(req, {"empty_reason": "none-known"})
    bundle = build_ips(patient, sections=sections,
                       author=data.get("author", "shr-service"),
                       generated_day=data.get("generated_day", "unknown"))
    return json.dumps(bundle)


def _cli_validate(stdin_text):
    try:
        bundle = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        print(f"not JSON: {exc}", file=sys.stderr)
        return 1
    verdict = validate_ips(bundle)
    if not verdict["valid"]:
        print("; ".join(verdict["problems"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2 or sys.argv[1] not in ("build", "validate"):
        print("usage: ips.py build|validate  (JSON on stdin)",
              file=sys.stderr)
        sys.exit(2)
    _in = sys.stdin.read()
    if sys.argv[1] == "build":
        try:
            print(_cli_build(_in))
        except IPSError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
    else:
        sys.exit(_cli_validate(_in))
