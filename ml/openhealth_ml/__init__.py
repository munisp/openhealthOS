"""openhealth_ml — the platform's ML/DL/GNN stack (ADR-0014).

Deliberately the one place the stdlib-only Python doctrine is broken:
PyTorch is a hard requirement for training and inference here, and that
break is documented and fenced (models.TORCH guard) so stdlib-only
packages never accidentally import it.

Modules:
    data        synthetic-calibrated generator + lakehouse gold reader
    features    tabular feature engineering + bipartite graph builder
    models      FraudMLP (tabular DL) + ClaimGCN (hand-rolled GNN)
    train       training loops with honest metrics, early stopping
    infer       CPU inference loading the registry champion
    registry    file-based model registry (git-committable)
    monitor     drift detection (PSI) + score tracking
    ab          champion/challenger deterministic traffic split
    continuous  continuous retraining driven by data-hash change
    ray_train   optional Ray distributed hyper-parameter search
"""
__version__ = "0.1.0"
