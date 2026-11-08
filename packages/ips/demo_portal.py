"""portal demo — deterministic "My Record" payload for the demo site.

Builds a synthetic IPS bundle, derives the patient-facing portal payload
through portal.portal_payload (the same generator that feeds the live
patient app), and writes record.json. Synthetic means synthetic:
Pax-SIM, Sim-Clinic, sha256-of-nothing-real.
"""

from __future__ import annotations

from ips import build_ips
from portal import export_portal, portal_payload


def seed_record_payload():
    bundle = build_ips(
        {"id": "Pax-SIM-0001", "name": "Adaeze O (Sim)",
         "birthDate": "1990-03-14", "gender": "female", "country": "ng"},
        sections={
            "medications": {"entries": [
                {"display": "artemether-lumefantrine 80/480 bd x 3 days"},
                {"display": "ferrous sulfate 200mg daily"}]},
            "allergies": {"entries": [
                {"display": "sulfonamides — rash"}]},
            "problems": {"entries": [
                {"display": "malaria, uncomplicated (resolved)"},
                {"display": "iron-deficiency anaemia (on treatment)"}]},
            "immunizations": {"entries": [
                {"display": "COVID-19 (2 doses)"},
                {"display": "tetanus toxoid (antenatal schedule)"}]},
            "vitals": {"entries": [
                {"display": "BP 122/78 · Temp 37.1C · Wt 62kg"}]},
            "pregnancy": {"empty_reason": "withheld-by-consent"},
            "results": {"entries": [
                {"display": "RDT malaria Pf — positive (treated)"},
                {"display": "Hb 10.4 g/dL"}]},
        },
        author="Sim Clinic Ikeja", generated_day=1)
    return portal_payload(
        bundle,
        visits=[{"day": 1, "facility": "Sim Clinic Ikeja",
                 "summary": "Malaria treated; iron started",
                 "note_sha256": hash_demo_note()}],
        share_link=None)   # no live share endpoint in the demo — stated


def hash_demo_note():
    import hashlib
    return hashlib.sha256(b"NOTE-SIM-001").hexdigest()


def export_record(out_path):
    return export_portal(seed_record_payload(), out_path)
