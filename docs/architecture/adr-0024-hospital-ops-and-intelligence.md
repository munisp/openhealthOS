# ADR-0024: Hospital operations, conversational voice, offline planning & intelligence

Status: accepted
Date: 2026-10-19

## Context

A gap review identified four missing hospital scenarios (inpatient/ADT,
theatre, laboratory workflow, billing) and evaluated seven external projects
for adoption. Licence doctrine ruled on each first: anything BSL/SSPL-class
is re-implemented clean-room, never embedded; permissively licensed value is
composed, not forked (ADR-0005).

| Project | Licence | Outcome |
|---|---|---|
| AVA voice agent (Asterisk) | MIT (Core) | Composed: on-prem STT/TTS edge feeding our voice seam |
| Metriport | AGPLv3 (core) | Not embedded; two patterns re-implemented clean-room |
| FalkorDB | SSPLv1 | Forbidden; graph analytics re-implemented (ADR-0023) |
| RustGIS | Apache-2.0 | Companion tool; we emit GeoJSON it opens |
| pg_vectorsearch | PostgreSQL License | Optional SQL accelerator for the guideline index |
| CocoIndex | Apache-2.0 | Pattern adopted: content-hash incremental indexing |
| osmus layercake | CC0 / ODbL | Road extracts for real-network ETA |
| EPR-KGQA | unverifiable | Rejected — unverifiable licence is not adoptable |

## Decision

**Hospital operations (four new stdlib packages):**
- `packages/inpatient` — ADT state machine + bed management. Beds and
  episodes can never drift: admission occupies, transfer sends the vacated
  bed to cleaning, discharge closes the episode. Infection-control bed
  closure is a first-class state.
- `packages/theatre` — OR day-lists with emergency bumping (an emergency
  inserts ahead of electives, because that is what theatres do), and the
  WHO surgical safety checklist as a hard gate: no sign-in, no start; no
  sign-out, no close.
- `packages/lis` — lab order → sample → result → verify. No result without
  a collected sample; four-eyes verification (the verifier is never the
  entrant); critical results (potassium, glucose, Hb, platelets) require
  clinician acknowledgement with latency measured as a quality measure.
- `packages/billing` — itemized bills in integer kobo, deposits, waivers,
  exemption categories (under-5 / pregnant / elderly / indigent) that zero
  eligible lines, and insurer splits that feed the adjudicator.

**Conversational voice (extends ADR-0022's seam):**
`POST /webhooks/voice/converse` — AVA/Asterisk runs STT/TTS at the edge in
the caller's language; the platform side stays deterministic (same keyword
triage protocol as every channel), because an emergency line must be
auditable and must work with zero cloud dependency.

**Planning & intelligence:**
- `packages/routing` — road-graph ETA (A*, per-edge road-class speeds) from
  layercake OSM extracts; replaces the haversine×1.4 heuristic where a roads
  extract has been imported.
- `packages/gisexport` — RFC 7946 GeoJSON layers (facilities, household
  density, response-time heat, coverage gaps) with k-anonymity (k=5) on all
  patient-derived layers, for offline analysis in RustGIS/QGIS.
- `packages/docclean` — SimHash near-duplicate detection (the same letter
  via WhatsApp, PWA, and scan is one document) and FHIR code→plain-text
  enrichment with a bundled ICD-10/surveillance dictionary.
- `packages/semsearch` — offline TF-IDF guideline search for CHW/paramedic
  apps; content-hash incremental reindexing (CocoIndex's freshness trick);
  `pg_ddl()`/`export_for_pg()` give the optional pg_vectorsearch accelerator
  path with hashes matching the offline store.

## Consequences

- The full inpatient episode — admission to discharge summary, theatre,
  labs, bill — is now representable. Remaining honest gaps: HR/payroll
  (deliberately composed via ERPNext, not built), MultiLineString road
  extracts need upstream splitting, and the pg_vectorsearch path is an
  accelerator — the offline index stays the source of truth.
- Two new demo scripts: `demo/hospital.sh`, `demo/planning.sh`.
