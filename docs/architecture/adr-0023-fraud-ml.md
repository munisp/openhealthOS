# ADR-0023: Graph neural nets, signal fusion, and MCMC for anti-fraud — clean-room, stdlib-only

Status: accepted
Date: 2026-10-19

## Context

ADR-0018 added deterministic anti-collusion controls. Deterministic rules catch
known patterns; they do not catch rings that arrange themselves to stay under
each rule's threshold. The platform needed three capabilities the ecosystem
mostly sells under licences our doctrine forbids (FalkorDB is SSPLv1; Neo4j is
GPLv3/commercial; TigerGraph is proprietary):

1. **Relational evidence.** Collusion is topological: a facility, a provider,
   and a rotating cast of "patients" form a dense cluster in the claims graph.
2. **Calibrated fusion.** A dozen detectors each emit a weak 0..1 signal; the
   adjudication queue needs one calibrated probability and an explanation.
3. **Honest facility comparisons.** A rural facility with 3 claims and 1 flag
   (33%!) must not outrank a 500-claim facility with a sustained 30% flag rate.

## Decision

New package `packages/fraudml/`, stdlib-only Python, three components plus a
pipeline:

- **`gnn.py`** — a from-scratch two-layer GCN (symmetric-normalised adjacency,
  ReLU, softmax, full-batch gradient descent with analytic backprop) over the
  facility–provider–patient claims graph. Trained on adjudicated labels it
  scores every node's ring membership; `label_propagation()` is the large-graph
  / cold-start fallback with the same scoring contract. Unlabelled nodes are
  flagged purely by neighbourhood — the test suite proves a facility with no
  label is flagged when its neighbours are dirty.
- **`fraudfusion.py`** — logistic fusion of per-detector signals
  (rule engine, GNN ring score, duplicate biometrics, stock divergence, MCMC
  rate, tariff outlier) into one probability with per-signal logit
  contributions for auditor explainability. Verdict bands: `<0.5` clear,
  `0.5–0.8` review, `≥0.8` auto-pend. `fit()` refits weights on labelled
  adjudication outcomes.
- **`mcmc.py`** — a two-level hierarchical Beta-Binomial model
  (`theta_i ~ Beta(mu·kappa, (1−mu)·kappa)`) sampled with component-wise
  random-walk Metropolis–Hastings in logit/log space. Partial pooling shrinks
  small-sample facilities toward the national rate; a facility is flagged only
  when the *lower* credible-interval bound exceeds tolerance. Deterministic
  under a fixed seed so every number an auditor cites is reproducible.
- **`pipeline.py`** — builds the graph from the claims export, computes ring
  scores, runs the MCMC facility report, and emits fused per-claim verdicts.

## Why not embed an existing engine

| Candidate | Licence | Verdict |
|---|---|---|
| FalkorDB (RedisGraph successor) | SSPLv1 | Forbidden — re-implemented here |
| Neo4j | GPLv3 / commercial | Forbidden as an embedded dependency |
| PyTorch Geometric / DGL | permissive, but torch-scale | Overweight; the ml/ fence is reserved for clinical models |

The claims graphs at state scale (tens of thousands of nodes) are well inside
what a stdlib GCN handles in seconds; label propagation covers the national
scale-out path.

## Consequences

- Fraud adjudication gains relational, calibrated, and statistically honest
  evidence with zero new runtime dependencies and zero licence exposure.
- MCMC runs are seeded; reproduction is `python3 -m unittest` away.
- If a future state deployment outgrows in-process analytics, the contract
  (`ring_scores`, `facility_report`, `fuse`) maps onto any permissively
  licensed graph store without changing callers.
