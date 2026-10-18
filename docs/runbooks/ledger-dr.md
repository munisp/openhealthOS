# Ledger disaster recovery — warm standby & promotion (ADR-0015)

The Rust ledger is single-writer with fsync-journal durability (ADR-0007).
`tools/ledger-mirror` closes the remaining gap: a **verified warm standby**
the operator can promote in minutes. Multi-writer consensus remains the
documented scale-out step (scorecard, TigerBeetle row); this runbook covers
everything short of it.

## Topology

```
primary ledger ──LEDGER_DATA_DIR=/data/ledger
      ▲  (reads only)
ledger-mirror -source /data/ledger -dest /standby/ledger -interval 2s
      ▼
/standby/ledger = byte-identical journal + snapshot + mirror-manifest.json
```

The mirror is incremental (only new journal bytes per cycle), truncation-
aware (a snapshot resyncs it), and self-verifying (`mirror-manifest.json`
holds SHA-256 of what was actually written, fsynced).

## Run it

```bash
# continuous mirror (systemd unit recommended on the standby node)
ledger-mirror -source /data/ledger -dest /standby/ledger -interval 2s

# one-shot (cron, object-storage sync pipelines)
ledger-mirror -source /data/ledger -dest /standby/ledger -once

# verify — run in monitoring; page on failure
ledger-mirror -source /data/ledger -dest /standby/ledger -verify
```

## Health gate (monitoring)

1. `ledger-mirror -verify` exits 0 — mirror is byte-identical.
2. `mirror-manifest.json` `synced_at` is fresh (< 3× interval) — the
   mirror is running, not just once-correct.

A standby that fails either check is a **hope, not a copy**: alert.

## Promotion (primary lost)

1. **Fence the primary.** Kill it / cordon the node. The ledger is
   single-writer: two writers is the only way to fork money. Promotion is
   an operator decision, never automatic.
2. Final sync: `ledger-mirror -source /data/ledger -dest /standby/ledger -once`
   (skip if the primary's disk is gone — the mirror is already the best copy).
3. Verify: `ledger-mirror -source /standby/ledger -dest /standby/ledger -verify`
   reads the manifest's hashes against the files (self-check).
4. Start the standby ledger with `LEDGER_DATA_DIR=/standby/ledger`.
   Recovery (snapshot + journal replay, torn-line tolerant, prepared-2PC
   restore) is the ledger's own tested path — it will come up with:
   - every committed balance exact;
   - any prepared-but-uncommitted transfer back in the prepared set,
     resolved by orchestrator retry (commit/abort), never by data loss.
5. Point the mirror at the NEW standby before declaring the incident over.

## Fail-back

The old primary, when repaired, becomes the standby: wipe its
`LEDGER_DATA_DIR`, mirror onto it from the new primary, verify, done.

## What this does NOT cover (stated honestly)

- **Split-brain**: there is no consensus protocol. Fencing is operational
  (step 1). If an operator promotes while the primary still writes, both
  journals diverge and reconciliation is manual (compare journals — they
  are JSONL op streams, diff-able by design).
- **Zero RPO**: replication lag is one poll interval (default 2s). Ops
  acknowledged-but-not-yet-mirrored in that window are lost on total
  primary loss. Tighten `-interval` to reduce; synchronous mirroring
  would trade primary latency for it and is a deliberate non-goal at
  pilot scale.
