#!/usr/bin/env bash
# Emergency dispatch + case management + CRM smoke (ADR-0019) —
# in-memory, no Postgres:
#
#   registry-service (facility directory with /facilities/near)
#   dispatch-service (SOS intake, WhatsApp webhook, triage, assignment,
#     case management, CRM)
#
# Asserts: a WhatsApp SOS with a location pin triages red and is routed
# to the nearest emergency-capable facility; the caller becomes a CRM
# contact with an ordered interaction log; red triage auto-opens a case
# with a 30-minute arrival-check task; a repeat SOS inside the dedupe
# window updates instead of cloning; the incident lifecycle drives the
# case to follow-up with a 48-hour welfare task; revoking consent
# blocks follow-up; the overdue list exposes SLA breaches; outcomes are
# coded on resolution.
#
# Run from the repo root: bash demo/dispatch.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/registry-service && go build -o "$TMP/registry" . )
( cd services/dispatch-service && go build -o "$TMP/dispatch" . )

LISTEN=:18191 "$TMP/registry" >"$TMP/registry.log" 2>&1 &
LISTEN=:18192 REGISTRY_URL=http://127.0.0.1:18191 "$TMP/dispatch" >"$TMP/dispatch.log" 2>&1 &
sleep 1.5
curl -sf -m 3 "http://127.0.0.1:18191/healthz" >/dev/null || { echo "registry failed:"; cat "$TMP/registry.log"; exit 1; }
curl -sf -m 3 "http://127.0.0.1:18192/healthz" >/dev/null || { echo "dispatch failed:"; cat "$TMP/dispatch.log"; exit 1; }
REG=http://127.0.0.1:18191
DIS=http://127.0.0.1:18192

echo "== 1. seed facilities: a PHC next door, an emergency hospital 4km away =="
curl -sf -X POST "$REG/facilities" -H 'Content-Type: application/json' -d '{
  "id":"PHC-NEAR","name":"Tudun Wada PHC","state":"Kano","lga":"Nassarawa",
  "ownership":"public","level":"PHC","services":["anc","immunisation"],
  "amenities":["pharmacy"],"latitude":6.5300,"longitude":3.3850,"hours":"Mon-Fri 08:00-16:00"}' >/dev/null
curl -sf -X POST "$REG/facilities" -H 'Content-Type: application/json' -d '{
  "id":"HOSP-EMG","name":"General Hospital Lagos","state":"Lagos","lga":"Ikeja",
  "ownership":"public","level":"hospital","services":["emergency","surgery"],
  "amenities":["emergency","ambulance","oxygen"],"latitude":6.5244,"longitude":3.3600,"hours":"24/7"}' >/dev/null
echo "   seeded PHC-NEAR (no emergency amenity) and HOSP-EMG (24/7 emergency)"

echo "== 2. WhatsApp SOS with a location pin — triage + auto-assignment =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
body = json.dumps({"entry":[{"changes":[{"value":{"messages":[
  {"from":"+2348030001111","type":"text","text":{"body":"my mother collapsed and is unconscious"}},
  {"from":"+2348030001111","type":"location","location":{"latitude":6.5244,"longitude":3.3792}}
]}}]}]}).encode()
req = urllib.request.Request(DIS+"/webhooks/whatsapp", data=body, headers={"Content-Type":"application/json"})
ack = json.load(urllib.request.urlopen(req))
assert ack["received"] and ack["incidents_created"] == 1, ack

inc = json.load(urllib.request.urlopen(DIS+"/incidents"))["incidents"]
assert len(inc) == 1, inc
i = inc[0]
assert i["triage"] == "red", i
assert i["loc_source"] == "whatsapp-pin", i
assert i["status"] == "dispatched", i
assert i["facility_id"] == "HOSP-EMG", i  # emergency amenity beats closer PHC
print(f"   incident {i['id']} red, dispatched to {i['facility_name']} ({i['facility_km']:.1f}km)")
PY

echo "== 3. CRM: the caller is a contact with an ordered interaction log =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
view = json.load(urllib.request.urlopen(DIS+"/contacts/+2348030001111"))
c = view["contact"]
assert c["preferred_channel"] == "whatsapp", c
assert c["consent_follow_up"] is True, c
assert view["interactions"] and view["interactions"][0]["kind"] == "sos", view
assert len(view["cases"]) == 1, view
print(f"   contact {c['phone']} — {c['interactions']} interaction(s), case {view['cases'][0]['id']} attached")
PY

echo "== 4. red triage auto-opened a case with a 30-minute arrival-check task =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
cases = json.load(urllib.request.urlopen(DIS+"/cases"))["cases"]
assert len(cases) == 1 and cases[0]["priority"] == "red" and cases[0]["status"] == "open", cases
detail = json.load(urllib.request.urlopen(DIS+"/cases/"+cases[0]["id"]))
tasks = detail["tasks"]
assert len(tasks) == 1 and tasks[0]["kind"] == "arrival_check" and tasks[0]["status"] == "open", tasks
print(f"   {cases[0]['id']} owner={cases[0]['owner']} task={tasks[0]['id']} due={tasks[0]['due_at']}")
PY

echo "== 5. repeat SOS inside 10 minutes dedupes, never clones =="
CODE=$(curl -s -o "$TMP/repeat.json" -w '%{http_code}' -X POST "$DIS/sos" \
  -H 'Content-Type: application/json' \
  -d '{"phone":"+2348030001111","channel":"app","complaint":"still unconscious, breathing shallow"}')
[ "$CODE" = "200" ] || { echo "   expected 200 dedupe, got $CODE"; exit 1; }
python3 - "$DIS" "$TMP/repeat.json" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
again = json.load(open(sys.argv[2]))
inc = json.load(urllib.request.urlopen(DIS+"/incidents"))["incidents"]
assert len(inc) == 1 and inc[0]["id"] == again["id"], (inc, again)
assert again["complaint"].startswith("still unconscious"), again
assert len(json.load(urllib.request.urlopen(DIS+"/cases"))["cases"]) == 1
print(f"   repeat SOS folded into {again['id']} — one incident, one case")
PY

echo "== 6. lifecycle drives the case: ack → on scene → transported → closed =="
INC=$(python3 -c "import json,urllib.request,sys;print(json.load(urllib.request.urlopen('$DIS/incidents'))['incidents'][0]['id'])")
for TO in acknowledged on_scene transported closed; do
  curl -sf -X POST "$DIS/incidents/$INC/transition" -H 'Content-Type: application/json' \
    -d "{\"to\":\"$TO\",\"actor\":\"dispatcher-1\"}" >/dev/null
done
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
case = json.load(urllib.request.urlopen(DIS+"/cases"))["cases"][0]
assert case["status"] == "follow_up", case
tasks = json.load(urllib.request.urlopen(DIS+"/cases/"+case["id"]))["tasks"]
fu = [t for t in tasks if t["kind"] == "followup_48h"]
assert len(fu) == 1 and fu[0]["status"] == "open", tasks
print(f"   case -> follow_up; 48h welfare task {fu[0]['id']} due {fu[0]['due_at']}")
PY

echo "== 7. overdue endpoint exposes SLA breaches =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
case = json.load(urllib.request.urlopen(DIS+"/cases"))["cases"][0]
# a promised callback that is already past due must surface immediately
req = urllib.request.Request(DIS+"/cases/"+case["id"]+"/tasks",
    data=json.dumps({"kind":"callback","due_at":"2020-01-01T00:00:00Z",
                     "assignee":"dispatcher-1","note":"promised the family an update"}).encode(),
    headers={"Content-Type":"application/json"})
json.load(urllib.request.urlopen(req))
over = json.load(urllib.request.urlopen(DIS+"/tasks/overdue"))["overdue"]
assert any(t["kind"] == "callback" for t in over), over
print(f"   overdue: {[t['id'] + '/' + t['kind'] for t in over]}")
PY

echo "== 8. arrival confirmed; case resolved with a coded outcome =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
case = json.load(urllib.request.urlopen(DIS+"/cases"))["cases"][0]
tasks = json.load(urllib.request.urlopen(DIS+"/cases/"+case["id"]))["tasks"]
arrival = [t for t in tasks if t["kind"] == "arrival_check"][0]
def post(path, payload):
    req = urllib.request.Request(DIS+path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req))
post("/tasks/"+arrival["id"]+"/done", {"note":"HOSP-EMG confirmed patient received"})
post("/cases/"+case["id"]+"/transition", {"to":"resolved","actor":"dispatcher-1",
                                          "outcome":"resolved_care_received"})
done_case = json.load(urllib.request.urlopen(DIS+"/cases/"+case["id"]))["case"]
assert done_case["outcome"] == "resolved_care_received", done_case
over = json.load(urllib.request.urlopen(DIS+"/tasks/overdue"))["overdue"]
assert not any(t["id"] == arrival["id"] for t in over), over
print(f"   {case['id']} resolved_care_received; arrival task cleared from overdue list")
PY

echo "== 9. revoked consent blocks the follow-up task =="
python3 - "$DIS" <<'PY'
import json, sys, urllib.request
DIS = sys.argv[1]
def post(path, payload):
    req = urllib.request.Request(DIS+path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req))
post("/sos", {"phone":"+2348030002222","channel":"app","complaint":"stroke symptoms, cannot move one side"})
post("/contacts/+2348030002222/consent", {"consent_follow_up":False,"actor":"caller"})
inc = [i for i in json.load(urllib.request.urlopen(DIS+"/incidents"))["incidents"]
       if i["caller_phone"] == "+2348030002222"][0]
for to in ["dispatched","acknowledged","on_scene","transported","closed"]:
    post(f"/incidents/{inc['id']}/transition", {"to":to,"actor":"dispatcher-1"})
case = [c for c in json.load(urllib.request.urlopen(DIS+"/cases"))["cases"]
        if c["contact_phone"] == "+2348030002222"][0]
tasks = json.load(urllib.request.urlopen(DIS+"/cases/"+case["id"]))["tasks"]
assert not any(t["kind"] == "followup_48h" for t in tasks), tasks
print(f"   {case['id']} closed with consent revoked — no 48h task queued (NDPA respected)")
PY

echo
echo "DISPATCH + CASE MANAGEMENT DEMO OK — one number, one caller history, owned follow-through."
