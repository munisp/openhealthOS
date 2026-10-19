#!/usr/bin/env bash
# Population health + patient wellness smoke (ADR-0034/0035):
# FHIR bundle -> OMOP-lite tables, cohorts, care gaps, predictive risk,
# prevention plans; wellness-service ingests device/self-logged data,
# mirrors FHIR Observations into the SHR, and plans see BOTH rails.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== 0. fixtures: a small FHIR bundle (as the SHR exports it) =="
cat > "$TMP/bundle.json" <<'JSON'
{"resourceType":"Bundle","type":"searchset","entry":[
 {"resource":{"resourceType":"Patient","id":"p-dia","birthDate":"1976-03-01","gender":"male"}},
 {"resource":{"resourceType":"Patient","id":"p-young","birthDate":"2000-06-01","gender":"female"}},
 {"resource":{"resourceType":"Condition","id":"c1","subject":{"reference":"Patient/p-dia"},
   "code":{"coding":[{"code":"E11.9"}]},"onsetDateTime":"2025-09-01"}},
 {"resource":{"resourceType":"Condition","id":"c2","subject":{"reference":"Patient/p-dia"},
   "code":{"coding":[{"code":"I10"}]},"onsetDateTime":"2025-09-01"}},
 {"resource":{"resourceType":"Observation","id":"o1","subject":{"reference":"Patient/p-dia"},
   "code":{"coding":[{"code":"8480-6"}]},"valueQuantity":{"value":152,"unit":"mmHg"},
   "effectiveDateTime":"2026-10-10"}},
 {"resource":{"resourceType":"Observation","id":"o2","subject":{"reference":"Patient/p-dia"},
   "code":{"coding":[{"code":"4548-4"}]},"valueQuantity":{"value":8.1,"unit":"%"},
   "effectiveDateTime":"2026-02-15"}},
 {"resource":{"resourceType":"Encounter","id":"e1","subject":{"reference":"Patient/p-dia"},
   "class":"AMB","period":{"start":"2026-10-10"}}}
]}
JSON

echo "== 1. OMOP-lite export =="
python3 packages/pophealth/pophealth.py omop --bundle "$TMP/bundle.json" --out "$TMP/omop" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["omop"]; print("   tables:", d); assert d["person"]==2 and d["condition_occurrence"]==2'
test -f "$TMP/omop/concepts.json" && echo "   concepts.json (OH-lite namespace) written"

echo "== 2. cohorts + care gaps + predictive risk =="
python3 packages/pophealth/pophealth.py all --bundle "$TMP/bundle.json" --out "$TMP/omop" \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("   cohorts:", d["cohorts"])
assert d["cohorts"]["diabetes"] == 1 and d["cohorts"]["hypertension"] == 1
gaps = d["gaps"]["p-dia"]
ids = {g["gap"] for g in gaps}
print("   p-dia gaps:", sorted(ids))
assert "hba1c" in ids            # 8 months stale, due every 6
assert "bp-check" not in ids     # checked 9 days ago
r = d["risk"]["p-dia"]
print("   p-dia cvd_risk=%s (%s) model=%s" % (r["cvd_risk"], r["cvd_band"], r["model"]))
assert r["model"] == "heuristic-1" and r["cvd_band"] in ("moderate", "high")'

echo "== 3. build services =="
( cd services/wellness-service && go build -o "$TMP/wellness" . )
( cd services/shr-service && go build -o "$TMP/shr" . )

echo "== 4. SHR up, wellness rail up (mirroring into the record) =="
LISTEN=:18221 SHR_DATA_DIR="$TMP/shr" "$TMP/shr" >"$TMP/shr.log" 2>&1 &
sleep 1
LISTEN=:18222 WELLNESS_DATA_DIR="$TMP/well" SHR_URL=http://127.0.0.1:18221 \
  POPHEALTH="$PWD/packages/pophealth/pophealth.py" \
  "$TMP/wellness" >"$TMP/well.log" 2>&1 &
sleep 1
curl -sf -m 3 http://127.0.0.1:18222/healthz >/dev/null || { cat "$TMP/well.log"; exit 1; }

echo "== 5. device ingest (Fitbit steps + Health Connect workout) -> FHIR Observations =="
curl -sf -X POST http://127.0.0.1:18222/wellness/ingest -H 'Content-Type: application/json' -d '{
  "patient_id":"p-dia","source":"fitbit","observations":[
    {"type":"steps","value":2300,"unit":"steps","ts":"2026-10-19T18:00:00Z","ref":"fb-day-1"},
    {"type":"heart-rate","value":78,"unit":"bpm","ts":"2026-10-19T18:00:00Z","ref":"fb-hr-1"}]}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); print("   accepted:",r["accepted"]); assert r["accepted"]==2'
# replay: exactly-once
curl -sf -X POST http://127.0.0.1:18222/wellness/ingest -H 'Content-Type: application/json' -d '{
  "patient_id":"p-dia","source":"fitbit","observations":[
    {"type":"steps","value":2300,"unit":"steps","ts":"2026-10-19T18:00:00Z","ref":"fb-day-1"}]}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["deduped"]==1; print("   replay deduped — exactly-once holds")'
# allow-list: unregistered source refused
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18222/wellness/ingest \
  -H 'Content-Type: application/json' -d '{"patient_id":"p-dia","source":"strava","observations":[{"type":"steps","value":1}]}')
[ "$code" = 403 ] && echo "   unregistered source refused (403) — allow-list is the policy"

echo "== 6. the data IS in the medical record (SHR) =="
curl -sf -H 'X-Patient-Self: p-dia' \
  "http://127.0.0.1:18221/fhir/Patient/p-dia/\$everything" | python3 -c '
import json, sys
b = json.load(sys.stdin)
obs = [e["resource"] for e in b["entry"] if e["resource"].get("resourceType") == "Observation"]
codes = [o["code"]["coding"][0]["code"] for o in obs]
print("   SHR observations:", codes)
assert "55423-8" in codes and "8867-4" in codes   # steps + heart rate, LOINC-coded
perf = obs[0].get("performer", [{}])[0].get("display", "")
print("   provenance:", perf)
assert perf == "wellness:fitbit"'

echo "== 7. summary + prevention plan (wellness data feeds the plan) =="
curl -sf http://127.0.0.1:18222/wellness/p-dia/summary | python3 -c '
import json, sys
s = json.load(sys.stdin)
print("   steps_today=%d sources=%s" % (s["steps_today"], s["sources"]))
assert s["steps_today"] == 2300 and s["sources"] == ["fitbit"]'
curl -sf "http://127.0.0.1:18222/wellness/p-dia/plan?age=50&sex=male" | python3 -c '
import json, sys
p = json.load(sys.stdin)
print("   plan recs:")
for r in p["recommendations"]: print("     -", r)
assert p["avg_daily_steps_14d"] == 2300
assert any("5,000 steps" in r for r in p["recommendations"])
assert p["risk"]["model"] == "heuristic-1"'

echo "== 8. unit batteries =="
( cd packages/pophealth && python3 -m unittest 2>&1 | tail -1 )
( cd services/wellness-service && go test ./... 2>&1 | tail -1 )
( cd apps/patient && node --test tests/wellness.test.mjs 2>&1 | grep -E "^# (pass|fail)" )

echo "POPULATION HEALTH + WELLNESS DEMO OK"
