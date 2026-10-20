# ADR-0060: Chronic-registry lakehouse feed for advanced analytics

Date: 2026-10-20 · Status: accepted

## Context

packages/chronicreg (ADR-0058) is the operational store for HIV /
hypertension / diabetes cascades. The question: can it feed the lakehouse
(packages/lakehouse, ADR-0047 medallion contracts) for advanced analytics?
Yes — but only as events on the shared contracts, never as a second
database schema someone must keep in sync.

## Decision

`packages/chronicreg/lakefeed.py`:

1. **Events, not tables** — registry state is emitted as `registry_enrolment`,
   `stage_transition`, and `adherence_snapshot` events in the exact shape the
   lakehouse bronze layer already partitions (`{id, ts, facility, type,
   payload}`). The existing bronze/silver/gold pipeline ingests them with no
   changes.
2. **OMOP-flavoured silver** — `silver_omop()` maps events to person /
   condition_occurrence / observation tables so pophealth cohort tooling
   (ADR-0034) consumes registries without bespoke adapters. Parquet when
   pyarrow is present, JSONL otherwise — same contract, swappable writer.
3. **Gold: cascade + adherence analytics** — latest-stage cascade per
   registry×facility, adherence band distribution, pct-high. Reproducible
   from bronze (replay-safe); **k-anonymity floor k=5 suppresses small
   cells** (same doctrine as surveillance aggregation).
4. **One-way door** — analytics NEVER write back to the operational
   registry. Care decisions flow from the registry; insight flows to the
   lakehouse.

## Consequences

- Positive: cascade funnels ("where do we lose patients between on_art and
  suppressed?"), adherence equity cuts by facility/LGA, and payrails quality
  adjustments are now computable from gold without touching operations.
- Limitations (honest): gold here is aggregate JSON; cohort-level survival
  analysis and OMOP CDM conformance tooling remain analytics-side work. The
  feed is batch (CLI/scheduled); streaming via sync events is the natural
  next step since the event shape already matches.
