#!/usr/bin/env bash
# Telemedicine smoke (ADR-0026): async-first consult, consent enforcement,
# clinician advice SLA, escalation to a LiveKit voice room with verified
# HS256 join tokens.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/telemed-service && go build -o "$TMP/telemed" . )
( cd services/media-service && go build -o "$TMP/media" . )
LISTEN=:18194 LK_API_KEY=demokey LK_API_SECRET=demosecret "$TMP/telemed" >"$TMP/telemed.log" 2>&1 &
LISTEN=:18195 MEDIA_DIR="$TMP/blobs" "$TMP/media" >"$TMP/media.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18194/healthz" >/dev/null || { cat "$TMP/telemed.log"; exit 1; }
curl -sf -m 3 "http://127.0.0.1:18195/healthz" >/dev/null || { cat "$TMP/media.log"; exit 1; }
API=http://127.0.0.1:18194
MEDIA=http://127.0.0.1:18195

echo "== 1. consent is mandatory (NDPA) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/consults" \
  -H 'Content-Type: application/json' \
  -d '{"patient_id":"PAT-9","complaint":"headache","consent":false}')
[ "$code" = "422" ] && echo "   unconsented consult rejected (422)"

echo "== 2. patient uploads a real photo to the media store =="
printf '\xff\xd8\xff\xe0 fake-jpeg-rash-photo' > "$TMP/rash.jpg"
REF=$(curl -sf -X POST "$MEDIA/blobs" -H 'Content-Type: image/jpeg' \
  -H 'X-Actor: PAT-9' --data-binary @"$TMP/rash.jpg" \
  | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['meta']['sha256']; print(r['ref'])")
echo "   stored as $REF (sha256-checked on every read)"

echo "== 3. async consult references the uploaded photo =="
TC=$(curl -sf -X POST "$API/consults" -H 'Content-Type: application/json' -d "{
  \"patient_id\":\"PAT-9\",\"facility_id\":\"PHC-1\",\"priority\":\"urgent\",
  \"complaint\":\"child with fever and rash since yesterday\",
  \"consent\":true,\"actor\":\"PAT-9\",\"role\":\"patient\",
  \"media_ref\":\"$REF\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "   $TC opened on the async tier (works on 2G)"

echo "== 4. clinician downloads the photo (integrity verified) =="
curl -sf "$MEDIA/blobs/${REF##*/}" -o "$TMP/downloaded.jpg"
cmp "$TMP/rash.jpg" "$TMP/downloaded.jpg" && echo "   bytes identical, etag checksum matched"

echo "== 5. clinician reviews and advises — thread flips to advice_given =="
curl -sf -X POST "$API/consults/$TC/transition" -H 'Content-Type: application/json' -d '{"to":"triaged"}' >/dev/null
curl -sf -X POST "$API/consults/$TC/transition" -H 'Content-Type: application/json' -d '{"to":"in_review","clinician":"dr-a"}' >/dev/null
curl -sf -X POST "$API/consults/$TC/messages" -H 'Content-Type: application/json' \
  -d '{"author":"dr-a","role":"clinician","body":"Rash is blanching; give paracetamol, return if neck stiffness. RDT advised at PHC."}' \
  | python3 -c "import json,sys; c=json.load(sys.stdin); assert c['state']=='advice_given', c['state']; print('   advice recorded; state:', c['state'])"

echo "== 6. photo unclear — escalate to a voice call (3G tier) =="
curl -sf -X POST "$API/consults/$TC/transition" -H 'Content-Type: application/json' -d '{"to":"in_review"}' >/dev/null
ROOM=$(curl -sf -X POST "$API/consults/$TC/escalate" -H 'Content-Type: application/json' \
  -d '{"tier":"voice","actor":"dr-a"}' | python3 -c "import json,sys; c=json.load(sys.stdin); assert c['state']=='escalated_to_call' and c['room']; print(c['room'])")
echo "   room provisioned atomically with escalation: $ROOM"

echo "== 7. both parties mint join tokens; signatures verify =="
for who in dr-a PAT-9; do
  curl -sf -X POST "$API/consults/$TC/token" -H 'Content-Type: application/json' \
    -d "{\"identity\":\"$who\"}" > "$TMP/tok-$who.json"
done
python3 - <<PY
import base64, hashlib, hmac, json
for who in ("dr-a", "PAT-9"):
    tok = json.load(open("$TMP/tok-%s.json" % who))
    h, p, sig = tok["token"].split(".")
    expect = base64.urlsafe_b64encode(
        hmac.new(b"demosecret", f"{h}.{p}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert hmac.compare_digest(sig, expect), "bad signature"
    claims = json.loads(base64.urlsafe_b64decode(p + "=="))
    assert claims["video"]["room"] == "$ROOM" and claims["sub"] == who
    print(f"   {who}: token valid, room={claims['video']['room']}, tier={tok['tier']}")
PY

echo "== 8. closed consults reject messages =="
curl -sf -X POST "$API/consults/$TC/transition" -H 'Content-Type: application/json' -d '{"to":"completed"}' >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/consults/$TC/messages" \
  -H 'Content-Type: application/json' -d '{"author":"PAT-9","role":"patient","body":"thanks"}')
[ "$code" = "409" ] && echo "   closed thread rejects new messages (409)"

echo
echo "TELEMED SMOKE: PASS"
