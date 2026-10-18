#!/usr/bin/env bash
# Hospital operations wave (ADR-0024): ADT + beds, theatre list with WHO
# checklist, lab with critical-result acknowledgement, billing with
# exemptions and insurance split.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== hospital operations smoke =="
PYTHONPATH=packages/inpatient:packages/theatre:packages/lis:packages/billing python3 - <<'PY'
from inpatient import Ward
from theatre import Theatre, CHECKLIST
from lis import Lab
from billing import BillingOffice

# 1. ADT: admit, escalate to ICU, discharge; beds never drift
w = Ward("FAC-1")
for b, ward, kind in [("B1","medical","general"),("ICU-1","icu","icu")]:
    w.add_bed(b, ward, kind)
ep = w.admit("PAT-1", "B1", "nurse-7", "via emergency — dispatch INC-00001")
w.transfer(ep.id, "ICU-1", "nurse-7", "deteriorating")
assert w.beds["B1"].state == "cleaning" and w.beds["ICU-1"].state == "occupied"
w.discharge(ep.id, "doctor-3", note="stabilised")
assert w.occupancy()["occupied"] == 0
print(f"1. ADT: {ep.id} admitted, ICU transfer, discharged — beds balanced")

# 2. Theatre: emergency bumps electives, WHO gates enforced
th = Theatre("TH-1")
th.book("2026-10-20", "P-e", "hernia", "elective", "dr-a", "dr-b")
em = th.book("2026-10-20", "P-e2", "ruptured ectopic", "emergency", "dr-a", "dr-b")
assert th.running_order("2026-10-20")[0] == em.id
for phase in ("sign_in", "time_out", "sign_out"):
    for item in CHECKLIST[phase]:
        th.check(em.id, phase, item, "nurse-1")
th.start(em.id, "nurse-1")
th.complete(em.id, "nurse-1", "patient stable")
print(f"2. theatre: {em.id} ran first (emergency bump), WHO checklist gated")

# 3. Lab: STAT worklist, critical result acknowledged by clinician
lab = Lab("FAC-1")
o = lab.order("PAT-1", "potassium_mmol", "dr-a", priority="stat")
lab.collect(o.id, "phleb-1"); lab.start_analysis(o.id)
lab.result(o.id, 7.8, "mmol/L", entered_by="scientist-1")
lab.verify(o.id, "scientist-2")
assert o.critical and o.id in lab.unacknowledged_criticals()
lab.acknowledge_critical(o.id, "dr-a")
print(f"3. lab: {o.id} critical K+ 7.8 — verified (four-eyes), acknowledged")

# 4. Billing: under-5 exemption, insurance split, integer kobo
bo = BillingOffice("FAC-1")
b = bo.open_bill("PAT-CHILD", insurance_pct=90)
b.add_line("CONSULT", "consultation", 200_000)
b.add_line("LAB-FBC", "fbc", 350_000)
b.apply_exemption("under5", "mo-1")
assert b.patient_due_kobo() == 0 and b.state == "open"
b2 = bo.open_bill("PAT-ADULT", insurance_pct=90)
b2.add_line("CONSULT", "consultation", 200_000)
assert b2.patient_due_kobo() == 20_000 and b2.insurer_due_kobo() == 180_000
b2.pay(20_000, "cash", "cashier-1")
assert b2.state == "settled"
print("4. billing: under-5 exempted to ₦0; 90% NHIA split; adult settled at ₦200")

print()
print("HOSPITAL OPERATIONS SMOKE: PASS")
PY
