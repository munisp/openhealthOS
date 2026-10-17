"""DHIS2 Web API adapter — pushes aggregate dataValueSets (technical spec:
automatic DHIS2 aggregate reporting). Sandbox mode captures payloads for
tests and offline demos; live mode POSTs to /api/dataValueSets. Aggregate
reporting is public-health data, never patient-level — enforced here:
payloads containing keys that look like patient identifiers are refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from httpclient import HTTPClient

FORBIDDEN_KEYS = {"patient", "patient_id", "nin", "name", "phone", "address"}


@dataclass
class PushResult:
    status: str          # pushed | captured | refused
    values: int = 0
    detail: str = ""


class DHIS2Adapter:
    def __init__(self, base_url: str = "", token: str = "", sandbox: bool = True):
        self.sandbox = sandbox
        self.client = None if sandbox else HTTPClient(base_url, token)
        self.captured: list[dict] = []

    @staticmethod
    def _check_aggregate(payload: dict) -> str | None:
        """Refuse anything that smells patient-level. Returns the offending
        key, or None when clean."""
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k, v in node.items():
                    if k.lower() in FORBIDDEN_KEYS:
                        return k
                    stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
        return None

    def push_data_value_set(self, payload: dict) -> PushResult:
        bad = self._check_aggregate(payload)
        if bad:
            return PushResult("refused", detail=f"patient-level key {bad!r} in aggregate payload")
        values = len(payload.get("dataValues", []))
        if values == 0:
            return PushResult("refused", detail="empty dataValues")
        if self.sandbox:
            self.captured.append(payload)
            return PushResult("captured", values)
        self.client.request("POST", "/api/dataValueSets", payload)
        return PushResult("pushed", values)
