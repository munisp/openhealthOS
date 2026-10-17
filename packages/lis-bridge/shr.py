"""SHR push client — lab results land in the shared health record as FHIR
Observations (the clinical loop's lab leg). Stdlib only.

Care never blocks on the SHR: a push failure is reported in the result
envelope (`shr_pushed: false`) and the observation remains queued for the
next sync pass — the record converges, it never loses results.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def push_observation(
    base_url: str,
    observation: dict,
    *,
    patient_id: str,
    facility_id: str,
    timeout: float = 3.0,
) -> None:
    """PUT one FHIR Observation into the SHR with provenance.

    Mirrors the shr-service contract (resourceType/id/patient_id/
    source_facility envelope, X-Facility-ID header for the consent gate).
    The same envelope is what a Medplum-backed foundation accepts via its
    FHIR REST endpoint — the seam swap changes the base URL, not the code.
    """
    if observation.get("resourceType") != "Observation" or not observation.get("id"):
        raise ValueError("observation must be a FHIR Observation with an id")
    envelope = {
        "resourceType": "Observation",
        "id": observation["id"],
        "patient_id": patient_id,
        "source_facility": facility_id,
        "body": observation,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/fhir/Observation/{observation['id']}",
        data=json.dumps(envelope).encode(),
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "X-Facility-ID": facility_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 300:
                raise ConnectionError(f"shr push: http {resp.status}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"shr push: {e}") from e
