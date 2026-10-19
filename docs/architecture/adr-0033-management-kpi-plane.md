# ADR-0033: Management KPI plane — nightly batch, one document, two new boards

Status: accepted · 2026-10-19

## Context

The platform had the analytics and the dashboards, but not the
connection: packages/economics (ADR-0032) computes margins, panel P&L
and denials on demand through the claims-orchestrator seam; the ml/
registry and monitor (ADR-0014) know champion versions and drift PSI;
ml/openhealth_ml/catchment.py can assign members to facilities. None of
that was visible to a hospital manager or a state desk — the dashboards
(apps/dashboards) showed operations telemetry, pilot gates and payer
tallies, nothing economic and nothing about model health.

Two additional constraints:

- The economics/monitor entry points are **compute-on-request** tools
  for analysts; a dashboard must never trigger a multi-second job per
  viewer, and must render when every upstream is down.
- Catchment assignment is O(members × facilities); fine at pilot scale,
  not at 10M members. ADR-0014 already assigns geospatial batch work to
  the state-plane Spark/Sedona tier.

## Decision

**One nightly job, one document, one endpoint, two boards.**

1. **`packages/kpiplane` (stdlib-only)** — the nightly state-plane batch
   job. It composes the existing rails: packages/economics for
   margins/panel/denials, the economics risk bander for panel_risk, a
   local haversine nearest-facility pass for catchments, direct reads of
   the file registry (the layout IS the interface — no ml import) for
   champion metadata, and `openhealth_ml.monitor.drift_report` for
   feature PSI (stdlib-safe import). Output:
   `lakehouse/gold/kpi_plane.json` with four sections
   (`facility_economics`, `panel_risk`, `catchments`, `ml_health`), each
   degrading independently to `{"status": "no-data", ...}` when its
   input export is absent. A pilot state with no enrolment file still
   gets denial and drift boards.

2. **api-gateway `GET /api/kpi/plane`** — serves that file
   (`KPI_PLANE_PATH`), read-only, `Cache-Control: max-age=300`, 404
   until the first nightly build lands. Deliberately a file read, never
   a fan-out: the board must render from last night's snapshot when
   every service is down.

3. **apps/dashboards gains `#/economics` and `#/mlhealth`** —
   EconomicsView (margin per service line worst-first, capitation panel
   P&L per risk band with walk-in leakage, preventable-denial share with
   per-HMO bars, catchment table with >15km exposure) and MLHealthView
   (drift verdict/PSI, champion freshness, per-model AUC/age). Pure
   transforms in data.js are node-tested; the same IndexedDB
   last-known + labelled-demo fallback applies, so the boards degrade
   exactly like the others.

4. **Sedona swap point is documented in the job docstring**: when member
   counts pass ~1M, `assign_catchments()` becomes a Sedona KNN spatial
   join over Parquet member/facility layers on the state-plane Spark
   tier. The JSON contract is the seam — dashboards, tests and the
   gateway keep working against the Spark output unchanged. Ray is not
   involved: nothing here is training.

## Why not compute live in the gateway

serveOverview-style fan-out was rejected: economics over a month of
encounters is seconds, not milliseconds, and it couples dashboard
latency to batch work. The file-serving pattern mirrors how the
lakehouse gold tier already feeds DHIS2: batch produces, edge serves.

## Consequences

- A manager sees economics and model health every morning without
  asking an analyst — the loop from ADR-0014/0032 analytics to daily
  decisions is closed.
- The nightly cadence is honest: capitation P&L and drift do not move
  intra-day, so freshness is stamped on the boards (`built <timestamp>`)
  and staleness is visible, not hidden.
- New KPI sections extend the document, not the gateway: the endpoint
  stays a file read.
- Licence posture unchanged: kpiplane is stdlib; Sedona/Spark remain
  Apache-2.0 and composed, never embedded.

## Verification

- `packages/kpiplane`: 9 stdlib tests (margins ordering, panel bands,
  denials, catchment assignment, known-pair haversine, registry reads,
  drift verdicts, no-input degradation, CLI gold write).
- `services/api-gateway`: kpi_test.go pins 200/404/405 behaviour.
- `apps/dashboards/tests/kpi.test.mjs`: 11 tests pin the kobo→naira
  shaping, walk-in nulls, stale-champion flagging, no-data tolerance and
  the demo plane.
- `demo/kpiplane.sh`: fixtures → nightly build → gateway serve →
  dashboard transforms, end to end.
