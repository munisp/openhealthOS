"""reference_adapter_dermengine.py — the reference composed adapter.

One vendor bridge, fully implemented, as the TEMPLATE every other
composed adapter follows. The transport is an injected HTTP callable
(http(method, path, json) -> dict) — at deployment it wraps the
vendor's REST client; in tests it is a fake. Nothing proprietary
ships; the SHAPE ships.

DermEngine-shaped: lesions upload, analysis results download.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicehub import DeviceHubError  # noqa: E402

REFERENCE_ADAPTER_VERSION = "ref-dermengine-bridge@1.0.0"


def dermengine_transport(http):
    """Build the ("ingest"|"push", payload) callable DeviceHub expects.

    No http callable → refuse: a composed adapter without its
    deployment transport is a phishing page with a logo."""
    if http is None:
        raise DeviceHubError("dermengine bridge: no http transport — "
                             "wire the vendor REST client at "
                             "deployment; nothing proprietary ships "
                             "here")

    def transport(direction, payload):
        if direction == "ingest":
            # payload: {"case_ref": ...} → fetch analysis results
            case_ref = (payload or {}).get("case_ref")
            if not case_ref:
                raise DeviceHubError("ingest needs a case_ref — a "
                                     "result pull about nothing pulls "
                                     "nothing")
            data = http("GET", f"/v1/cases/{case_ref}/results")
            observations = []
            for r in data.get("results", []):
                observations.append({
                    "code": r["parameter"], "value": str(r["value"]),
                    "units": r.get("units", ""),
                    "timestamp": r.get("captured_at", ""),
                    "panel": case_ref})
            if not observations:
                raise DeviceHubError("vendor returned no results — "
                                     "absence is not a value")
            return {"message_id": data.get("message_id", case_ref),
                    "patient_id": data.get("patient_ref"),
                    "observations": observations}
        if direction == "push":
            # payload: {message_id, patient_id, observations, panel}
            if not payload.get("observations"):
                raise DeviceHubError("pushing an empty result set — "
                                     "absence is not a value")
            resp = http("POST", "/v1/cases", {
                "patient_ref": payload["patient_id"],
                "panel": payload.get("panel"),
                "results": [{"parameter": o["code"],
                             "value": o["value"],
                             "units": o.get("units", "")}
                            for o in payload["observations"]]})
            if resp.get("status") not in ("accepted", "created", 200,
                                          201):
                raise DeviceHubError(
                    f"vendor refused the push: {resp.get('status')} — "
                    "an unconfirmed push did not happen")
            return {"vendor_ref": resp.get("case_ref"),
                    "status": "pushed"}
        raise DeviceHubError(f"unknown direction {direction!r} — the "
                             "bridge speaks ingest and push")

    transport.adapter_version = REFERENCE_ADAPTER_VERSION
    return transport
