"""appmarket — third-party app marketplace: manifests, scoped installs,
consent-gated launch.

The physician/integrator ecosystem trail: an EMR grows an ecosystem
when third parties can build on it safely. ohsdk is the wire protocol
(SMART Backend Services, least-privilege scopes); this package is the
governance layer above it — who may publish, what an app may touch,
which facilities installed it, and when access is revoked.

Doctrine:
- DECLARED SCOPES ARE THE CONTRACT: an app lists its scopes at publish;
  check_access refuses anything undeclared — a scope you didn't declare
  is a scope you don't have.
- FOUR-EYES ON INSTALL: the hand that proposes an install never
  approves it; the same hand pattern as tariff books and order sets.
- PATIENT DATA IS CONSENT-GATED: patient-scoped launch requires a
  consent reference; absence is not consent.
- REVOCATION IS INSTANT AND AUDITED: a revoked install answers refuse,
  never stale-yes.
"""

from __future__ import annotations

import hashlib
import json

APPMARKET_VERSION = "appmarket@1.0.0"


class AppMarketError(Exception):
    pass


def _refuse(msg):
    raise AppMarketError(msg)


# The scope catalogue is an entry set — a new integration surface adds
# an entry, never an if-scope branch. Mirrors the ohsdk least-privilege
# classes plus patient-facing scopes.
SCOPES = {
    "system/Claim.read",
    "system/Claim.write",
    "system/Coverage.read",
    "system/ServiceRequest.read",
    "system/DiagnosticReport.write",
    "system/MedicationRequest.read",
    "system/MedicationDispense.write",
    "system/DocumentReference.read",
    "patient/IPS.read",          # patient summary — consent-gated
    "bulk/export.aggregate",     # cohort-level only, consent-scoped
}

PATIENT_SCOPES = {s for s in SCOPES if s.startswith("patient/")}


class AppRegistry:
    def __init__(self):
        self.apps = {}             # app_id -> manifest
        self.installs = {}         # (app_id, facility) -> install
        self.tokens = {}           # token_id -> launch grant
        self.audit = []

    # -- publish -----------------------------------------------------
    def register_app(self, app_id, *, publisher, version, scopes,
                     redirect_uris, description, day):
        if not app_id or not publisher or not version:
            _refuse("an app names itself, its publisher, its version")
        if app_id in self.apps:
            _refuse("duplicate app id — publish a new version")
        scopes = set(scopes)
        unknown = scopes - SCOPES
        if unknown:
            _refuse(f"undeclared scopes {sorted(unknown)} — the "
                    "catalogue is the contract")
        if not scopes:
            _refuse("an app with no scopes is a pamphlet, not an app")
        for uri in redirect_uris:
            if not uri.startswith("https://"):
                _refuse("redirect URIs are TLS or they do not travel")
        self.apps[app_id] = {
            "app_id": app_id, "publisher": publisher, "version": version,
            "scopes": sorted(scopes),
            "redirect_uris": list(redirect_uris),
            "description": description, "registered_day": day,
        }
        self.audit.append({"event": "app_registered", "app": app_id,
                           "publisher": publisher, "day": day})
        return dict(self.apps[app_id])

    # -- install (four-eyes) ------------------------------------------
    def install(self, app_id, *, facility, by, approved_by, day):
        app = self.apps.get(app_id)
        if app is None:
            _refuse("unknown app — install from the catalogue, not from "
                    "a side channel")
        if not facility:
            _refuse("an install names its facility")
        if not by or not approved_by or by == approved_by:
            _refuse("the hand that proposes never approves")
        key = (app_id, facility)
        existing = self.installs.get(key)
        if existing and existing["state"] == "active":
            _refuse("already installed")
        self.installs[key] = {
            "app_id": app_id, "facility": facility, "state": "active",
            "proposed_by": by, "approved_by": approved_by, "day": day,
        }
        self.audit.append({"event": "app_installed", "app": app_id,
                           "facility": facility, "by": by,
                           "approved_by": approved_by, "day": day})
        return dict(self.installs[key])

    def revoke(self, app_id, *, facility, by, reason, day):
        inst = self.installs.get((app_id, facility))
        if inst is None or inst["state"] != "active":
            _refuse("nothing active to revoke")
        if not reason:
            _refuse("a revocation states its reason")
        inst["state"] = "revoked"
        inst["revoked_by"] = by
        inst["revoked_day"] = day
        inst["revoke_reason"] = reason
        # every outstanding token for this install dies with it
        for t in self.tokens.values():
            if t["app_id"] == app_id and t["facility"] == facility \
                    and t["state"] == "active":
                t["state"] = "revoked"
        self.audit.append({"event": "app_revoked", "app": app_id,
                           "facility": facility, "by": by,
                           "reason": reason, "day": day})
        return dict(inst)

    # -- launch --------------------------------------------------------
    def launch(self, app_id, *, facility, day, expires_day,
               consent_ref=None):
        """Issue a launch grant. Patient scopes need a consent
        reference — absence is not consent."""
        app = self.apps.get(app_id)
        if app is None:
            _refuse("unknown app")
        inst = self.installs.get((app_id, facility))
        if inst is None or inst["state"] != "active":
            _refuse("not installed here — a token without an install is "
                    "a key without a door")
        if expires_day <= day:
            _refuse("a grant that starts expired is not a grant")
        if set(app["scopes"]) & PATIENT_SCOPES and not consent_ref:
            _refuse("patient-scoped launch needs a consent reference — "
                    "absence is not consent")
        token_id = "tok_" + hashlib.sha256(
            f"{app_id}{facility}{day}{len(self.tokens)}".encode()
        ).hexdigest()[:16]
        grant = {"token_id": token_id, "app_id": app_id,
                 "facility": facility, "scopes": list(app["scopes"]),
                 "issued_day": day, "expires_day": expires_day,
                 "consent_ref": consent_ref, "state": "active"}
        self.tokens[token_id] = grant
        self.audit.append({"event": "launch_issued", "app": app_id,
                           "facility": facility, "token": token_id,
                           "day": day})
        return dict(grant)

    # -- access check ---------------------------------------------------
    def check_access(self, token_id, *, scope, day):
        t = self.tokens.get(token_id)
        if t is None:
            _refuse("unknown token")
        if t["state"] != "active":
            _refuse("token revoked — revocation is instant, never "
                    "stale-yes")
        if day > t["expires_day"]:
            _refuse("token expired")
        if scope not in SCOPES:
            _refuse(f"unknown scope {scope!r}")
        if scope not in t["scopes"]:
            _refuse("scope not granted — a scope you didn't declare is "
                    "a scope you don't have")
        return {"allowed": True, "app_id": t["app_id"],
                "facility": t["facility"], "scope": scope}

    def report(self):
        active = sum(1 for i in self.installs.values()
                     if i["state"] == "active")
        return {"version": APPMARKET_VERSION, "apps": len(self.apps),
                "installs_active": active,
                "tokens_active": sum(1 for t in self.tokens.values()
                                     if t["state"] == "active"),
                "audit_events": len(self.audit)}
