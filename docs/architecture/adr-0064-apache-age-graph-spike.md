# ADR-0064: Apache AGE graph spike — relationship queries at the national core

## Status
Spike (evaluated; adoption decision deferred to post-spike review).

## Context
Three national-scale questions are relationship problems, and relational SQL
answers them with recursive CTEs that are slow to write, slow to run, and
hard for non-engineers (epidemiologists, NHIA investigators, state
commissioners) to reason about:

1. **Claims-fraud rings** — prescriber ↔ facility ↔ claim ↔ payout-account
   loops and unnatural stars (ghost facilities, collusion).
2. **Contact tracing** — 2-hop "who shared an encounter with a case" during
   outbreak investigation (extends `packages/surveillance/outbreak.py`,
   which detects the signal but not the network).
3. **Referral flow** — which PHCs refer where, where chains break, which
   facilities are orphaned (state commissioning intelligence).

Apache AGE is a PostgreSQL extension adding graph storage and openCypher
queries inside stock Postgres. It passes every platform doctrine check:

- **Licence**: Apache-2.0 — passes the SBOM licence gate (tools/release/sbom.py).
- **Compose-don't-fork**: an extension loaded into off-the-shelf PostgreSQL;
  nothing vendored, nothing forked.
- **No JVM, no new daemon**: runs inside the Postgres we already operate.

## Decision
Spike only, with a strict placement rule:

- **AGE lives at T0/T1 only** (national core, state plane), where Postgres
  already runs. The T2 facility edge and T3 devices are untouched:
  stdlib-only, offline-first, no graph engine they cannot carry.
- The lakehouse medallion pipeline (ADR-0060) is **complemented, not
  replaced**: AGE answers live relationship questions on the operational
  graph; graph-derived features (e.g. facility connectedness degree) may be
  exported to gold aggregates under the existing k-anonymity floor.
- The spike must prove three killer queries, not assert them.

## Spike design and sandbox honesty
The build sandbox has no root and no Docker, so a live AGE server cannot
run inside CI here. The spike therefore ships four artefacts:

1. `packages/graphspike/schema.sql` — graph DDL + the three killer queries
   in real AGE openCypher form.
2. `packages/graphspike/synthdata.py` — deterministic synthetic dataset
   (mirrors `synthseed` doctrine): a planted fraud ring, a planted contact
   cluster, a realistic referral network.
3. `packages/graphspike/spike.py` — a stdlib-only reference engine that
   executes the SAME query logic over the same data and pins golden
   outputs. The live AGE run must reproduce these outputs exactly
   (`run_age.sh --verify`) before adoption is declared proven.
4. `infra/compose/graph.yaml` + `packages/graphspike/run_age.sh` — the
   live path: official `apache/age` image (compose-don't-fork), load, run,
   diff against golden outputs.

Adoption gate: this ADR moves to Accepted only when `run_age.sh --verify`
passes on a real AGE instance and a T1 operator signs off on the
operational cost (one more extension to version and upgrade).

## Consequences
- If adopted: NHIA fraud investigation, outbreak contact tracing, and
  referral commissioning gain a query surface a specialist can use directly.
- If rejected: the reference engine and dataset remain useful as a test
  fixture for the SQL/CTE approach; nothing else changes.
- Deferred: graph-derived gold features, AGE-backed care-circle queries for
  howUdeyChat, OpenHIE-aligned graph exchange format.
