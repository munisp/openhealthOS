"""portal — the patient-facing "My Record" payload (chart-portal@1.0.0).

The patient-portal trail: a patient should see their own chart — visits,
medications, allergies, problems, immunizations — in plain language,
offline-capable, and shareable. This exporter derives the portal payload
from the same IPS bundle that would travel to a foreign ED, so the
patient and the receiving clinician read the same truth — alignment by
construction, never by copy-paste.

Doctrine:
- THE PATIENT READS THE SAME RECORD: the portal renders the IPS
  sections; a portal that tells a different story than the clinical
  record is a liability.
- WITHHELD IS STATED: consent-withheld sections render as
  "withheld — ask your clinician", never silently dropped.
- THE CHECKSUM IS THE CONTRACT: the payload carries the IPS fingerprint
  plus its own sha256, like every other surface.
"""

from __future__ import annotations

import hashlib
import json

from ips import SECTIONS, fingerprint as ips_fingerprint
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

PORTAL_VERSION = "chart-portal@1.0.0"

# Plain-language titles per section — adding a section adds an entry.
DISPLAY = {
    "medications": "Your medications",
    "allergies": "Your allergies",
    "problems": "Your health conditions",
    "immunizations": "Your vaccinations",
    "history-of-procedures": "Procedures you have had",
    "results": "Your test results",
    "vitals": "Your latest measurements",
    "pregnancy": "Pregnancy history",
}

EMPTY_DISPLAY = {
    "none-known": "None known",
    "withheld-by-consent": "Withheld — ask your clinician",
    "not-collected": "Not recorded",
}


class PortalError(Exception):
    pass


def _item_display(resource):
    """Render one clinical item for a lay reader: prefer an explicit
    display, else the scalar fields as 'key: value' pairs."""
    if resource.get("display"):
        return str(resource["display"])
    parts = [f"{k}: {v}" for k, v in sorted(resource.items())
             if k != "meta" and isinstance(v, (str, int, float))]
    return "; ".join(parts) if parts else "(recorded)"


def portal_payload(ips_bundle, *, visits=(), share_link=None):
    """ips_bundle: output of ips.build_ips (FHIR document Bundle).
    visits: [{day, facility, summary, note_sha256}] newest-first.
    share_link: an optional SMART Health Link (ips.shl) for sharing.
    Returns a patient-facing payload with its own fingerprint."""
    if not isinstance(ips_bundle, dict) or \
            ips_bundle.get("resourceType") != "Bundle" or \
            ips_bundle.get("type") != "document":
        raise PortalError("the portal reads an IPS document bundle — "
                          "nothing else")
    by_url = {}
    patient = {"name": "Patient"}
    comp = None
    for e in ips_bundle.get("entry", ()):
        res = e.get("resource", {})
        by_url[e.get("fullUrl")] = res
        if res.get("resourceType") == "Composition":
            comp = res
        elif res.get("resourceType") == "Patient":
            name = res.get("name", [{}])[0].get("text", "Patient")
            patient = {"id": res.get("id"), "name": name,
                       "birthDate": res.get("birthDate"),
                       "gender": res.get("gender")}
    if comp is None:
        raise PortalError("bundle has no Composition — not an IPS")

    loinc_to_key = {spec["code"]: key for key, spec in SECTIONS.items()}
    sections = []
    for s in comp.get("section", ()):
        key = loinc_to_key.get(
            s.get("code", {}).get("coding", [{}])[0].get("code"))
        if key is None:
            continue
        refs = s.get("entry", ())
        if refs:
            items = [_item_display(by_url[r]) for r in refs
                     if r in by_url]
            state = "recorded"
        else:
            state = s.get("emptyReason", {}).get(
                "coding", [{}])[0].get("code") or "not-collected"
            items = []
        sections.append({
            "key": key,
            "title": DISPLAY[key],
            "state": state,
            "display_state": (EMPTY_DISPLAY[state]
                              if state != "recorded" else None),
            "items": items,
        })

    body = {
        "version": PORTAL_VERSION,
        "patient": patient,
        "sections": sections,
        "visits": [dict(v) for v in visits],
        "share_link": share_link,
        "ips_fingerprint": ips_fingerprint(ips_bundle),
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(body, sha256=digest)


def export_portal(payload, out_path):
    payloadio.write_text(out_path, json.dumps(payload, indent=2, ensure_ascii=False))
    return out_path
