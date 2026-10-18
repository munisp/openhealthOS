"""Model registry (ADR-0014) — file-based, git-committable, MLflow-shaped.

Each model version is a directory:

    ml/registry/<name>/v<N>/weights.pt    — state_dict
    ml/registry/<name>/v<N>/metrics.json  — holdout metrics from training
    ml/registry/<name>/v<N>/card.json     — data hash, archetype rates, notes
    ml/registry/<name>/champion           — file containing "v<N>"

The registry is deliberately boring files: the state plane runs without a
tracking server, git is the audit trail, and an MLflow server can adopt the
same layout later without code change. Promotion is explicit and only ever
forward on holdout metric — continuous.py enforces that.
"""
from __future__ import annotations

import json
import os
import shutil


def _dir(root: str, name: str) -> str:
    return os.path.join(root, name)


def versions(root: str, name: str) -> list[str]:
    d = _dir(root, name)
    if not os.path.isdir(d):
        return []
    return sorted((v for v in os.listdir(d) if v.startswith("v")),
                  key=lambda v: int(v[1:]))


def next_version(root: str, name: str) -> str:
    vs = versions(root, name)
    return f"v{(int(vs[-1][1:]) + 1) if vs else 1}"


def register(root: str, name: str, weights_src: str, metrics: dict, card: dict) -> str:
    v = next_version(root, name)
    dest = os.path.join(_dir(root, name), v)
    os.makedirs(dest)
    shutil.copyfile(weights_src, os.path.join(dest, "weights.pt"))
    with open(os.path.join(dest, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, sort_keys=True)
    with open(os.path.join(dest, "card.json"), "w") as f:
        json.dump(card, f, indent=2, sort_keys=True)
    return v


def champion(root: str, name: str) -> str | None:
    try:
        with open(os.path.join(_dir(root, name), "champion")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def promote(root: str, name: str, version: str) -> None:
    if version not in versions(root, name):
        raise ValueError(f"unknown version {version}")
    with open(os.path.join(_dir(root, name), "champion"), "w") as f:
        f.write(version)


def load_champion(root: str, name: str):
    """Load the champion state_dict + metadata. Returns None if unregistered."""
    v = champion(root, name)
    if v is None:
        return None
    d = os.path.join(_dir(root, name), v)
    meta = {}
    for fn in ("metrics.json", "card.json"):
        with open(os.path.join(d, fn)) as f:
            meta[fn[:-5]] = json.load(f)
    return {"version": v, "weights_path": os.path.join(d, "weights.pt"), **meta}
