"""capture.py — free text in, structured draft record out.

The CHW speaks or types naturally: "malaria, gave amodiaquine 200mg,
temp 38.9". This module turns that into a draft structured form using
the mednlp coding assist (real ICD-10/NAFDAC terminology snapshots) —
the user verifies the draft; nothing is a record until confirmed.

Honesty rules (the voice path makes them MORE important, not less):
- Unmapped mentions are listed as unmapped — the form shows what it
  could NOT code, never silently drops it.
- A draft derived from free text carries source:"free-text" and the
  original text, so downstream review can always see the provenance.
- PII detected in the narrative is flagged (names/phones do not belong
  in a structured clinical draft) — flagged, not silently stripped:
  the user decides.
- Voice input arrives as text (the STT layer is the voicebridge seam);
  this module is deterministic and offline.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "mednlp"))
import coding_assist  # noqa: E402
import ngpatterns  # noqa: E402

DOSE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|ml|g|tablets?|tabs?)\b", re.I)
TEMP_RE = re.compile(r"(?:temp|temperature)[:\s]*(\d{2}(?:\.\d)?)\b", re.I)


def capture(text, mapper=None):
    """Free text -> draft form. Deterministic, offline, honest."""
    a = coding_assist.assist(text, mapper=mapper) if mapper else coding_assist.assist(text)
    draft = {"source": "free-text", "original": text, "fields": {},
             "unmapped": a["unmapped"], "warnings": []}

    # conditions and drugs with real terminology codes
    conds = [m for m in a["mapped"] if m.get("system", "").endswith("icd-10")]
    drugs = [m for m in a["mapped"] if "icd-10" not in m.get("system", "")]
    if conds:
        draft["fields"]["diagnosis"] = [
            {"code": c["code"], "display": c["display"]} for c in conds]
    if drugs:
        draft["fields"]["medications"] = [
            {"code": d["code"], "display": d["display"]} for d in drugs]

    d = DOSE_RE.search(text)
    if d:
        draft["fields"]["dose"] = {"value": float(d.group(1)),
                                   "unit": d.group(2).lower()}
    t = TEMP_RE.search(text)
    if t:
        draft["fields"]["temperature_c"] = float(t.group(1))

    # PII in the narrative: flag, never strip silently
    pii = ngpatterns.detect(text)
    if pii:
        draft["warnings"].append({
            "kind": "pii-in-narrative",
            "labels": sorted({s["label"] for s in pii}),
            "message": "identifiers detected in the note — keep them out "
                       "of the structured record",
        })

    # an empty draft is an honest empty draft
    if not draft["fields"]:
        draft["warnings"].append({"kind": "nothing-extracted",
                                  "message": "no clinical content recognised — manual entry"})
    return draft
