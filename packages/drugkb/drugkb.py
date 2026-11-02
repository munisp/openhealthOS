"""formulary.py — the pharmacy knowledge base: a curated open subset.

Commercial interaction databases (First Databank, Lexicomp) are
licensed content we cannot embed. The honest path the gap analysis
names: a curated open subset plus local formulary rules. Every rule
carries an evidence reference; the tables are DATA, versioned — a
dosing rule without a version is an unaccountable intervention.

Absence from these tables is NOT clearance: an unrated pair reports
'unrated', and the pharmacist review queue stays the safeguard. The
renal table moved here from crosslink so drug knowledge has ONE home;
crosslink re-exports it for its eGFR seam.
"""


class FormularyError(Exception):
    pass


FORMULARY_VERSION = "formulary-ng@1.0.0"

# ---- renal dosing table (moved from crosslink: one home for drug
# knowledge; thresholds ascending, the first band the eGFR falls below
# wins — most restrictive match) ----
RENAL_RULES_VERSION = "renal-rules@1.0.0"
EGFR_STALE_DAYS = 90   # an eGFR from last quarter is history, not chemistry

RENAL_RULES = {
    # drug-or-class: [(below_egfr, verdict, note), ...] ascending
    "metformin": [
        (30, "contraindicated", "eGFR <30: lactic acidosis risk"),
        (45, "dose-adjust", "eGFR 30-44: halve dose, recheck renal fn")],
    "nsaid": [
        (30, "contraindicated", "eGFR <30: haemodynamic AKI risk"),
        (60, "dose-adjust", "eGFR 30-59: avoid chronic use, hydrate, monitor")],
    "enoxaparin": [
        (15, "contraindicated", "eGFR <15: accumulation, bleeding"),
        (30, "dose-adjust", "eGFR 15-29: once-daily, anti-Xa monitoring")],
    "spironolactone": [
        (30, "contraindicated", "eGFR <30: hyperkalaemia risk")],
    "vancomycin": [
        (30, "dose-adjust", "eGFR <30: extend interval, dose by trough")],
    "gentamicin": [
        (30, "dose-adjust", "eGFR <30: extended interval + levels; "
                            "nephrotoxic — reassess indication")],
    "acyclovir": [
        (30, "dose-adjust", "eGFR <30: extend interval; crystalluria risk")],
}

# ---- drug classes (WHO EML-flavoured subset, Nigeria formulary) ----
DRUG_CLASSES = {
    "nsaid": ("ibuprofen", "diclofenac", "naproxen", "aspirin",
              "ketorolac", "celecoxib"),
    "ace-inhibitor": ("lisinopril", "enalapril", "ramipril"),
    "arb": ("losartan", "valsartan"),
    "aminoglycoside": ("gentamicin", "amikacin"),
    "fluoroquinolone": ("ciprofloxacin", "levofloxacin", "ofloxacin"),
    "macrolide": ("erythromycin", "azithromycin", "clarithromycin"),
    "sulfonamide": ("cotrimoxazole", "sulfamethoxazole"),
    "antacid": ("aluminium-hydroxide", "magnesium-hydroxide",
                "calcium-carbonate"),
    "loop-diuretic": ("furosemide", "bumetanide"),
    "opioid": ("morphine", "tramadol", "codeine", "pethidine"),
    "benzodiazepine": ("diazepam", "lorazepam", "midazolam"),
}

# ---- interaction pairs: drug-or-class × drug-or-class ----
# (a, b, severity, mechanism, evidence_ref) — curated open subset only;
# each entry must carry an open evidence reference or it does not ship.
_REF = "who-model-formulary/open-pharmacology-consensus"
INTERACTIONS = [
    ("warfarin", "nsaid", "major",
     "additive bleeding risk (antiplatelet + gastric mucosa)", _REF),
    ("warfarin", "sulfonamide", "major",
     "CYP2C9 inhibition raises INR — bleeding", _REF),
    ("warfarin", "macrolide", "major",
     "CYP inhibition raises INR — bleeding", _REF),
    ("methotrexate", "nsaid", "major",
     "reduced renal clearance — methotrexate toxicity", _REF),
    ("spironolactone", "ace-inhibitor", "major", "hyperkalaemia", _REF),
    ("spironolactone", "arb", "major", "hyperkalaemia", _REF),
    ("ace-inhibitor", "arb", "moderate",
     "dual RAS blockade — hyperkalaemia/AKI", _REF),
    ("aminoglycoside", "loop-diuretic", "major",
     "additive ototoxicity and nephrotoxicity", _REF),
    ("fluoroquinolone", "antacid", "moderate",
     "chelation reduces absorption — separate by 2 hours", _REF),
    ("opioid", "benzodiazepine", "major",
     "additive respiratory depression", _REF),
]

# ---- daily dose ceilings (mg/day) ----
MAX_DAILY_MG = {
    "paracetamol": (4000, "hepatotoxicity ceiling", _REF),
    "ibuprofen": (2400, "prescription ceiling; OTC ceiling lower", _REF),
    "diclofenac": (150, "cardiovascular risk rises with dose", _REF),
    "tramadol": (400, "seizure risk above ceiling", _REF),
}

_SEVERITY_RANK = {"clear": 0, "dose-adjust": 1, "moderate": 1,
                  "major": 2, "contraindicated": 3}


def classes_of(drug):
    """The classes a drug belongs to; empty set for unmapped drugs —
    unmapped is not safe, just unmapped."""
    d = drug.strip().lower()
    return {cls for cls, members in DRUG_CLASSES.items() if d in members}


def renal_verdict(drug, egfr):
    """Apply the versioned renal table to a bare eGFR value.
    Checks the drug AND its classes; the most restrictive match wins.
    Returns None when the drug is unrated — unrated is not clearance."""
    d = drug.strip().lower()
    best = None
    matched = []
    for key in (d, *sorted(classes_of(d))):
        rules = RENAL_RULES.get(key)
        if not rules:
            continue
        matched.append(key)
        for below, verdict, note in rules:
            if egfr < below:
                cand = {"verdict": verdict, "note": note}
                break
        else:
            cand = {"verdict": "clear", "note": "within renal bands"}
        if best is None or (_SEVERITY_RANK[cand["verdict"]] >
                            _SEVERITY_RANK[best["verdict"]]):
            best = cand
    if best is None:
        return None
    return dict(best, matched=matched, rule_version=RENAL_RULES_VERSION)


def _pair_hits(a, b, d, dcls, m, mcls):
    da, ma = a in ({d} | dcls), a in ({m} | mcls)
    db, mb = b in ({d} | dcls), b in ({m} | mcls)
    return (da and mb) or (db and ma)


def check_order(drug, *, dose_mg_per_day=None, current_meds=(),
                allergies=(), egfr=None):
    """The knowledge base meets one order. Findings, never silence."""
    if not drug or not drug.strip():
        raise FormularyError("no drug named — nothing to check")
    d = drug.strip().lower()
    dcls = classes_of(d)
    findings = []
    seen = set()

    def add(kind, severity, **kw):
        key = (kind, kw.get("against", ""), kw.get("detail", ""))
        if key in seen:
            return
        seen.add(key)
        findings.append({"kind": kind, "severity": severity, **kw})

    al = {a.strip().lower() for a in allergies}
    hits = ({d} | dcls) & al
    for h in sorted(hits):
        add("allergy", "contraindicated", detail=h,
            note=f"'{h}' is on the allergy list — the order fights the "
                 "chart")

    for med in current_meds:
        m = med.strip().lower()
        mcls = classes_of(m)
        if m == d:
            add("duplication", "major", against=m,
                note="same drug twice — duplicate therapy")
            continue
        shared = dcls & mcls
        if shared:
            add("duplication", "moderate", against=m,
                note=f"same class ({sorted(shared)[0]}) — duplicate "
                     "class therapy")
        for (a, b, sev, mech, ref) in INTERACTIONS:
            if _pair_hits(a, b, d, dcls, m, mcls):
                add("interaction", sev, against=m, mechanism=mech,
                    evidence_ref=ref)

    if dose_mg_per_day is not None and d in MAX_DAILY_MG:
        cap, note, ref = MAX_DAILY_MG[d]
        if dose_mg_per_day > cap:
            add("dose-ceiling", "major",
                detail=f"{dose_mg_per_day}mg/day > {cap}mg/day",
                note=note, evidence_ref=ref)

    if egfr is not None:
        rv = renal_verdict(d, egfr)
        if rv and rv["verdict"] != "clear":
            add("renal", ("contraindicated" if rv["verdict"] ==
                          "contraindicated" else "major"),
                detail=f"eGFR {egfr}: {rv['verdict']}",
                note=rv["note"], rule_version=rv["rule_version"])

    return {"drug": d, "findings": findings, "clear": not findings,
            "formulary_version": FORMULARY_VERSION,
            "note": "absence from this table is not clearance — unrated "
                    "pairs stay with the pharmacist"}
