"""NIMC identity adapter — NIN verification for registry linkage
(product spec: NIN binding via NIMC where available, progressive enrolment
otherwise). Sandbox mode is deterministic and offline; live mode calls the
NIMC verification API. Same interface either way, so edge facilities with
no connectivity still enrol — verification just defers.
"""
from __future__ import annotations

from dataclasses import dataclass

from httpclient import HTTPClient


@dataclass
class Verification:
    nin: str
    status: str          # verified | no_match | deferred | invalid_format
    name: str = ""
    detail: str = ""


class NIMCAdapter:
    """NIN verification. sandbox=True: in-memory registry (tests, offline
    demos). Live mode: NIMC REST API behind HTTPClient."""

    def __init__(self, base_url: str = "", token: str = "", sandbox: bool = True):
        self.sandbox = sandbox
        self.client = None if sandbox else HTTPClient(base_url, token)
        # sandbox registry: nin -> (name, dob) — fixture identities
        self._registry: dict[str, tuple[str, str]] = {}

    def register_fixture(self, nin: str, name: str, dob: str) -> None:
        """Seed the sandbox registry (tests and demos)."""
        self._registry[nin] = (name, dob)

    @staticmethod
    def valid_format(nin: str) -> bool:
        return nin.isdigit() and len(nin) == 11

    def verify(self, nin: str, dob: str = "") -> Verification:
        if not self.valid_format(nin):
            return Verification(nin, "invalid_format", detail="NIN is 11 digits")
        if self.sandbox:
            rec = self._registry.get(nin)
            if rec is None:
                return Verification(nin, "deferred",
                                    detail="not in sandbox registry — verify when online")
            name, stored_dob = rec
            if dob and dob != stored_dob:
                return Verification(nin, "no_match", name=name, detail="date of birth mismatch")
            return Verification(nin, "verified", name=name)
        resp = self.client.request("POST", "/verify", {"nin": nin, "dob": dob})
        status = "verified" if resp.get("match") else "no_match"
        return Verification(nin, status, name=resp.get("name", ""))
