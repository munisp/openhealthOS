"""providers.py — the LLM seam. Honest about absence.

The sandbox and the T0 edge have no model access; production may have
several. The contract is one function, and absence is a first-class
regime — agents must work (degraded, deterministic, SAYING SO) with
provider=None.
"""


class NullProvider:
    """The honest default: declares itself unavailable. Agents must
    never pretend a NullProvider completion came from a model."""
    available = False

    def complete(self, prompt, **kw):
        raise RuntimeError("no LLM provider configured — deterministic "
                           "fallback only; outputs must say llm:unavailable")


class CallableProvider:
    """Wrap any callable(prompt, **kw) -> str. Production wires real
    endpoints here (facility-local model, national inference service);
    the agents neither know nor care."""
    available = True

    def __init__(self, fn):
        self.fn = fn

    def complete(self, prompt, **kw):
        return self.fn(prompt, **kw)
