"""silver_hook.py — the scrubber the lakehouse silver gate calls (ADR-0065).

One seam, two implementations behind it:
- today: deterministic Nigerian pattern de-id (deid.deidentify_note);
- when the OpenMed sidecar is live: same signature, REST-backed detection.
lakehouse.silver(scrub=make_scrubber()) enforces the doctrine: no free
text crosses into the analytical plane raw.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from deid import deidentify_note  # noqa: E402


def make_scrubber(method="mask", min_score=0.5):
    """Return scrub(text) -> de-identified text."""
    def scrub(text: str) -> str:
        return deidentify_note(text, method=method, min_score=min_score)["text"]
    return scrub
