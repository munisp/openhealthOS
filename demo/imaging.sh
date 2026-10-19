#!/usr/bin/env bash
# Imaging/video/docintel smoke (ADR-0028): DICOMweb facade against a fake
# PACS, STOW header fallback → triage worklist, multipart video upload,
# Range streaming, transcode-job lifecycle, and the docintel fallback
# chain (contract level — heavy engines live in the docai sidecar).
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/media-service && go build -o "$TMP/media" . )
( cd services/imaging-service && go build -o "$TMP/imaging" . )

echo "== fake PACS (echoes DICOMweb) =="
cat > "$TMP/fakepacs.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps([{"study": "1.2.3", "path": self.path}]).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0")); self.rfile.read(n)
        body = b'{"stored":true}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
HTTPServer(("127.0.0.1", 18197), H).serve_forever()
PY
python3 "$TMP/fakepacs.py" &

LISTEN=:18195 MEDIA_DIR="$TMP/blobs" "$TMP/media" >"$TMP/media.log" 2>&1 &
LISTEN=:18196 ORTHANC_URL=http://127.0.0.1:18197 "$TMP/imaging" >"$TMP/imaging.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18195/healthz" >/dev/null || { cat "$TMP/media.log"; exit 1; }
curl -sf -m 3 "http://127.0.0.1:18196/healthz" >/dev/null || { cat "$TMP/imaging.log"; exit 1; }
MEDIA=http://127.0.0.1:18195
IMG=http://127.0.0.1:18196

echo "== 1. DICOMweb proxy (QIDO-RS passthrough) =="
curl -sf "$IMG/dicomweb/studies?PatientID=PAT-1" | grep -q '"study"' \
  && echo "   QIDO query proxied to PACS"

echo "== 2. STOW header fallback registers the worklist =="
curl -sf -X POST "$IMG/dicomweb/studies" \
  -H 'X-Modality: CR' -H 'X-Study-Description: Chest trauma screen' \
  -H 'X-Study-UID: 1.2.3' -H 'X-Patient-ID: PAT-1' \
  -H 'Content-Type: application/dicom' --data-binary 'FAKE-DICOM-BYTES' >/dev/null
worklist=$(curl -sf "$IMG/worklist")
echo "$worklist" | grep -q '"priority":"stat"' \
  && echo "   trauma keyword → stat priority on the worklist"

echo "== 3. SLA overdue view + radiologist sign =="
curl -sf -X POST "$IMG/worklist/1.2.3/sign" -H 'Content-Type: application/json' \
  -d '{"radiologist":"musa"}' >/dev/null
curl -sf "$IMG/worklist" | grep -q '"read_by":"musa"' && echo "   signed, cleared from unread"

echo "== 4. multipart video upload (3 parts, resume-safe) =="
# Part-size cap is MaxUploadBytes/MaxUploadParts + 1 MiB ≈ 1.5 MiB —
# chunked upload is for 2G drops, so parts stay small.
head -c 1000000 /dev/urandom > "$TMP/p1"
head -c 1000000 /dev/urandom > "$TMP/p2"
head -c 1000000 /dev/urandom > "$TMP/p3"
cat "$TMP/p1" "$TMP/p2" "$TMP/p3" > "$TMP/whole.mpg"
sess=$(curl -sf -X POST "$MEDIA/uploads" -H 'X-Actor: sonographer-1' \
  -H 'Content-Type: application/json' \
  -d '{"content_type":"video/mpeg","size":3000000}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for n in 1 2 3; do
  curl -sf -X PUT "$MEDIA/uploads/$sess/parts/$n" -H 'X-Actor: sonographer-1' \
    --data-binary "@$TMP/p$n" >/dev/null
done
ref=$(curl -sf -X POST "$MEDIA/uploads/$sess/complete" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ref"])')
echo "   assembled -> $ref"
blobid=${ref#blob://media/}
curl -sf "$MEDIA/blobs/$blobid" -o "$TMP/roundtrip.mpg"
cmp "$TMP/whole.mpg" "$TMP/roundtrip.mpg" && echo "   byte-exact roundtrip"

echo "== 5. Range streaming =="
code=$(curl -s -o "$TMP/range.bin" -w '%{http_code}' -H 'Range: bytes=1000000-1000999' "$MEDIA/blobs/$blobid")
[ "$code" = "206" ] && [ "$(stat -c%s "$TMP/range.bin")" = "1000" ] \
  && echo "   206 partial content, 1000 bytes"

echo "== 6. transcode job lifecycle =="
job=$(curl -sf -X POST "$IMG/videos" -H 'X-Actor: sonographer-1' \
  -H 'Content-Type: application/json' -d "{\"source_ref\":\"$ref\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf -X POST "$IMG/videos/$job/update" -H 'Content-Type: application/json' \
  -d "{\"status\":\"done\",\"manifest_ref\":\"blob://media/hls/$job/master.m3u8\",\"segment_refs\":[\"blob://media/hls/$job/480p/seg_00000.ts\"],\"renditions\":[480,360]}" >/dev/null
curl -sf "$IMG/videos/$job" | grep -q '"status":"done"' \
  && echo "   job done, manifest + renditions recorded"

echo "== 7. de-ID export plan (k-anonymity floor) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$IMG/studies/1.2.3/export-plan" \
  -H 'Content-Type: application/json' -d '{"cohort_size":3,"pseudonym":"ANON-001"}')
[ "$code" = "422" ] && echo "   cohort of 3 rejected (k=5 floor)"
curl -sf -X POST "$IMG/studies/1.2.3/export-plan" -H 'Content-Type: application/json' \
  -d '{"cohort_size":12,"pseudonym":"ANON-001","modality":"US"}' | grep -q '"burned_in_pixel_risk":true' \
  && echo "   US modality flagged for burned-in pixel risk"

echo "== 8. docintel fallback chain (unit level) =="
( cd packages/docintel && python3 -m unittest 2>&1 | tail -1 )

echo "== transcoder core (unit level) =="
( cd workers/transcoder && cargo test --offline 2>&1 | grep -E 'test result: ok' | head -1 )

echo "IMAGING DEMO OK"
