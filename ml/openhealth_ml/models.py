"""Models (ADR-0014) — real PyTorch, CPU-first.

* FraudMLP: tabular fraud scorer over the ten engineered features.
* ClaimGCN: graph convolution over the facility↔member bipartite graph —
  two hand-rolled GCN layers (A_hat = D^-1/2 (A+I) D^-1/2), no torch_geometric
  dependency, so inference runs on any CPU including the state-plane nodes.

Both are deliberately small: the deployment targets are CPUs, the training
target is continuous retraining on lakehouse exports, and the role is
advisory (PEND with reasons — never auto-deny, per ADR-0012).
"""
from __future__ import annotations

try:
    import torch
    import torch.nn as nn
    TORCH = True
except ImportError:  # stdlib-only environments (edge, minimal CI) skip
    torch = None
    nn = object
    TORCH = False


if TORCH:

    class FraudMLP(nn.Module):
        def __init__(self, in_features: int = 10, hidden: int = 32):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_features, hidden), nn.ReLU(), nn.Dropout(0.1),
                nn.Linear(hidden, hidden), nn.ReLU(), nn.Dropout(0.1),
                nn.Linear(hidden, 1),
            )

        def forward(self, x):
            return self.net(x).squeeze(-1)  # logits

        @torch.no_grad()
        def score(self, x):
            return torch.sigmoid(self.forward(x))


    class GCNLayer(nn.Module):
        """One GCN propagation: H' = σ(D^-1/2 (A+I) D^-1/2 H W)."""

        def __init__(self, in_dim: int, out_dim: int):
            super().__init__()
            self.lin = nn.Linear(in_dim, out_dim)

        def forward(self, x, adj_norm):
            return adj_norm @ self.lin(x)


    class ClaimGCN(nn.Module):
        def __init__(self, in_features: int = 3, hidden: int = 16):
            super().__init__()
            self.g1 = GCNLayer(in_features, hidden)
            self.g2 = GCNLayer(hidden, hidden)
            self.head = nn.Linear(hidden, 1)

        @staticmethod
        def normalize_adj(adj: "torch.Tensor") -> "torch.Tensor":
            n = adj.shape[0]
            a_hat = adj + torch.eye(n)
            deg = a_hat.sum(1).clamp(min=1.0)
            d_inv_sqrt = deg.pow(-0.5)
            return d_inv_sqrt.unsqueeze(1) * a_hat * d_inv_sqrt.unsqueeze(0)

        def forward(self, x, adj_norm):
            h = torch.relu(self.g1(x, adj_norm))
            h = torch.relu(self.g2(h, adj_norm))
            return self.head(h).squeeze(-1)  # node logits

        @torch.no_grad()
        def score(self, x, adj_norm):
            return torch.sigmoid(self.forward(x, adj_norm))
