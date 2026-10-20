"""Open formulary — WHO EML core + NAFDAC-style registry entries as DATA.

Doctrine (ADR-0056): we never embed a licensed drug database (e.g. proprietary
interaction compendia). Interaction and dose rules here are curated, openly
sourced clinical knowledge expressed as data, reviewable in version control.
Deployers may layer a licensed compendium via Formulary.load_extra().
"""
from __future__ import annotations
import json, re

# (code, name, class, form, strength, who_eml)
DRUGS = [
    ("NG-AMOX500", "Amoxicillin", "penicillin", "capsule", "500mg", True),
    ("NG-AMOX250S", "Amoxicillin", "penicillin", "suspension", "250mg/5ml", True),
    ("NG-ACT-AL", "Artemether/Lumefantrine", "antimalarial", "tablet", "20/120mg", True),
    ("NG-SP", "Sulfadoxine/Pyrimethamine", "antimalarial", "tablet", "500/25mg", True),
    ("NG-ORS", "Oral rehydration salts", "electrolyte", "sachet", "1L", True),
    ("NG-ZINC10", "Zinc sulfate", "supplement", "tablet", "10mg", True),
    ("NG-PARA500", "Paracetamol", "analgesic", "tablet", "500mg", True),
    ("NG-IBU400", "Ibuprofen", "nsaid", "tablet", "400mg", True),
    ("NG-AMLO5", "Amlodipine", "ccb", "tablet", "5mg", True),
    ("NG-LISIN10", "Lisinopril", "acei", "tablet", "10mg", True),
    ("NG-METF500", "Metformin", "biguanide", "tablet", "500mg", True),
    ("NG-INSULIN", "Insulin (human)", "insulin", "injection", "100IU/ml", True),
    ("NG-SALB100", "Salbutamol", "beta2-agonist", "inhaler", "100mcg", True),
    ("NG-FOLIC5", "Folic acid", "supplement", "tablet", "5mg", True),
    ("NG-FEFOL", "Iron/Folic acid", "supplement", "tablet", "60/0.4mg", True),
    ("NG-OXYTOCIN", "Oxytocin", "uterotonic", "injection", "10IU/ml", True),
    ("NG-MGSO4", "Magnesium sulfate", "anticonvulsant", "injection", "50%", True),
    ("NG-MISO600", "Misoprostol", "uterotonic", "tablet", "600mcg", True),
    ("NG-WARF5", "Warfarin", "anticoagulant", "tablet", "5mg", False),
    ("NG-ASA75", "Aspirin", "antiplatelet", "tablet", "75mg", True),
    ("NG-CIPRO500", "Ciprofloxacin", "fluoroquinolone", "tablet", "500mg", True),
    ("NG-DOXY100", "Doxycycline", "tetracycline", "capsule", "100mg", True),
]

# class-level interaction rules: (class_a, class_b, severity, message)
# severity: "contraindicated" (fail-closed) | "major" | "moderate"
CLASS_INTERACTIONS = [
    ("acei", "nsaid", "major", "NSAIDs blunt ACE-inhibitor effect and risk renal injury"),
    ("anticoagulant", "nsaid", "major", "Additive bleeding risk"),
    ("anticoagulant", "antiplatelet", "major", "Additive bleeding risk"),
    ("anticoagulant", "fluoroquinolone", "moderate", "May potentiate anticoagulation; monitor INR"),
    ("biguanide", "nsaid", "moderate", "Renal risk with dehydration; review"),
    ("insulin", "beta2-agonist", "moderate", "Beta-agonists may raise blood glucose"),
    ("penicillin", "tetracycline", "moderate", "Bacteriostatic may blunt bactericidal effect"),
]

# drug-level dose ranges: code -> {min_mg_day, max_mg_day, max_mg_dose, note}
DOSE_RULES = {
    "NG-PARA500": {"max_mg_day": 4000, "max_mg_dose": 1000, "note": "Hepatotoxic above 4g/day"},
    "NG-IBU400": {"max_mg_day": 2400, "max_mg_dose": 800, "note": "Take with food"},
    "NG-AMLO5": {"max_mg_day": 10, "max_mg_dose": 10, "note": "Elderly start 2.5mg"},
    "NG-WARF5": {"max_mg_day": 15, "max_mg_dose": 10, "note": "Titrate to INR"},
    "NG-MISO600": {"max_mg_day": 1200, "max_mg_dose": 600, "note": "PPH prophylaxis 600mcg single dose"},
}

class AllergyConflict(Exception):
    """Raised on patient allergy vs prescribed class — FAIL-CLOSED."""

def _index():
    return {c: {"code": c, "name": n, "class": k, "form": f, "strength": s, "eml": e}
            for c, n, k, f, s, e in DRUGS}

class Formulary:
    def __init__(self, extra_path=None):
        self.drugs = _index()
        self.class_interactions = list(CLASS_INTERACTIONS)
        self.dose_rules = dict(DOSE_RULES)
        if extra_path:
            self.load_extra(extra_path)

    def load_extra(self, path):
        with open(path) as fh:
            extra = json.load(fh)
        for d in extra.get("drugs", []):
            self.drugs[d["code"]] = d
        self.class_interactions += [tuple(x) for x in extra.get("class_interactions", [])]
        self.dose_rules.update(extra.get("dose_rules", {}))

    def lookup(self, code):
        return self.drugs.get(code)

    def search(self, q):
        q = q.lower()
        return [d for d in self.drugs.values()
                if q in d["name"].lower() or q in d["code"].lower()]

    def check_allergy(self, code, patient_allergies):
        """Fail-closed: raise AllergyConflict on class or name match."""
        d = self.drugs.get(code)
        if not d:
            return
        for a in patient_allergies or []:
            a = a.lower().strip()
            if not a:
                continue
            if a == d["class"].lower() or a in d["name"].lower() or d["name"].lower() in a:
                raise AllergyConflict(
                    f"ALLERGY: patient allergic to '{a}' — {d['name']} ({d['class']}) contraindicated")

    def check_interactions(self, codes):
        """Return warn-level findings; never blocks (warn-and-override doctrine)."""
        classes = []
        for c in codes:
            d = self.drugs.get(c)
            if d:
                classes.append((c, d["class"]))
        findings = []
        for a, b, sev, msg in self.class_interactions:
            for ca, ka in classes:
                for cb, kb in classes:
                    if ca != cb and {ka, kb} == {a, b}:
                        findings.append({"drugs": sorted([ca, cb]), "severity": sev, "message": msg})
        # dedupe
        seen, out = set(), []
        for f in findings:
            key = tuple(f["drugs"])
            if key not in seen:
                seen.add(key); out.append(f)
        return out

    def check_dose(self, code, mg_per_dose, doses_per_day):
        r = self.dose_rules.get(code)
        if not r:
            return []
        findings = []
        if r.get("max_mg_dose") and mg_per_dose > r["max_mg_dose"]:
            findings.append({"severity": "major", "message":
                f"{code}: {mg_per_dose}mg exceeds max single dose {r['max_mg_dose']}mg. {r['note']}"})
        if r.get("max_mg_day") and mg_per_dose * doses_per_day > r["max_mg_day"]:
            findings.append({"severity": "major", "message":
                f"{code}: {mg_per_dose*doses_per_day}mg/day exceeds max {r['max_mg_day']}mg/day. {r['note']}"})
        return findings

def strength_mg(strength):
    m = re.match(r"^(\d+(?:\.\d+)?)mg", strength or "")
    return float(m.group(1)) if m else None
