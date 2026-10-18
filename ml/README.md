# openhealth-ml — the platform's ML/DL/GNN stack

Real PyTorch models, real training loops, shipped weights, CPU inference,
a file-based registry, drift monitoring, champion/challenger A/B, and
continuous retraining on lakehouse data. Governing decision: **ADR-0014**.

This directory is the one deliberate exception to the platform's
stdlib-only Python doctrine: PyTorch is required for training and
inference. The break is fenced (`models.TORCH` guard) so nothing else in
the platform can import torch by accident, and every module that can be
stdlib-only (`registry`, `monitor`, `ab`) is.

## What is in the box

| Piece | Module | What it actually is |
|---|---|---|
| Fraud scorer (DL) | `models.FraudMLP` | 3-layer MLP over 10 engineered features |
| Fraud-ring detector (GNN) | `models.ClaimGCN` | 2-layer GCN over the facility↔member bipartite graph, hand-rolled (`A_hat = D^-1/2 (A+I) D^-1/2`), no `torch_geometric` |
| Training | `train.py` | Adam, class-weighted BCE, early stopping, holdout AUC-ROC/PR-AUC computed without sklearn |
| Data | `data.py` | lakehouse gold reader (production path) + calibrated Nigerian synthetic generator with 4 labelled fraud archetypes (ghost / upcoding / velocity / duplicate) |
| Registry | `registry.py` | file-based, git-committable: `ml/registry/<name>/v<N>/{weights.pt,metrics.json,card.json}` + a `champion` pointer |
| Inference | `infer.py` | `ChampionScorer` / `FacilityGNNScorer`, CPU-only (`map_location="cpu"`), heuristic fallback when no champion exists |
| Monitoring | `monitor.py` | per-feature PSI drift (stdlib-only — runs on the state plane) + score-window tracking |
| A/B | `ab.py` | deterministic champion/challenger split by `sha256(claim_id)`; challenger never decides outcomes |
| Continuous training | `continuous.py` | retrains only when `dataset_hash` changes; promotes only when holdout AUC beats the champion |
| Scale-out | `ray_train.py` | optional Ray hyper-parameter search; serial CPU fallback when Ray is absent |

## Quickstart

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu   # training/inference nodes only

# train + register + promote-if-better (synthetic bootstrap)
PYTHONPATH=ml python3 -m openhealth_ml.train --model fraud-mlp --epochs 40 --promote
PYTHONPATH=ml python3 -m openhealth_ml.train --model claim-gcn --epochs 60 --promote

# CPU inference with the champion
PYTHONPATH=ml python3 -m openhealth_ml.infer --claims 200
PYTHONPATH=ml python3 -m openhealth_ml.infer --gnn          # facility audit ranking

# continuous training (production path: reads lakehouse/gold/claims.jsonl)
PYTHONPATH=ml python3 -m openhealth_ml.continuous --once --model fraud-mlp

# drift report between two lakehouse exports (no torch needed)
PYTHONPATH=ml python3 -m openhealth_ml.monitor

# optional distributed hyper-parameter search
PYTHONPATH=ml python3 -m openhealth_ml.ray_train             # serial CPU fallback
```

## Tests

```bash
python3 -m unittest discover -s ml/tests -v          # stdlib lane (torch tests skip)
PYTHONPATH=/path/to/torch:ml python3 -m unittest discover -s ml/tests -v
```

## Honest limitations

* **Synthetic labels are known because we injected them.** The generator is
  calibrated to Nigerian PHC reality (scheme mix, malaria-dominated codes,
  PHC tariff band, NHIA fraud archetypes), but real-world validation needs
  adjudicated production claims. The pipeline is schema-identical so those
  drop in unchanged via `lakehouse/gold/claims.jsonl`.
* **Scores are advisory.** A high score produces a PEND recommendation with
  reasons for a human adjudicator — never an auto-deny (ADR-0012).
* **Shipped weights are bootstrap champions.** They exist so a fresh
  deployment scores above heuristic from day one; `continuous.py` replaces
  them with production-trained versions as lakehouse data accrues.
