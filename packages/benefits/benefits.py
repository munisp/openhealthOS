#!/usr/bin/env python3
"""Benefit packages as data (ADR-0031).

A patient should be able to ask any channel — USSD, PWA, the clerk's
screen — "what does my cover include?" and get the SAME answer the
claims adjudicator would act on. That requires the benefit package to be
data, not a PDF: per scheme, per service code — covered or not, co-pay,
referral rules, and the tariff that prices it.

Doctrine:
  * One catalogue. This module is the canonical scheme→coverage map;
    the adjudicator's enforcement fixture (packages/adjudicator) reads
    the same codes, so what the patient sees can never diverge from what
    gets paid.
  * Tariffs are versioned and dated. NHIA's April-2025 actuarial review
    (capitation ₦1,450/enrollee covering 90%, FFS +373-378%) is the
    seed; every entry carries tariff_year so staleness is computable.
  * Staleness is a report, not an opinion: recompute any tariff year
    against a CPI path and show the gap. A schedule that hasn't moved
    while prices have is a policy failure the data should shout about.

CLI contract (mirrors adjudicate.py): one JSON op on stdin, one JSON
result on stdout. Ops: package | explain | check | staleness.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict

# ------------------------------------------------------------- catalogue
#
# Seed: NHIA benefit package + the April-2025 tariff revision (public
# price list; amounts in kobo). tariff_year stamps the schedule vintage.
# This is a PILOT SUBSET — states upload their full negotiated schedules
# as bundle JSON (same shape as packages/adjudicator/tariffs.py).

TARIFF_YEAR = 2025

SERVICES = {
    # code: (name, category, tariff_kobo)
    "MAL-RDT":  ("Malaria rapid test", "diagnostics", 1500_00),
    "LAB-MP":   ("Malaria microscopy", "diagnostics", 2000_00),
    "LAB-HB":   ("Haemoglobin / PCV", "diagnostics", 1800_00),
    "LAB-FBC":  ("Full blood count", "diagnostics", 2600_00),
    "RAD-CXR":  ("Chest X-ray", "imaging", 1800_00),
    "ANC-001":  ("Antenatal visit", "maternal", 3000_00),
    "DEL-NORM": ("Normal delivery", "maternal", 35000_00),
    "DEL-CS":   ("Caesarean section", "maternal", 75000_00),
    "SURG-HERN": ("Hernia repair", "surgery", 35000_00),
    "ORS-001":  ("ORS + zinc (childhood diarrhoea)", "child-health", 800_00),
    "ITN-001":  ("Insecticide-treated net", "child-health", 2500_00),
    "IMM-PENTA": ("Pentavalent immunisation", "child-health", 1200_00),
    "PARA-TAB": ("Paracetamol course", "medicines", 300_00),
    "AMX-SUS":  ("Amoxicillin suspension", "medicines", 1500_00),
    "ACT-COURSE": ("Artemisinin combination therapy", "medicines", 1800_00),
    "CONS-GEN": ("General outpatient consult", "consult", 2000_00),
    "CONS-SPEC": ("Specialist consult (referral)", "consult", 8000_00),
    "DENT-EXT": ("Tooth extraction", "dental", 5000_00),
    "REN-DIAL": ("Haemodialysis session", "tertiary", 80000_00),
}

CATEGORIES_ORDER = ["consult", "diagnostics", "imaging", "medicines",
                    "maternal", "child-health", "surgery", "dental", "tertiary"]


@dataclass
class Coverage:
    covered: bool
    copay_pct: int = 10              # NHIA Apr-2025: capitation covers 90%
    referral_required: bool = False
    note: str = ""


@dataclass
class Scheme:
    scheme_id: str
    name: str
    capitation_kobo: int             # per enrollee per year
    coverage: dict = field(default_factory=dict)  # code -> Coverage


def _cov(codes, copay=10, referral=(), note=""):
    return {c: Coverage(True, copay, c in referral, note) for c in codes}


_BASIC_CODES = ["CONS-GEN", "MAL-RDT", "LAB-MP", "LAB-HB", "LAB-FBC",
                "RAD-CXR", "ANC-001", "DEL-NORM", "ORS-001", "ITN-001",
                "IMM-PENTA", "PARA-TAB", "AMX-SUS", "ACT-COURSE"]

SCHEMES = {
    "BHCPF-VUL": Scheme(
        "BHCPF-VUL", "BHCPF vulnerable-group package",
        capitation_kobo=1450_00,
        coverage={
            **_cov(_BASIC_CODES, copay=0, note="fully covered for vulnerable groups"),
            "DEL-CS": Coverage(True, 0, True, "emergency referral only"),
            "SURG-HERN": Coverage(False, note="not in BHCPF minimum package"),
            "CONS-SPEC": Coverage(True, 0, True),
            "DENT-EXT": Coverage(False),
            "REN-DIAL": Coverage(False, note="tertiary exclusion"),
        }),
    "STATE-BASIC": Scheme(
        "STATE-BASIC", "State basic healthcare package",
        capitation_kobo=1450_00,
        coverage={
            **_cov(_BASIC_CODES),
            "DEL-CS": Coverage(True, 10, True),
            "SURG-HERN": Coverage(True, 10, True),
            "CONS-SPEC": Coverage(True, 10, True),
            "DENT-EXT": Coverage(True, 10),
            "REN-DIAL": Coverage(False, note="tertiary exclusion"),
        }),
}


# ------------------------------------------------------------- queries

class BenefitsError(Exception):
    pass


def package_for(scheme_id: str) -> dict:
    s = SCHEMES.get(scheme_id)
    if s is None:
        raise BenefitsError("unknown scheme: %s" % scheme_id)
    services = []
    for code, cov in sorted(s.coverage.items()):
        name, cat, tariff = SERVICES.get(code, (code, "other", 0))
        services.append({
            "code": code, "name": name, "category": cat,
            "covered": cov.covered, "copay_pct": cov.copay_pct,
            "referral_required": cov.referral_required, "note": cov.note,
            "tariff_kobo": tariff, "tariff_year": TARIFF_YEAR,
        })
    return {
        "scheme": s.scheme_id, "name": s.name,
        "capitation_kobo": s.capitation_kobo, "tariff_year": TARIFF_YEAR,
        "services": services,
    }


def explain(scheme_id: str) -> dict:
    """Plain-language summary: what a USSD menu or PWA page can read out."""
    pkg = package_for(scheme_id)
    by_cat = {}
    for svc in pkg["services"]:
        by_cat.setdefault(svc["category"], {"covered": 0, "excluded": 0, "examples": []})
        slot = by_cat[svc["category"]]
        if svc["covered"]:
            slot["covered"] += 1
            if len(slot["examples"]) < 3:
                slot["examples"].append(svc["name"])
        else:
            slot["excluded"] += 1
    covered = [s for s in pkg["services"] if s["covered"]]
    copays = sorted({s["copay_pct"] for s in covered})
    return {
        "scheme": pkg["scheme"], "name": pkg["name"],
        "headline": ("%d covered services; co-pay %s at point of care"
                     % (len(covered),
                        "none" if copays == [0] else
                        "%d%%" % copays[0] if len(copays) == 1 else
                        "%d-%d%%" % (copays[0], copays[-1]))),
        "categories": by_cat,
        "referral_note": "Specialist consults and surgery need a referral from your primary facility.",
    }


def check(scheme_id: str, code: str) -> dict:
    """The moment-of-care question: is THIS service covered, and what does
    the patient pay? Same answer the adjudicator would give."""
    pkg = package_for(scheme_id)
    for svc in pkg["services"]:
        if svc["code"] == code:
            patient_pays = svc["tariff_kobo"] * svc["copay_pct"] // 100 if svc["covered"] else svc["tariff_kobo"]
            return {
                "scheme": scheme_id, "code": code, "name": svc["name"],
                "covered": svc["covered"], "copay_pct": svc["copay_pct"],
                "referral_required": svc["referral_required"],
                "tariff_kobo": svc["tariff_kobo"],
                "patient_pays_kobo": patient_pays,
                "note": svc["note"],
            }
    return {"scheme": scheme_id, "code": code, "covered": False,
            "note": "service not in the benefit package"}


# ------------------------------------------------------------- staleness

def staleness(target_year: int, cpi_path: dict) -> dict:
    """Recompute the seed tariffs against cumulative CPI. cpi_path maps
    year -> annual inflation rate as a fraction (e.g. {"2026": 0.15}).
    A tariff frozen since tariff_year while prices compounded is
    under-funded by gap_pct — that gap is where providers refuse
    enrollees or balance-bill them."""
    if target_year <= TARIFF_YEAR:
        raise BenefitsError("target year must be after the tariff year")
    factor = 1.0
    for y in range(TARIFF_YEAR + 1, target_year + 1):
        factor *= 1.0 + float(cpi_path.get(str(y), 0.0))
    rows = []
    for code, (name, cat, tariff) in sorted(SERVICES.items()):
        expected = round(tariff * factor)
        rows.append({
            "code": code, "name": name, "category": cat,
            "tariff_kobo": tariff, "expected_kobo": expected,
            "gap_kobo": expected - tariff,
            "gap_pct": round((factor - 1.0) * 100, 1),
        })
    cap = 1450_00
    return {
        "tariff_year": TARIFF_YEAR, "target_year": target_year,
        "cpi_factor": round(factor, 4),
        "capitation_kobo": cap, "capitation_expected_kobo": round(cap * factor),
        "services": rows,
    }


# ------------------------------------------------------------- CLI

def main():
    op = json.load(sys.stdin)
    kind = op.get("op")
    try:
        if kind == "package":
            out = package_for(op["scheme"])
        elif kind == "explain":
            out = explain(op["scheme"])
        elif kind == "check":
            out = check(op["scheme"], op["code"])
        elif kind == "staleness":
            out = staleness(int(op["target_year"]), op.get("cpi_path", {}))
        else:
            raise BenefitsError("unknown op: %r" % kind)
    except (BenefitsError, KeyError) as e:
        json.dump({"error": str(e)}, sys.stdout)
        sys.exit(1)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
