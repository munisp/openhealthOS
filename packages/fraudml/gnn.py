"""Collusion-ring detection with a from-scratch graph convolutional network.

Clean-room, stdlib-only. Built because every production-grade graph engine we
evaluated (FalkorDB -> SSPLv1, Neo4j -> GPLv3/commercial dual) carries a
license this platform's doctrine forbids embedding, so the graph analytics
layer is re-implemented here per ADR-0005 (compose, don't fork) and the
clean-room rule.

Model: 2-layer GCN (Kipf & Welling style) with symmetric normalised
adjacency, ReLU, softmax, full-batch gradient descent on cross-entropy.
For graphs too large to train on, label_propagation() gives a cheap
semi-supervised fallback with the same node-scoring contract.

Node types are homogeneous-ised: facilities, providers and patients are all
nodes; edges carry a weight (claim volume, shared biometric device, shared
bank account, ...). A node's fraud probability after training is its ring
score — high scores mean the node sits inside a neighbourhood of known-bad
actors, which is exactly how facility/provider collusion rings (ADR-0018)
present topologically.
"""

import math
import random


def _matmul(a, b):
    n, k, m = len(a), len(b), len(b[0])
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        ai = a[i]
        oi = out[i]
        for t in range(k):
            v = ai[t]
            if v == 0.0:
                continue
            bt = b[t]
            for j in range(m):
                oi[j] += v * bt[j]
    return out


def _transpose(a):
    return [list(col) for col in zip(*a)]


class Graph:
    """Undirected weighted graph with per-node feature vectors."""

    def __init__(self, node_ids, features):
        self.node_ids = list(node_ids)
        self.index = {nid: i for i, nid in enumerate(self.node_ids)}
        n = len(self.node_ids)
        if len(features) != n or any(len(f) != len(features[0]) for f in features):
            raise ValueError("features must be n x d with one row per node")
        self.features = [list(map(float, row)) for row in features]
        self.adj = [[0.0] * n for _ in range(n)]

    def add_edge(self, a, b, weight=1.0):
        if a not in self.index or b not in self.index:
            raise ValueError("both endpoints must be graph nodes")
        ia, ib = self.index[a], self.index[b]
        if ia == ib:
            raise ValueError("self edges are implicit (added at normalisation)")
        if weight <= 0:
            raise ValueError("edge weight must be positive")
        self.adj[ia][ib] += weight
        self.adj[ib][ia] += weight

    def normalized_adjacency(self):
        """D^-1/2 (A + I) D^-1/2 — the GCN propagation matrix."""
        n = len(self.adj)
        a_hat = [row[:] for row in self.adj]
        for i in range(n):
            a_hat[i][i] += 1.0
        deg = [sum(row) for row in a_hat]
        inv_sqrt = [1.0 / math.sqrt(d) if d > 0 else 0.0 for d in deg]
        return [
            [a_hat[i][j] * inv_sqrt[i] * inv_sqrt[j] for j in range(n)]
            for i in range(n)
        ]

    def neighbours(self, node_id):
        i = self.index[node_id]
        return [(self.node_ids[j], self.adj[i][j]) for j in range(len(self.adj)) if self.adj[i][j] > 0]


class GCN:
    """Two-layer GCN trained full-batch on a subset of labelled nodes."""

    def __init__(self, graph, hidden=8, seed=42, lr=0.2, epochs=200, l2=1e-4):
        self.graph = graph
        self.hidden = hidden
        self.lr = lr
        self.epochs = epochs
        self.l2 = l2
        rng = random.Random(seed)
        d_in = len(graph.features[0])
        scale1 = math.sqrt(2.0 / d_in)
        scale2 = math.sqrt(2.0 / hidden)
        self.w1 = [[rng.gauss(0, scale1) for _ in range(hidden)] for _ in range(d_in)]
        self.w2 = [[rng.gauss(0, scale2) for _ in range(2)] for _ in range(hidden)]
        self.a_norm = graph.normalized_adjacency()

    def _forward(self):
        x = self.graph.features
        z1 = _matmul(self.a_norm, _matmul(x, self.w1))
        h1 = [[v if v > 0 else 0.0 for v in row] for row in z1]
        z2 = _matmul(self.a_norm, _matmul(h1, self.w2))
        # softmax
        probs = []
        for row in z2:
            mx = max(row)
            exps = [math.exp(v - mx) for v in row]
            s = sum(exps)
            probs.append([e / s for e in exps])
        return z1, h1, probs

    def fit(self, labels):
        """labels: {node_id: 0|1} for the supervised subset."""
        idx = [self.graph.index[nid] for nid in labels]
        y = [int(labels[nid]) for nid in labels]
        if len(set(y)) < 2:
            raise ValueError("need both classes in the labelled set")
        n = len(self.graph.node_ids)
        d_in = len(self.graph.features[0])

        for _ in range(self.epochs):
            z1, h1, probs = self._forward()
            # dL/dz2 on labelled rows only (softmax + cross-entropy)
            dz2 = [[0.0, 0.0] for _ in range(n)]
            for i, yi in zip(idx, y):
                dz2[i][0] = probs[i][0] - (1.0 if yi == 0 else 0.0)
                dz2[i][1] = probs[i][1] - (1.0 if yi == 1 else 0.0)
            # grads for w2: (A_norm h1)^T dz2 / m  + l2
            ah1 = _matmul(self.a_norm, h1)
            m = float(len(idx))
            gw2 = _matmul(_transpose(ah1), dz2)
            for r in range(self.hidden):
                for c in range(2):
                    gw2[r][c] = gw2[r][c] / m + self.l2 * self.w2[r][c]
            # backprop to h1 -> z1
            dh1 = _matmul(_matmul(self.a_norm, dz2), _transpose(self.w2))
            dz1 = [[dh1[i][j] if z1[i][j] > 0 else 0.0 for j in range(self.hidden)]
                   for i in range(n)]
            ax = _matmul(self.a_norm, self.graph.features)
            gw1 = _matmul(_transpose(ax), dz1)
            for r in range(d_in):
                for c in range(self.hidden):
                    gw1[r][c] = gw1[r][c] / m + self.l2 * self.w1[r][c]
            for r in range(self.hidden):
                for c in range(2):
                    self.w2[r][c] -= self.lr * gw2[r][c]
            for r in range(d_in):
                for c in range(self.hidden):
                    self.w1[r][c] -= self.lr * gw1[r][c]
        return self

    def fraud_probability(self, node_id):
        _, _, probs = self._forward()
        return probs[self.graph.index[node_id]][1]

    def ring_scores(self):
        """{node_id: fraud probability} for every node, sorted desc."""
        _, _, probs = self._forward()
        scores = {nid: probs[i][1] for i, nid in enumerate(self.graph.node_ids)}
        return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))


def label_propagation(graph, labels, iterations=50, alpha=0.85):
    """Large-graph fallback: diffuse labels over the normalised adjacency.

    Returns the same {node_id: score} contract as GCN.ring_scores().
    """
    n = len(graph.node_ids)
    a = graph.normalized_adjacency()
    y0 = [0.5] * n
    labelled = {}
    for nid, lab in labels.items():
        i = graph.index[nid]
        y0[i] = float(lab)
        labelled[i] = float(lab)
    f = y0[:]
    for _ in range(iterations):
        af = [sum(a[i][j] * f[j] for j in range(n)) for i in range(n)]
        f = [alpha * af[i] + (1 - alpha) * y0[i] for i in range(n)]
        for i, v in labelled.items():  # clamp supervision
            f[i] = v
    return {nid: f[i] for i, nid in enumerate(graph.node_ids)}
