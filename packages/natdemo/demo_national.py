"""demo_national.py — deterministic national-rails demo seeder.

One national day, two countries: the seven legacy-supersession rails
(eprescribe, sharedsum, triageline, panelcare, observatory, healthcred,
teleconsulta) exercised against the NG and CV locale entries. Every
gate fires on purpose — a refused redemption, a restricted summary,
a breached callback promise, a suppressed small cell, a revoked
credential, a boat-borne consult.

Same seed, same payload, byte for byte. The checksum is the contract.
"""

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _pkg in ("eprescribe", "sharedsum", "triageline", "panelcare",
             "observatory", "healthcred", "teleconsulta", "localepack",
             "qrtrust"):
    sys.path.insert(0, os.path.join(_HERE, "..", _pkg))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

from eprescribe import PrescriptionRail  # noqa: E402
from sharedsum import SharedSummaryService, SummaryContribution  # noqa: E402
from triageline import TriageLine  # noqa: E402
from panelcare import Panel  # noqa: E402
from observatory import Observatory  # noqa: E402
from healthcred import CredentialWallet, Verifier  # noqa: E402
from teleconsulta import TeleconsultaNetwork  # noqa: E402
from localepack import get_locale  # noqa: E402

VERSION = "natdemo@1.0.0"

DEMO_SALT = "natdemo-salt-2026"


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _names(locale):
    """First patient/clinician names from the locale entry pools."""
    pools = locale["name_pools"]
    given = pools.get("given", ["Ada"])
    family = pools.get("family", ["Okafor"])
    return f"{given[0]} {family[0]}", f"Dr {family[-1]}"


def seed_national(locale_code, seed=42):
    """Seed one national day for a locale. Unknown locales refuse in
    localepack; this function never invents a country."""
    locale = get_locale(locale_code)
    fac_main = locale["facilities"][0]["id"]
    fac_hub = locale["facilities"][1]["id"] \
        if len(locale["facilities"]) > 1 else fac_main
    payer = locale["payer"]["name"]
    patient, doctor = _names(locale)

    # --- eprescribe: issue, partial redeem, one never collected ------
    rail = PrescriptionRail()
    rail.register_pharmacy(f"PH-{locale_code.upper()}-1",
                           f"Farmácia Central" if locale_code == "cv"
                           else "Central Pharmacy", day=1)
    rail.register_payer(payer, payer)
    prefix = locale["formulary_prefix"]
    rail.issue("RX-D1", "p-1", doctor, fac_main, 1,
               [{"drug_code": f"{prefix}AMOX500",
                 "dose_text": "500mg tds 7d", "quantity": 21}],
               payer=payer)
    rail.dispense("RX-D1", f"PH-{locale_code.upper()}-1", 0, 10,
                  by="pharm-1", day=2)
    rail.issue("RX-D2", "p-2", doctor, fac_main, 1,
               [{"drug_code": f"{prefix}PARA500", "dose_text": "1g prn",
                 "quantity": 20}], payer=payer)
    eprescribe = {
        "issued": 2,
        "partially_dispensed": rail.prescriptions["RX-D1"].state,
        "token": rail.prescriptions["RX-D1"].token,
        "dispensed_qty": 10,
        "remaining_qty": rail.prescriptions["RX-D1"].remaining(0),
        "never_collected": rail.never_collected(5),
        "payer": payer,
        "audit_sha256": rail.audit("RX-D1")["sha256"],
    }

    # --- sharedsum: contributions, a read, a break-glass, a refusal --
    svc = SharedSummaryService()
    svc.contribute(SummaryContribution(
        "p-1", fac_main, "problems", 10,
        [{"code": "B54", "display": "Malaria"}], contributed_by=doctor))
    svc.contribute(SummaryContribution(
        "p-1", fac_hub, "allergies", 12,
        [{"substance": "artemisinin", "reaction": "urticaria"}],
        contributed_by=doctor))
    read_ok = svc.read("p-1", accessor=f"dr-hub", facility=fac_hub,
                       day=20, context="care-relationship")
    svc.read("p-1", accessor="dr-ed", facility=fac_hub, day=21,
             context="break-glass", reason="unconscious in ED")
    svc.restrict("p-9", by="p-9", day=15)
    svc.contribute(SummaryContribution(
        "p-9", fac_main, "problems", 14,
        [{"code": "F32", "display": "Depression"}],
        contributed_by=doctor))
    try:
        svc.read("p-9", accessor="dr-hub", facility=fac_hub, day=20,
                 context="care-relationship")
        refused = "NOT REFUSED — BUG"
    except Exception as exc:
        refused = str(exc)
    sharedsum = {
        "sections_read": sorted(read_ok["sections"].keys()),
        "read_checksum": read_ok["sha256"],
        "break_glass_count": len(svc.break_glass_report(21)),
        "restricted_refusal": refused,
        "coverage": svc.coverage(),
    }

    # --- triageline: dispatch met, callback kept, callback breached --
    line = TriageLine()
    line.open_call("C-1", "caller-1", handler="nurse-1", minute=540,
                   day=1, stream="chest-pain")
    line.assess("C-1", "crushing-with-breathlessness", by="nurse-1",
                day=1, minute=543)
    line.complete("C-1", by="nurse-1", day=1, minute=547,
                  handed_to=f"ambulance-dispatch-{locale_code}")
    line.open_call("C-2", "caller-2", handler="nurse-1", minute=600,
                   day=1, stream="child-diarrhoea")
    line.assess("C-2", "poor-intake", by="nurse-1", day=1, minute=605)
    line.complete("C-2", by="nurse-1", day=1, minute=610)
    line.complete_callback("C-2", by="nurse-3", day=1, minute=700,
                           outcome="reached, ORS advised")
    line.open_call("C-3", "caller-3", handler="nurse-2", minute=620,
                   day=1, stream="child-diarrhoea")
    line.assess("C-3", "poor-intake", by="nurse-2", day=1, minute=625)
    line.complete("C-3", by="nurse-2", day=1, minute=630)
    line.complete_callback("C-3", by="nurse-4", day=1, minute=900,
                           outcome="reached late")
    sla = line.sla_report(1, 1000)
    triageline = {
        "calls": 3, "dispatch_met": True,
        "callbacks_kept": sla["kept"],
        "callbacks_breached": sla["breached"],
        "report_sha256": sla["sha256"],
    }

    # --- panelcare: overdue recall, prevention due-list --------------
    panel = Panel(doctor, fac_main, cap=1800)
    panel.assign("p-1", day=0, sex="female", birth_day=-10000,
                 conditions=["hypertension"])
    panel.assign("p-2", day=0, sex="male", birth_day=-500,
                 conditions=["hiv-art"])
    panel.record_review("p-1", "hypertension", day=10, by=doctor)
    overdue = panel.overdue_recalls(100)
    due = panel.prevention_due("p-2", 0)
    panelcare = {
        "panel_size": 2, "cap": 1800,
        "overdue_recalls": overdue,
        "prevention_due_child": due,
        "kpis_sha256": panel.kpis(100)["sha256"],
    }

    # --- observatory: rollup + one suppressed small cell -------------
    obs = Observatory()
    cc = locale_code.upper()
    obs.register_region(cc, locale["country"])
    region_name = locale["facilities"][1]["state"] \
        if len(locale["facilities"]) > 1 else locale["country"]
    obs.register_region(f"{cc}-R1", region_name, parent=cc)
    obs.registry.propose("malaria-incidence", {
        "event_kind": "case", "numerator": ["B50", "B54"],
        "title": "Malaria cases", "version": "mi@1"}, by="epi-1", day=1)
    obs.registry.ratify("malaria-incidence", by="epi-2", day=1)
    obs.record_event("case", region=cc, day=5, condition="B54",
                     count=87, source=fac_main)
    obs.record_event("case", region=f"{cc}-R1", day=6, condition="B54",
                     count=3, source=fac_hub)
    national = obs.compute("malaria-incidence", region=cc,
                           period_start=1, period_end=10)
    small = obs.compute("malaria-incidence", region=f"{cc}-R1",
                        period_start=1, period_end=10)
    observatory = {
        "national_value": national["value"],
        "national_checksum": national["events_sha256"],
        "small_cell": {"suppressed": small["suppressed"],
                       "suppression": small["suppression"]},
    }

    # --- healthcred: one verified offline, one revoked ---------------
    ring = {"demo-k1": b"natdemo-key-material-not-production"}
    wallet = CredentialWallet(ring, "demo-k1", issuer=fac_main,
                              salt=DEMO_SALT)
    cred_ok = wallet.mint("S-0001", "p-1", "vaccination",
                          "yellow-fever dose 1 complete",
                          valid_until=3650, issued_day=10, by=fac_main)
    cred_rev = wallet.mint("S-0002", "p-3", "test-result",
                           "covid-19 antigen negative",
                           valid_until=40, issued_day=10, by=fac_main)
    verifier = Verifier(ring, revoked={cred_rev["tag"].rsplit(".", 1)[0]},
                        salt=DEMO_SALT)
    v_ok = verifier.verify(cred_ok["tag"], verifier="border-post",
                           day=100, subject_ref="p-1")
    try:
        verifier.verify(cred_rev["tag"], verifier="border-post", day=20)
        revoked_msg = "NOT REFUSED — BUG"
    except Exception as exc:
        revoked_msg = str(exc)
    healthcred = {
        "verified_serial": v_ok["serial"],
        "subject_checked": v_ok["subject_checked"],
        "revoked_refusal": revoked_msg,
        "audit": verifier.audit(),
    }

    # --- teleconsulta: boat bundle, SLA met, emergency escalation ----
    net = TeleconsultaNetwork()
    net.register_facility(f"{cc}-PERIPH", locale["facilities"][0]["name"],
                          kind="peripheral")
    net.register_facility(f"{cc}-HUB", locale["facilities"][1]["name"]
                          if len(locale["facilities"]) > 1 else
                          locale["facilities"][0]["name"],
                          kind="hub",
                          specialties=["dermatology", "cardiology"])
    net.create("TC-1", "p-4", f"{cc}-PERIPH", "dermatology",
               priority="routine", by="nurse-1", day=1, minute=480,
               history="chronic ulcer 6 weeks")
    b = net.bundle("TC-1", by="nurse-1", day=1, minute=490)
    net.transmit("TC-1", day=3, minute=60, medium="boat")
    net.receive("TC-1", f"{cc}-HUB", bundle_sha256=b["sha256"],
                day=3, minute=120)
    net.answer("TC-1", specialist="dr-hub", day=3, minute=200,
               assessment="venous ulcer",
               plan="compression bandaging, review 2 weeks")
    net.create("TC-2", "p-5", f"{cc}-PERIPH", "cardiology",
               priority="emergency", by="nurse-1", day=4, minute=300,
               history="crushing chest pain, diaphoretic")
    net.bundle("TC-2", by="nurse-1", day=4, minute=305)
    net.transmit("TC-2", day=4, minute=310, medium="sync")
    net.escalate("TC-2", by="nurse-1", day=4, minute=315,
                 channel="medevac",
                 reason="STEMI suspected — beyond island capability")
    sla_net = net.sla_report()
    teleconsulta = {
        "boat_medium": True,
        "tc1_sla_met": net.consults["TC-1"]["response"]["sla_met"],
        "tc2_escalated_to": "medevac",
        "report_sha256": sla_net["sha256"],
    }

    payload = {
        "locale": locale_code,
        "country": locale["country"],
        "currency": locale["currency"]["code"],
        "payer": payer,
        "facilities": [f["name"] for f in locale["facilities"][:2]],
        "patient": patient, "doctor": doctor,
        "eprescribe": eprescribe, "sharedsum": sharedsum,
        "triageline": triageline, "panelcare": panelcare,
        "observatory": observatory, "healthcred": healthcred,
        "teleconsulta": teleconsulta,
        "version": VERSION, "seed": seed,
    }
    payload["sha256"] = _sha({k: v for k, v in payload.items()
                              if k != "sha256"})
    return payload


def export_all(out_dir):
    for code in ("ng", "cv"):
        payload = seed_national(code)
        path = os.path.join(out_dir, f"national.{code}.json")
        payloadio.write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return True


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _HERE, "..", "..", "apps", "pwa")
    export_all(out)
    print(f"national payloads written to {out}")
