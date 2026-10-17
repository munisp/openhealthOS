# Runbook: backup and restore

Two kinds of state exist, with different rules: **Postgres** (patients,
consents, FHIR resources, telehealth, equipment, sync events) and the
**ledger journal** (money). Both must survive the loss of any single node.

## What to back up, and how often

| Store | Contents | Method | RPO target |
|---|---|---|---|
| Postgres | all relational state | nightly `pg_dump` + continuous WAL archiving | 5 min (WAL) |
| Ledger dir | `snapshot.json` + `journal.log` per ledger instance | volume snapshot or `rsync` after a `snapshot` op | 24 h worst case; every accepted op is fsynced, so node-local loss is bounded by disk failure, not by the app |

Postgres is the system of record for clinical/enrolment data; the ledger
journal is the system of record for balances (ADR-0007). Neither may be
reconstructed from the other — back up both.

## Postgres backup

```sh
# nightly logical dump
pg_dump "$DATABASE_URL" --format=custom --file=openhealth-$(date +%F).dump
# plus WAL archiving (archive_command) for point-in-time recovery
```

Verify monthly: restore the latest dump into a scratch database and run
`SELECT count(*) FROM patients; SELECT count(*) FROM sync_events;`
against expectations.

## Ledger backup

The ledger is durable by construction: every accepted op is appended to
`journal.log` with fsync, and `snapshot.json` + truncation compacts it.
To take a consistent file-level backup:

1. Issue a `snapshot` op (CLI: `echo snapshot | oh_ledger` with
   `LEDGER_DATA_DIR` set, or via the orchestrator) — this writes
   `snapshot.json`, fsyncs, and truncates the journal.
2. Copy `snapshot.json` and the (now tiny) `journal.log` to backup
   storage. This pair is a complete, consistent ledger.

## Restore: Postgres

```sh
createdb openhealth_restore
pg_restore --dbname=openhealth_restore openhealth-YYYY-MM-DD.dump
# or for PITR: restore base backup + replay WAL to the target timestamp
```

Then run the migration job against the restored database — migrations are
idempotent (`IF NOT EXISTS`), so this is a no-op when the dump is current
and repairs schema drift when it is not.

## Restore: ledger

1. Stop the ledger-using service (claims-orchestrator or wallet-service).
2. Replace the data dir contents with the backed-up `snapshot.json` and
   `journal.log`.
3. Start the service. Boot replays snapshot then journal and **fails
   closed** on any inconsistency — if it starts, balances are exact.
4. Verify: query a known account balance and compare against the last
   settlement report.

## Disaster-recovery order

1. Postgres first (identity, consents, clinical data gate everything).
2. Ledger volumes second (money path stays down — fail-closed — until
   restored; it never serves guessed balances).
3. Services last; they are stateless and come up with `kubectl apply`.

## Drills

- Quarterly: full restore of both stores into a scratch namespace, run
  the demo script (`demo/demo.sh`) against it.
- After any restore drill, record recovery time; target RTO ≤ 4 h at
  pilot scale.
