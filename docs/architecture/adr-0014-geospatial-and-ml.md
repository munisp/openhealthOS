# ADR-0014: Geospatial platform and the real ML/DL/GNN stack

Status: Accepted
Date: 2026-10-18

## Context

Two platform capabilities were specified at world-class level:

1. **Geospatial** — facility mapping with capacity profiles (beds, doctors,
   nurses, amenities), "facilities near me", and navigation, with Cesium,
   Apache Sedona, and GeoLibre named as candidate technologies.
2. **Machine learning** — the user asked directly: is the AI/ML/DL/GNN
   real? Are weights shipped? Are there training and fine-tuning scripts?
   Can it run on CPU? The honest answer before this ADR was: rules only,
   no weights, no training pipeline, no registry, no monitoring, no A/B —
   validated against nothing.

Both had to be answered within the platform's hard constraints: offline-first
on $100 Android phones over 2G, CPU-only state-plane nodes, the three-language
doctrine (Go services, Rust ledger, stdlib-only Python), and the licence
doctrine (no BSL/SSPL/Elastic-licensed embedding, ever).

## Decision 1 — Geospatial: reject CesiumJS for the read path; haversine in
the registry; offline canvas map; OS-level navigation

**CesiumJS is rejected for this platform.** It is a WebGL globe engine
(~1MB+ of JS, GPU context, tile CDN). On the actual deployment target —
low-RAM Android WebViews over intermittent 2G, frequently offline — it is
the wrong tool in every dimension: payload, GPU requirement, and network
assumption. We do not ship technology for the demo screenshot; we ship for
the ward nurse in Kaura LGA.

**Apache Sedona is rejected for the read path** and noted for the batch
path. Sedona is Spark-tier geospatial analytics: the right answer when the
lakehouse runs national batch geospatial joins (facility catchment
modelling, outbreak heat surfaces). It is not a service-side dependency —
we do not run a Spark cluster to answer "what is within 25km of me". The
read path is a haversine index in the registry service
(`services/registry-service/geo.go`), O(n) over a facilities table that is
realistically ≤50k rows — microseconds. When the lakehouse analytics tier
lands, Sedona (or GeoPandas at smaller scale) is the designated batch
option; `packages/lakehouse` is the integration seam.

**GeoLibre** (opengeos) is tracked as the future tile-serving seam if and
when the state plane needs hosted basemaps for desktop operations staff.
The phone apps never consume tiles.

What ships instead:

- **Registry capacity profile** — `Facility` gains `beds`, `doctors`,
  `nurses`, `amenities[]` (pharmacy|lab|maternity|xray|ambulance|oxygen|
  cold-chain), `phone`, `hours`; migration `0007_facility_capacity_geo.sql`
  plus a geo index. This is the data every downstream decision needs:
  maps, referral routing, catchment planning.
- **`GET /facilities/near`** — haversine (WGS-84 mean radius 6371.0088km),
  validated lat/lng (400 on malformed), `radius_km`/`radius` alias capped
  at 500, `limit` capped at 500, `level` and `amenity` filters, unplaced
  facilities (0,0 sentinel) never routable. Postgres and in-memory stores
  behave identically (store-level tests).
- **Offline canvas map** (`apps/dashboards/js/map.js`) — self-contained
  canvas renderer over a bundled Natural Earth 1:50m Nigeria boundary
  (public domain, RDP-simplified to 127 points / 2.2KB). No tiles, no CDN,
  no WebGL; precached by the service worker; works with zero connectivity.
  Markers are level-coloured (PHC/clinic/hospital) and sized by beds; tap
  opens the full capacity profile.
- **Dashboards `#/map` view** — map + capacity summary + facility detail
  (beds/doctors/nurses/amenity chips/phone/hours) + "facilities near me"
  (browser geolocation → client-side haversine over the cached list, so it
  works offline) + level/amenity filters + nearest-alternative routing.
- **Patient app near-me** — server-first (`/api/registry/facilities/near`),
  offline-honest fallback to client-side haversine over the last cached
  facility list; i18n in all five product languages.
- **Navigation is handed to the OS maps app** via `geo:` deep links.
  Turn-by-turn navigation with offline maps, voice guidance, and transport
  modes is a solved problem on the phone; re-implementing it inside a
  health record would be worse on every axis. We answer *which facility,
  how far, what does it have*; the maps app answers *how do I get there*.

## Decision 2 — ML/DL/GNN: real PyTorch models, shipped weights, CPU
inference, honest continuous training

The `ml/` tree is the platform's **one deliberate exception** to the
stdlib-only Python doctrine: PyTorch is a hard dependency for training and
inference. The break is fenced behind `models.TORCH` so nothing else can
import torch by accident, and every module that can be stdlib-only
(`registry`, `monitor`, `ab`) is — monitoring runs on the state plane
where torch is not installed.

What ships:

- **FraudMLP** — a 3-layer MLP (hidden 32, dropout 0.1) over ten engineered
  claim features chosen to catch the four NHIA-documented fraud archetypes
  (ghost billing, upcoding, facility velocity abuse, duplicate billing).
  Trained with Adam, class-weighted BCE, early stopping on holdout AUC.
- **ClaimGCN** — a two-layer graph convolution over the facility↔member
  bipartite claim graph, hand-rolled (`A_hat = D^-1/2 (A+I) D^-1/2`), no
  `torch_geometric` dependency, so inference runs on any CPU. Node features
  are **structural only** — an early version carried a per-facility
  fraud-rate feature; that is the label in disguise (target leakage) and
  was removed. Facility-level supervision answers the payer's real
  question: *which facility do I audit?*
- **File-based model registry** (`ml/registry/`) — `weights.pt` +
  `metrics.json` + `card.json` per version, a `champion` pointer, git as
  the audit trail. MLflow-shaped by layout so a tracking server can adopt
  it later without code change; no server required today.
- **Shipped bootstrap champions** — real trained weights committed to the
  repo: fraud-mlp v2 (holdout AUC-ROC 0.823, PR-AUC 0.49 at 6% prevalence)
  and claim-gcn v2 (holdout AUC-ROC 0.994 on a 120-facility graph). A fresh
  deployment scores above heuristic from day one.
- **CPU-only inference** (`infer.py`) — `map_location="cpu"`, no CUDA ever
  assumed; heuristic fallback when no champion exists so a bare deployment
  degrades to rules, never to errors.
- **Training loops** (`train.py`) — honest metrics (rank-based AUC-ROC and
  trapezoid PR-AUC computed without sklearn), stratified facility splits
  for the GCN, deterministic seeds.
- **Data pipeline** (`data.py`) — production path reads the lakehouse gold
  export (`lakehouse/gold/claims.jsonl`); bootstrap path is a generator
  calibrated to Nigerian PHC reality (BHCPF/state scheme mix,
  malaria-dominated code distribution, PHC tariff band) with four labelled
  fraud archetypes **concentrated in bad-actor facilities** — the
  real-world ring pattern, and what makes facility-level supervision a
  meaningful two-class task. Labels are honest: known because injected.
- **Continuous training** (`continuous.py`) — retrains only when
  `dataset_hash` changes; promotes only when the challenger's holdout AUC
  beats the champion's. The champion is never replaced by a worse model.
- **Drift monitoring** (`monitor.py`) — per-feature PSI (stdlib-only):
  ≥0.25 significant (retrain), 0.1–0.25 watch, plus score-window tracking
  (mean/max/PEND-rate).
- **Champion/challenger A/B** (`ab.py`) — deterministic arm assignment by
  `sha256(claim_id)`, stable across nodes with no coordination; the
  challenger never decides outcomes.
- **Ray scale-out** (`ray_train.py`) — optional; hyper-parameter search
  parallelises across a Ray cluster when present, serial CPU fallback when
  not. The models are deliberately small — one CPU trains them in minutes;
  Ray exists for the multi-state/partitioned future, not because today
  needs it.
- **frauddetect bridge** (`bridge.py` + the detector's `ml_scorer` seam) —
  the rules engine gains an ML advisory: `ML_FRAUD_SCORE` reason with model
  version, score raises triage priority, ML never clears what rules pended
  and never denies. frauddetect itself never imports torch; ML failure
  (any exception) is invisible to adjudication.

### Deployment doctrine (unchanged, restated)

ML output is **advisory**: a high score produces a PEND recommendation with
human-readable reasons for a human adjudicator — never an auto-deny
(ADR-0012). This is both an ethics position and a regulatory one (NDPA
2023 automated-decision provisions).

## Consequences

- `ml/` is the only tree where `pip install torch` is required, and only
  on training/inference nodes. CI has two lanes: stdlib (torch tests skip)
  and `ml-torch` (CPU wheel; includes registry-integrity — shipped
  champions must load and score on CPU).
- Facility capacity data becomes a registry write-path concern: sync agents
  and LGA hubs should populate it; the map is only as honest as the
  registry.
- The geospatial read path adds no services, no databases, no licences.
- The synthetic generator's calibration (archetype rates, tariff table)
  lives in `data.py` and is expected to drift toward production
  distributions as lakehouse exports accumulate — `continuous.py` handles
  that transition automatically, and `monitor.py` watches for it.

## Honest limitations (stated to the user, recorded here)

- Bootstrap champions are trained on calibrated synthetic data. Real-world
  validation requires adjudicated production claims; the pipeline is
  schema-identical so those drop in unchanged.
- The GCN's discrimination comes from ring structure (shared ghost
  members, velocity degree); single-actor upcoding without ring structure
  is the MLP's job, not the GNN's.
- The offline map is a schematic boundary render, not a basemap — by
  design, and stated in the UI.
