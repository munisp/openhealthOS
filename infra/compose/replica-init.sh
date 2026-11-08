#!/usr/bin/env bash
# replica-init.sh — bootstrap a streaming read replica for checkpoint
# fan-out. Idempotent: a data directory that already holds a replica
# just starts; an empty one is built from the primary.
#
# The contract this script keeps honest:
# - the replica is READ fan-out only; nothing writes here, ever
# - replication credentials come from the environment, never baked in
# - the slot is declared (replica_1) so lag is observable — the
#   syncmesh ReplicationSlotGuard reads exactly this slot's lag
set -euo pipefail

PGDATA=/var/lib/postgresql/data
PRIMARY_HOST="${PRIMARY_HOST:-postgres}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
SLOT="${REPLICATION_SLOT:-replica_1}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "replica-init: empty data dir — base backup from $PRIMARY_HOST"
  # the replication password arrives via PGPASSWORD from the compose
  # environment; it is never written into this script or the image
  pg_basebackup -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U replicator \
    -D "$PGDATA" -Fp -Xs -P -R -C -S "$SLOT"
  # -R writes primary_conninfo into postgresql.auto.conf
  # -C -S create and pin the replication slot on the primary
  cat >> "$PGDATA/postgresql.auto.conf" <<'CONF'
hot_standby = on
wal_receiver_status_interval = 5s
hot_standby_feedback = on
CONF
  echo "replica-init: base backup complete, slot $SLOT pinned"
fi

# -R already wrote primary_conninfo into postgresql.auto.conf; the
# stock entrypoint config is enough — no hidden config_file path
exec postgres
