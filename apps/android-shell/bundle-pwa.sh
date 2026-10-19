#!/usr/bin/env bash
# Snapshot the PWA into the shell's assets — run before assembleRelease so
# the offline fallback ships the current app, never a stale one.
#
# APP_SRC selects the app: ../pwa (clinician, default) or ../patient
# (patient app — includes the PHI step-up sheet, ADR-0039; it is plain
# WebView-safe DOM, no native bridge needed).
set -euo pipefail
cd "$(dirname "$0")"
SRC="${APP_SRC:-../pwa}"
DST=app/src/main/assets/pwa

rm -rf "$DST"
mkdir -p "$DST"
cp -R "$SRC"/. "$DST/"
# shared cross-app assets the apps link as ../
cp ../design-tokens.css ../ambulance-map.js app/src/main/assets/ 2>/dev/null || true

echo "bundled $(find "$DST" -type f | wc -l) files ($(du -sh "$DST" | cut -f1)) from $SRC into $DST"
