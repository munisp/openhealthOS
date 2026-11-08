"""Export the deployment sync-rules.yaml from a seeded syncmesh registry.

The YAML PowerSync consumes is a *build artifact* of the ratified
registry — nobody hand-edits sync rules, they are proposed, four-eyes
ratified, and only then emitted. This script seeds the reference
deployment registry (every default entity class, production scopes)
and writes the artifact.

Usage (from the repo root):

    python3 packages/syncmesh/export_powersync.py

Deterministic: same registry in, same bytes out.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from syncmesh import VERSION, SyncRuleRegistry, emit_powersync_yaml

# The reference deployment: one ratified rule per entity class, with
# the scopes a national programme actually runs. Scopes are evaluated
# server-side — a device never receives a row outside its bucket.
DEPLOYMENT_RULES = [
    # rule_id, entity_class, bucket, scopes, retention
    ("r-ward-observations", "observation", "ward_observations",
     ["facility", "role"], "rolling-90d"),
    ("r-ward-flowsheets", "flowsheet-row", "ward_flowsheets",
     ["facility"], "rolling-30d"),
    ("r-ward-emar", "emar-administration", "ward_emar",
     ["facility", "role"], "episode"),
    ("r-pharmacy-orders", "medication-order", "pharmacy_orders",
     ["facility", "role"], "rolling-90d"),
    ("r-pharmacy-rx", "prescription", "pharmacy_rx",
     ["facility", "role"], "rolling-90d"),
    ("r-teleconsulta-status", "teleconsulta-status",
     "teleconsulta_status", ["facility"], "rolling-90d"),
    ("r-referral-status", "referral-status", "referral_status",
     ["facility"], "rolling-90d"),
    ("r-clinical-certs", "certificate", "clinical_certificates",
     ["facility", "role"], "episode"),
    ("r-panel-recalls", "panel-recall", "panel_recalls",
     ["facility", "panel"], "rolling-90d"),
    ("r-triage-calls", "triage-call", "triage_calls",
     ["facility", "role"], "rolling-30d"),
]

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "infra", "powersync",
                        "sync-rules.yaml")


def build_registry():
    reg = SyncRuleRegistry()
    for rule_id, entity_class, bucket, scopes, retention in \
            DEPLOYMENT_RULES:
        reg.propose(rule_id, {"entity_class": entity_class,
                              "bucket": bucket, "scopes": scopes,
                              "retention": retention,
                              "rule_version": 1},
                    by="cmio", day=1)
        reg.ratify(rule_id, by="cnio", day=1)  # four-eyes, never self
    return reg


def main():
    reg = build_registry()
    yaml_text = emit_powersync_yaml(reg)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(yaml_text)
    print(f"syncmesh {VERSION}: wrote {OUT_PATH} "
          f"({len(DEPLOYMENT_RULES)} ratified rules)")


if __name__ == "__main__":
    main()
