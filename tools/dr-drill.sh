#!/usr/bin/env bash
# OpenHealth OS — automated disaster-recovery drill (ADR-0013, improvement).
#
# Backups that have never been restored are hopes, not backups. This drill:
#   1. pg_dump the live database (custom format)
#   2. restore into a throwaway database
#   3. verify row counts of core clinical tables match source exactly
#   4. drop the throwaway database
# Exit 0 only if restore + verification succeed.
#
#   DATABASE_URL=postgres://…/openhealth tools/dr-drill.sh
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
SRC_DB="$(basename "${DATABASE_URL%%\?*}")"
ADMIN_URL="${DATABASE_URL%/*}/postgres"
DRILL_DB="${SRC_DB}_drill_$$"
DUMP="$(mktemp /tmp/openhealth-dr-XXXXXX.dump)"
trap 'rm -f "$DUMP"; psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS $DRILL_DB" || true' EXIT

echo "== DR drill: dump $SRC_DB =="
pg_dump "$DATABASE_URL" --format=custom --file="$DUMP"
test -s "$DUMP" || { echo "FAIL: empty dump"; exit 1; }

echo "== DR drill: restore into $DRILL_DB =="
psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS $DRILL_DB"
psql "$ADMIN_URL" -q -c "CREATE DATABASE $DRILL_DB"
pg_restore --dbname="${DATABASE_URL%/*}/$DRILL_DB" --no-owner --no-privileges "$DUMP"

echo "== DR drill: verify table counts =="
FAILED=0
for table in facilities patients fhir_resources sync_events claims_drafts claim_drafts; do
  # tolerate either historical or current spelling; skip absent tables
  exists=$(psql "$DATABASE_URL" -Atc "SELECT to_regclass('public.$table') IS NOT NULL")
  [ "$exists" = "t" ] || continue
  src=$(psql "$DATABASE_URL" -Atc "SELECT count(*) FROM $table")
  dst=$(psql "${DATABASE_URL%/*}/$DRILL_DB" -Atc "SELECT count(*) FROM $table")
  if [ "$src" != "$dst" ]; then
    echo "FAIL: $table source=$src restored=$dst"
    FAILED=1
  else
    echo "  $table: $src rows — match"
  fi
done

[ "$FAILED" = "0" ] || { echo "DR DRILL FAILED"; exit 1; }
echo "DR DRILL OK — backup restored and verified ($DRILL_DB dropped on exit)"
