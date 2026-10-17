"""Shared HTTP plumbing for external adapters (Apache-2.0 SDK side).

One client, three rules: bounded timeouts (a national API being down must
never hang a clinic workflow), limited retries with backoff, and JSON in /
JSON out. Adapters subclass or compose this; sandbox modes never touch it.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class AdapterError(Exception):
    """Upstream call failed after retries — callers degrade, never hang."""


class HTTPClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 8.0,
                 retries: int = 2, backoff: float = 0.25):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.backoff * attempt)
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:  # 4xx is an answer, don't retry
                detail = e.read().decode(errors="replace")[:300]
                raise AdapterError(f"{method} {path} -> {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
        raise AdapterError(f"{method} {path} failed after {self.retries + 1} attempts: {last}")
