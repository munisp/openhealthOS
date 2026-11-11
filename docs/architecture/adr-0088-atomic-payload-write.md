# ADR-0088: Stamped payloads land atomically — the wave-19 flake explained and closed

- Status: accepted
- Date: 2026-11-11
- Wave: 24 (README row 229)

## Context

Wave 19 (the freshness gate) left one stated flake:
`test_self_exclusion_is_declared_data` failed once — while the tree
held a stale geointproof — and never reproduced across repeated re-runs.
A flake that stays unexplained is a gate nobody fully trusts.

The mechanism, reconstructed and then proven on this estate's harness:

- `scan_stamped` **never raises** — an unparseable file is skipped, by
  contract (a genuinely malformed registered payload still gets its own
  named verdict from `verify_payload`).
- During wave-19 development, an exporter run (the freshness dry-run /
  refresh iterations) overlapped the suite. The coverage scan read
  `proofindex.json` **mid-rewrite**, got `JSONDecodeError`, and skipped
  it; the self-exclusion membership assertion failed for that one scan,
  and the next run self-healed.
- The stale geointproof was the tree state at the time — correlation,
  not cause. A stale payload still parses, so the scan still sees it.

Measured with a 256KB payload written in 4KB chunks under a concurrent
scan loop: a raw writer is torn on **12,579 of 12,759 scans (98.6%)**;
the same harness through `payloadio.write_text` is torn on **0 of
6,953**. Every one of the 35 stamped payloads was written with a raw
`open(path, "w")` — the class covered the whole evidence estate.

## Decision

1. **One seam**: `payloadio.write_text(path, text)` — bytes go to a tmp
   sibling in the target's own directory (the rename guarantee does not
   cross filesystems), fsync, then `os.replace`. A reader sees the old
   payload or the new one, never a torn one. The write is loud: any
   failure raises and sweeps its tmp residue.
2. **Residue is never evidence**: tmp names never end in `.json`, so
   `scan_stamped` cannot mistake a crashed writer's litter for a
   payload.
3. **The seam owns atomicity, not serialization**: each exporter keeps
   its own `json.dumps` arguments; the two stamp contracts stay the
   proofindex registry's data. The seam also writes explicit UTF-8,
   removing a latent locale-dependent encoding risk at sites that
   previously used locale-default text mode with `ensure_ascii=False`
   (byte-identical in this estate's UTF-8 locale; hardened elsewhere).
4. **Adoption is gated**: the adoption census is registry data (28 → 29
   writer modules, delegates and declared raw exceptions named as
   data), and `TestAdoption` fails red on any writer that bypasses the
   seam. A seam unadopted by its own estate is decoration.
5. **Closure by construction**: the wave-19 flake is replayed in the
   demo under the seam (concurrent rewrite + 50 membership scans, zero
   skips) — the flake class is impossible, not merely unwatched.

## Consequences

- All 29 payload-writer modules route through the seam; byte-drift
  across every registered payload is **zero** (proven by sha256
  snapshot before/after adoption, and by the freshness gate staying
  green against HEAD).
- `atomproof` (demo + board + proofindex payload + refresh recipe)
  carries the mechanism, the measured campaign, and the five drills.
- The wave-19 flake entry in memory is reclassified: explained
  (torn-read under concurrent write), closed (atomic writes).

## Alternatives considered

- **Retry-on-parse-error in scan_stamped**: hides the window instead of
  closing it; a retry can still land inside a long write, and "never
  raises, skips unparseable" is the correct gate contract for genuinely
  malformed files.
- **Suite-level mutex against exporters**: operational rule, not a
  property; browsers and operators can still read mid-write.
- **Shared serializer in the seam**: rejected — the two stamp contracts
  are deliberate registry data; a third format funnel invites drift.
