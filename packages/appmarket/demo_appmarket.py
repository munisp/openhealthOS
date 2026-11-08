"""appmarket demo — deterministic seed + checksum-stamped payload.

Three synthetic publishers, four installs, live tokens, and one
revocation — because a marketplace demo that shows no revocation is
hiding the feature that matters most.
"""

from __future__ import annotations

import hashlib
import json

from appmarket import AppRegistry, SCOPES

DEMO_VERSION = "appmarket-demo@1.0.0"


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def seed_appmarket():
    reg = AppRegistry()
    reg.register_app("labco-sim", publisher="LabCo Sim", version="2.1.0",
                     scopes=["system/ServiceRequest.read",
                             "system/DiagnosticReport.write"],
                     redirect_uris=["https://labco.example/cb"],
                     description="Lab orders in, results back.", day=0)
    reg.register_app("payer-analytics-sim", publisher="PayerSim",
                     version="1.4.0", scopes=["bulk/export.aggregate"],
                     redirect_uris=["https://payersim.example/cb"],
                     description="Cohort-level actuarial extracts.", day=0)
    reg.register_app("patient-viewer-sim", publisher="ViewSim",
                     version="0.9.2", scopes=["patient/IPS.read"],
                     redirect_uris=["https://viewsim.example/cb"],
                     description="Patient-consented record viewer.", day=1)

    reg.install("labco-sim", facility="phc-sim-ikeja", by="admin-sim-a",
                approved_by="admin-sim-b", day=1)
    reg.install("labco-sim", facility="phc-sim-yaba", by="admin-sim-a",
                approved_by="admin-sim-b", day=1)
    reg.install("payer-analytics-sim", facility="state-sim-lagos",
                by="admin-sim-c", approved_by="admin-sim-a", day=2)
    reg.install("patient-viewer-sim", facility="phc-sim-ikeja",
                by="admin-sim-a", approved_by="admin-sim-c", day=2)

    tok_lab = reg.launch("labco-sim", facility="phc-sim-ikeja", day=3,
                         expires_day=33)
    tok_payer = reg.launch("payer-analytics-sim", facility="state-sim-lagos",
                           day=3, expires_day=10)
    tok_patient = reg.launch("patient-viewer-sim", facility="phc-sim-ikeja",
                             day=3, expires_day=33,
                             consent_ref="consent-sim-001")

    # the honest beat: one publisher gets revoked, its tokens die
    reg.revoke("patient-viewer-sim", facility="phc-sim-ikeja",
               by="admin-sim-c",
               reason="publisher failed annual security attestation",
               day=5)

    return {"registry": reg,
            "tokens": {"lab": tok_lab, "payer": tok_payer,
                       "patient": tok_patient}}


def appmarket_payload(book: dict) -> dict:
    reg = book["registry"]
    apps = [dict(a) for a in reg.apps.values()]
    installs = [dict(i) for i in reg.installs.values()]
    tokens = [dict(t) for t in reg.tokens.values()]
    for t in tokens:
        t["expired_by_now"] = False   # demo time is relative; state is the truth
    return _stamp({
        "version": DEMO_VERSION,
        "scope_catalogue": sorted(SCOPES),
        "apps": apps,
        "installs": installs,
        "tokens": tokens,
        "report": reg.report(),
    })


def export_appmarket(book: dict, out_path: str) -> str:
    with open(out_path, "w") as fh:
        json.dump(appmarket_payload(book), fh, indent=2,
                  ensure_ascii=False)
    return out_path
