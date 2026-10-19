#!/usr/bin/env bash
# Refresh the bundled PWA snapshot (Resources/pwa) from apps/pwa —
# mirrors apps/android-shell/bundle-pwa.sh. Run on a Mac before building.
set -euo pipefail
cd "$(dirname "$0")"
SRC="${APP_SRC:-../pwa}"  # clinician PWA by default; APP_SRC=../patient for the patient build
DEST="OpenHealthShell/Resources/pwa"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC"/. "$DEST"/
# shared cross-app assets the PWA links as ../
cp ../design-tokens.css ../ambulance-map.js "$DEST/../" 2>/dev/null || {
  mkdir -p "$DEST/.."
  cp ../design-tokens.css ../ambulance-map.js "$DEST/../"
}
echo "bundled $(find "$DEST" -type f | wc -l | tr -d ' ') files into $DEST"
