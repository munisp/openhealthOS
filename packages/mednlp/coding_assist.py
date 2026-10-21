"""coding_assist.py — free-text note -> terminology codes (ADR-0065).

The coding-quality problem at PHC level: a nurse writes "malaria fever,
gave amodiaquine"; nobody on site is a trained coder. The pipeline:

1. EXTRACT clinical mentions. Today: a transparent rule spotter whose
   output shape matches OpenMed NER spans exactly ({text,label,start,end})
   — when the model path is live, openmed.extract_clinical_entities drops
   in at this seam and everything downstream is unchanged.
2. MAP mentions to the terminology-service snapshots (ADR-0056 data):
   ICD-10 for conditions, NAFDAC for drugs, NHIA tariff for billables.
   Unmapped mentions are reported as unmapped — never guessed to the
   nearest code. Coding that lies is worse than no coding.
"""
import json
import os

from ngpatterns import detect  # noqa: F401  (re-export seam for pipelines)

TERM_DIR = os.path.join(os.path.dirname(__file__), "..", "..",
                        "data", "terminology")

# minimal rule spotter; honest about being a stand-in for model NER.
RULE_SPANS = [
    (r"\bmalaria\b", "condition"),
    (r"\btyphoid\b", "condition"),
    (r"\bhypertension\b|\bhigh blood pressure\b", "condition"),
    (r"\bdiabetes\b", "condition"),
    (r"\bpneumonia\b", "condition"),
    (r"\bamodiaquine\b", "drug"),
    (r"\bartemether\b", "drug"),
    (r"\bamoxicillin\b", "drug"),
    (r"\bparacetamol\b", "drug"),
    (r"\bamlodipine\b", "drug"),
    (r"\bmetformin\b", "drug"),
]


def extract_mentions(text):
    import re
    spans = []
    for pattern, label in RULE_SPANS:
        for m in re.finditer(pattern, text, re.I):
            spans.append({"start": m.start(), "end": m.end(),
                          "text": m.group(0).lower(), "label": label})
    spans.sort(key=lambda s: s["start"])
    return spans


class TerminologyMapper:
    """Maps mentions against the terminology snapshots (offline)."""

    def __init__(self, term_dir=TERM_DIR):
        self.index = {}  # (kind, normalized_display) -> {system, code, display}
        for fname, kind in (("icd10-core.json", "condition"),
                            ("nafdac-core.json", "drug"),
                            ("nhia-tariff-core.json", "billable")):
            path = os.path.join(term_dir, fname)
            with open(path, encoding="utf-8") as fh:
                snap = json.load(fh)
            for c in snap.get("concepts", []):
                disp = c.get("display", "")
                self.index[(kind, disp.lower())] = {
                    "system": snap.get("url", fname), "code": c.get("code"),
                    "display": disp}

    def lookup(self, kind, mention):
        """Exact then substring match against displays; None if unmapped."""
        key = (kind, mention.lower())
        if key in self.index:
            return self.index[key]
        for (k, disp), v in self.index.items():
            if k == kind and (mention.lower() in disp or disp in mention.lower()):
                return v
        return None


def assist(text, mapper=None):
    """Full assist: extract -> map. Returns mapped codes + honest unmapped."""
    mapper = mapper or TerminologyMapper()
    mentions = extract_mentions(text)
    mapped, unmapped = [], []
    for s in mentions:
        hit = mapper.lookup(s["label"], s["text"])
        if hit:
            mapped.append({**s, **hit})
        else:
            unmapped.append(s)
    return {"mentions": len(mentions), "mapped": mapped, "unmapped": unmapped}
