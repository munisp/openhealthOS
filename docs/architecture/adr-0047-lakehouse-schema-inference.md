# ADR-0047: Lakehouse schema inference — insight from forge-core, not the library

Status: accepted (2026-10-20)
Source: foxtrotcommunications/foxtrotcommunications-forge-core
(Apache-2.0). Evaluated for adoption; **library rejected, insight adopted.**

## Context

Forge-core decomposes nested JSON already sitting in a data warehouse
into normalized dbt models, targeting BigQuery/Snowflake/Databricks.
Our lakehouse is a deliberately stdlib medallion pipeline (bronze →
silver → gold, Parquet-on-MinIO, no dbt, no cloud warehouse — ADR-0005).
Adopting the library would require adopting dbt plus one of its three
supported warehouses — inverting the architecture to fit a dependency.
Rejected on those grounds.

The *insight* underneath it is correct and we had the gap: nested event
JSON should be normalized by **inference over the data**, not by
hand-maintained per-entity flattening code. With 20+ services emitting
into bronze, our silver typing rules were hand-edited — every new
producer field was a silent NULL-drift risk until someone updated code.

## Decision

`packages/lakehouse/schemainfer.py` (stdlib):

- **infer**: scans a bronze JSONL partition, and per dot-path field
  reports inferred type (string/number/bool/timestamp/date/object/array),
  presence rate, nullability, and a **child-table flag** — any object or
  array is a 1NF child-table candidate (the forge-core normalization
  move); scalars stay in the parent. Sparse fields below
  `--min-presence` are marked optional so silver writers never
  hard-depend on them. Type widening is deliberately conservative:
  mixed-type fields become `string` rather than dropping rows on a
  strict column.
- **diff**: compares two inferred schemas and exits non-zero on removed
  or retyped fields — producer drift is a loud CI failure in review,
  not silent silver-column rot. Additions are informational.
- Deterministic (same input → same schema), sampling is explicit
  (`--sample`), and it composes with the existing pipeline: inferred
  schemas are review artefacts that drive the silver writers, not a
  runtime dependency of the hot path.

## Consequences

- New event fields surface as schema diffs in PR review; silver writers
  are updated deliberately, with the evidence attached.
- Bronze stays the immutable truth; inference never mutates it.
- If a future state plane genuinely adopts dbt/Snowflake (national
  analytics is the candidate), forge-core becomes worth re-evaluating —
  noted here so the reconsideration is cheap.
