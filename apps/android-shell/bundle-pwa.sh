#!/usr/bin/env bash
# Snapshot the PWA into the shell's assets — run before assembleRelease so
# the offline fallback ships the current app, never a stale one.
set -euo pipefail
cd "$(dirname "$0")"
SRC=../pwa
DST=app/src/main/assets/pwa

rm -rf "$DST"
mkdir -p "$DST"
cp -r "$SRC/index.html" "$SRC/manifest.webmanifest" "$SRC/sw.js" \
      "$SRC/css" "$SRC/js" "$SRC/icons" "$DST/"

echo "bundled $(find "$DST" -type f | wc -l) files ($(du -sh "$DST" | cut -f1)) into $DST"
