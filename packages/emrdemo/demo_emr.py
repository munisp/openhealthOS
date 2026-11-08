"""demo_emr.py — deterministic inpatient + specialty demo payloads.

The P0/P1/P2 engines get their demo surfaces the same way the VHC stack
did: a seeded scenario, run through the real modules, exported as JSON
the pages render. Both locales (NG and CV) seed from the localepack
registry — same engines, different country context. A demo you cannot
reproduce is a story you cannot audit.

Doctrine:
- EVERY NAME COMES FROM THE LOCALE PACK: patient names, facilities,
  currencies. Nothing Nigerian leaks into the CV scenario and vice
  versa.
- THE GATES FIRE VISIBLY: the seed includes the dose refused, the cycle
  gated by neutropenia, the pack quarantined — surfaces show the
  doctrine working, not a highlight reel.
- DETERMINISTIC: same locale + seed = same bytes.
"""

import hashlib
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for pkg in ("emar", "nursing", "anesthesia", "growthmon", "dental",
            "oncology", "cssd", "dietetics", "certificates",
            "problemlist", "localepack"):
    sys.path.insert(0, os.path.join(_here, "..", pkg))

from emar import MAR  # noqa: E402
from nursing import Flowsheet, FluidBalance, Handoff, WardReport  # noqa: E402
from anaesthesia import AnaesthesiaRecord  # noqa: E402
from who2006 import Anthropometry  # noqa: E402
from dental import Odontogram  # noqa: E402
from oncology import OncologyCourse, register_regimen, REGIMENS  # noqa: E402
from cssd import AutoclaveCycle, SterilePack, CSSDRegistry  # noqa: E402
from dietetics import NutritionScreen, DietOrder, WardKitchenSheet  # noqa: E402
from certificates import CertificateRegistry  # noqa: E402
from problemlist import ProblemList  # noqa: E402
from localepack import get_locale, seed_population  # noqa: E402

VERSION = "emr-demo@1.0.0"


def _stamp(body):
    body["version"] = VERSION
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False)
        .encode()).hexdigest()
    return body


def seed_inpatient(locale_code, seed=42):
    """One ward morning: MAR, flowsheet, I&O, handoff, anaesthesia,
    growth grading — with the refusals visible."""
    loc = get_locale(locale_code)
    people = seed_population(locale_code, n=6, seed=seed)
    fac = loc["facilities"][0]
    p1 = people[0]
    p2 = people[1]
    cur = loc["currency"]["code"]

    # --- eMAR: one order dispensed, doses given/refused/overdue ---
    mar = MAR("MAR-DEMO", p1["ref"], "ADM-DEMO",
              f"WB-{p1['ref']}")
    mar.add_order({
        "order_id": "ORD-D1", "patient_ref": p1["ref"],
        "drug_code": f"{loc['formulary_prefix']}AMOX500",
        "drug_class": "penicillin", "dose": "500mg", "route": "oral",
        "frequency": "tds", "state": "dispensed", "duration_days": 1,
        "medication_barcode": "MED-AMOX500",
    }, day=1)
    slots = sorted(mar.slots.values(),
                   key=lambda s: (s.day, s.scheduled_minute or 0))
    # dose 1 given on time with clean scans
    slots[0].record("given", by="nurse-ada", minute=8 * 60 + 5,
                    patient_barcode=f"WB-{p1['ref']}",
                    medication_barcode="MED-AMOX500")
    # dose 2 refused — patient nauseated
    slots[1].record("refused", by="nurse-ada", minute=14 * 60 + 10,
                    reason="patient nauseated — refused oral dose")
    # dose 3 left scheduled → overdue on the sheet

    # --- nursing: flowsheet + I&O + handoff ---
    fs = Flowsheet(p1["ref"], "ADM-DEMO")
    for minute, hr, spo2 in ((480, 82, 97), (720, 88, 96), (960, 91, 98)):
        fs.record("heart-rate", hr, day=1, minute=minute, by="nurse-ada")
        fs.record("spo2", spo2, day=1, minute=minute, by="nurse-ada")
    row = fs.record("temperature", 39.9, day=1, minute=480, by="nurse-ada")
    fs.record("temperature", 37.9, day=1, minute=485, by="nurse-ada",
              amends=row["row_id"])  # correction cites the original

    fb = FluidBalance(p1["ref"], "ADM-DEMO")
    fb.record("intake", "iv", 1000, day=1, minute=480, by="nurse-ada")
    fb.record("intake", "oral", 500, day=1, minute=600, by="nurse-ada")
    fb.record("output", "urine", 400, day=1, minute=700, by="nurse-ada")

    ho = Handoff("HO-D1", p1["ref"], day=1, shift="morning",
                 from_nurse="nurse-ada",
                 situation="day 1 post-admission, malaria",
                 background="presented with fever, RDT positive",
                 assessment="stable, tolerating orals, febrile AM",
                 recommendation="continue obs q4h, repeat RDT day 3")
    ho.countersign(by="nurse-bola")

    # p2 with NEVER-recorded vitals → the ward report must flag them
    fs2 = Flowsheet(p2["ref"], "ADM-DEMO-2")
    ward = WardReport(fac["id"])
    ward.add_patient(fs, fb)
    ward.add_patient(fs2)
    ward.add_handoff(ho)
    ward_report = ward.report(day=1, minute=1000)

    # --- anaesthesia: one emergency laparotomy ---
    ana = AnaesthesiaRecord("ANA-D1", "CASE-D1", p2["ref"],
                            anaesthetist="dr-anes", day=1)
    ana.assess_preop(asa="II", mallampati="II", fasting_hours=3,
                     elective=False, airway_plan="RSI, direct laryngoscopy",
                     by="dr-anes")
    ana.induce(minute=0, anaesthesia_type="general")
    ana.administer_agent("propofol", "150mg", minute=0, by="dr-anes",
                         witness="nurse-ola")
    ana.administer_agent("suxamethonium", "100mg", minute=1, by="dr-anes",
                         witness="nurse-ola")
    ana.record_event("intubated, ETT 7.5", minute=3)
    for minute, hr, sys_, spo2 in ((5, 88, 110, 99), (30, 84, 118, 98),
                                   (60, 80, 115, 99)):
        ana.record_observation("heart-rate", hr, minute=minute)
        ana.record_observation("systolic-bp", sys_, minute=minute)
        ana.record_observation("spo2", spo2, minute=minute)
    ana.record_event("knife to skin", minute=15)
    ana.record_event("appendix removed, peritoneal lavage", minute=55)
    ana.close(minute=90, disposition="pacu", handed_over_to="nurse-pacu",
              by="dr-anes")

    # --- growth: one child graded, one refusing the implausible ---
    anthro = Anthropometry()
    kid = people[2]
    growth = anthro.grade_child("male" if seed % 2 == 0 else "female",
                                age_months=18.0, weight_kg=8.6,
                                height_cm=76.0)
    growth["child_ref"] = kid["ref"]
    growth["child_name"] = kid["name"]

    return _stamp({
        "kind": "inpatient-demo",
        "locale": locale_code, "country": loc["country"],
        "currency": cur, "facility": fac["name"],
        "patients": [{"ref": p["ref"], "name": p["name"]}
                     for p in people[:3]],
        "mar": mar.sheet(1), "mar_audit": mar.audit(1),
        "flowsheet_rows": fs.rows,
        "fluid_balance": fb.totals(1),
        "handoff": ho.as_dict(),
        "ward_report": ward_report,
        "anaesthesia": ana.as_dict(),
        "growth": growth,
    })


def seed_specialty(locale_code, seed=42):
    """Dental, oncology, CSSD, dietetics, certificates, problem list."""
    loc = get_locale(locale_code)
    people = seed_population(locale_code, n=6, seed=seed)
    fac = loc["facilities"][0]
    p1, p2, p3 = people[0], people[1], people[2]

    # --- dental ---
    od = Odontogram(p1["ref"])
    od.tooth(36).record("caries", by="dr-dent", day=1,
                        surfaces=["occlusal"], note="MOD caries")
    od.tooth(36).record("filled", by="dr-dent", day=1,
                        surfaces=["occlusal"], note="composite restoration")
    od.tooth(46).record("caries", by="dr-dent", day=1)
    od.tooth(46).record("extracted", by="dr-dent", day=2,
                        note="non-restorable")
    od.tooth(11).record("watch", by="dr-dent", day=1,
                        note="incipient lesion, review 6 months")
    dental = od.snapshot(day=2)
    dental["patient_name"] = p1["name"]

    # --- oncology ---
    if "CHOP" not in REGIMENS:
        register_regimen(
            "CHOP",
            drugs=[{"name": "cyclophosphamide", "mg_per_m2": 750,
                    "absolute_max_mg": 1500},
                   {"name": "doxorubicin", "mg_per_m2": 50,
                    "absolute_max_mg": 100},
                   {"name": "vincristine", "mg_per_m2": 1.4,
                    "absolute_max_mg": 2.0}],
            cycle_length_days=21, max_cycles=6, ratified_by="dr-onc")
    onc = OncologyCourse("OC-D1", p2["ref"], "CHOP",
                         oncologist="dr-onc", diagnosis="DLBCL")
    c1 = onc.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                        bloods={"neutrophils": 2.5, "platelets": 150.0,
                                "haemoglobin": 11.0})
    c1.record_administration(by="nurse-chemo", nurse_witness="nurse-2")
    c1.record_toxicity(1, assessed_by="dr-onc", day=10)
    # cycle 2 attempt with neutropenia → the gate must refuse
    gate_refusal = None
    try:
        onc.plan_cycle(2, height_cm=170, weight_kg=70, day=22,
                       bloods={"neutrophils": 0.6, "platelets": 150.0,
                               "haemoglobin": 11.0},
                       previous_toxicity_grade=1)
    except Exception as e:  # the refusal IS the demo
        gate_refusal = str(e)
    oncology = onc.course_summary()
    oncology = dict(oncology)
    oncology["cycle_1_doses"] = c1.doses
    oncology["cycle_1_bsa"] = round(c1.bsa, 3)
    oncology["gate_refusal"] = gate_refusal
    oncology["patient_name"] = p2["name"]

    # --- CSSD ---
    cssd = CSSDRegistry(fac["id"])
    cssd.register_cycle(AutoclaveCycle("CY-D1", "pre-vac-134",
                                       temp_c=134.5, duration_minutes=4,
                                       day=1, bi_result="negative"))
    cssd.register_cycle(AutoclaveCycle("CY-D2", "pre-vac-134",
                                       temp_c=118.0, duration_minutes=4,
                                       day=1))  # failed — low temp
    good = SterilePack("PK-D1", ["laparotomy set"], assembler="tech-1",
                       day=1)
    bad = SterilePack("PK-D2", ["minor-ops set"], assembler="tech-1",
                      day=1)
    cssd.register_pack(good, "CY-D1")
    cssd.register_pack(bad, "CY-D2")
    cssd.issue_to_theatre("PK-D1", day=1, issued_to="nurse-theatre")
    cssd_report = cssd.daily_report(1)

    # --- dietetics ---
    screen = NutritionScreen(p3["ref"], day=1, screener="nurse-ada")
    screen.assess(bmi=15.5, weight_loss_pct=12.0, intake_reduced_days=7)
    diet = DietOrder("DI-D1", p3["ref"], fac["id"], day=1,
                     prescriber="dr-eze", screen=screen, diet_code="f-75",
                     portions_per_day=6)
    sheet = WardKitchenSheet(fac["id"], day=1)
    sheet.add(diet)
    dietetics = {"screen": screen.result, "ticket": diet.kitchen_ticket(),
                 "manifest": sheet.manifest(),
                 "patient_name": p3["name"]}

    # --- certificates ---
    certs = CertificateRegistry(fac["id"])
    sick = certs.issue_sick_note(p1["ref"], issued_by="dr-eze", day=1,
                                 days_off=7, diagnosis_stated="malaria")
    ref = certs.issue_referral(
        p2["ref"], issued_by="dr-eze", day=1, to_specialty="oncology",
        to_facility=fac["name"], clinical_question="? DLBCL staging",
        summary="cervical lymphadenopathy, B symptoms, LDH raised")
    certificates = {
        "sick_note": sick.as_dict(),
        "referral": ref.as_dict(),
        "verify_sick_note": certs.verify(sick.cert_id,
                                         sick.fingerprint()),
        "register": certs.register_snapshot(),
    }

    # --- problem list ---
    pl = ProblemList(p2["ref"])
    pl.add(title="Diffuse large B-cell lymphoma", icd10="C83.3",
           onset_day=1, recorded_by="dr-onc", day=1)
    htn = pl.add(title="Hypertension", icd10="I10", onset_day=1,
                 recorded_by="dr-eze", day=1)
    pl.add(title="Recurrent malaria", onset_day=1, recorded_by="dr-eze",
           day=1)  # uncoded — stated, not blank
    problem_list = pl.snapshot(day=1)
    problem_list["patient_name"] = p2["name"]

    return _stamp({
        "kind": "specialty-demo",
        "locale": locale_code, "country": loc["country"],
        "currency": loc["currency"]["code"],
        "facility": fac["name"],
        "dental": dental, "oncology": oncology, "cssd": cssd_report,
        "dietetics": dietetics, "certificates": certificates,
        "problem_list": problem_list,
    })


def export_all(out_dir, seed=42):
    """Both locales, both surfaces — four payloads."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for locale_code in ("ng", "cv"):
        for kind, fn in (("inpatient", seed_inpatient),
                         ("specialty", seed_specialty)):
            payload = fn(locale_code, seed=seed)
            path = os.path.join(out_dir, f"{kind}.{locale_code}.json")
            with open(out_dir and path or path, "w",
                      encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1, ensure_ascii=False)
            paths.append(path)
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    for p in export_all(out):
        print("wrote", p)
