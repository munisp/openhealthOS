"""inferlock@1.0.0 — deterministic inference contracts for clinical AI.

The estate already governs models as registry entries (aigov: licence
classes, draft-only outputs, override monitoring) and gates their writes
(writegate + agentwrite). What it never pinned is the RUN: which exact
artifact, under which exact decoding contract, produced which exact
output — and whether a re-run reproduces it bitwise.

Doctrines enforced here:

- A model you cannot checksum is a model you cannot audit. Every
  artifact is pinned at registration by sha256 + size + format.
  The stamp contract applies to the STRING (row 201): an uppercase or
  short digest is refused at the door, never compared.
- The checksum is the contract — extended from payloads (proofindex)
  to RUNS. Every run records four digests as data: artifact, contract
  (seed + decoding parameters), input, output.
- We do not ASSERT zero-drift; we PROVE it per run, and when it breaks
  we name the layer that moved — artifact / config / runtime. Drift is
  a verdict, never a crash, never silent. A framework that claims drift
  away by construction is marketing; a gate that detects and names it
  is engineering.
- Determinism proves sameness, not correctness: a deterministically
  wrong answer is still wrong. inferlock guarantees only that what was
  reviewed is what runs — the clinical gate remains writegate plus a
  named human reviewer, and inferlock never weakens it.
- Sampling is stated, never smuggled: a contract with temperature != 0
  cannot claim determinism; matching digests under a sampling config
  earn amber "sampling-config", not green.
- Registry entries, never code branches (country/company-agnostic
  doctrine, applied to models): a governed GGUF triage model and an
  ONNX segmentation model register through the same seam, as data.
- The executor is INJECTED. This module never embeds a runtime
  (ADR-0005): the real executor shells out to an external process; the
  demo injects a deterministic pure function.

Stdlib only (ADR-0005). No clocks are read here — the caller stamps
time.
"""

from __future__ import annotations

import hashlib
import json

INFERLOCK_VERSION = "inferlock@1.0.0"

#: Artifact formats the registry recognises. Closed on purpose.
FORMATS = ("gguf", "tflite", "onnx", "ruleset")

#: The layers at which drift is detected and named, in check order.
#: "input" drift means the two runs were never comparable.
DRIFT_LAYERS = ("artifact", "config", "input", "runtime")

#: Default licence vocabulary, mirroring aigov's classes. Callers that
#: compose with packages/aigov should inject aigov.LICENCE_CLASSES.
DEFAULT_LICENCE_CLASSES = ("embed-clean", "compose-only",
                           "research-only")

_HEX = frozenset("0123456789abcdef")


class InferlockError(ValueError):
    """A malformed registration or contract. Fail closed, always named."""


def _canon(obj) -> str:
    """Compact canonical digest — the estate's primary contract."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _check_digest(value, *, field) -> None:
    """The string is the contract: 64 lowercase ASCII hex, or refused
    before any comparison can happen."""
    if (not isinstance(value, str) or not value.isascii()
            or len(value) != 64
            or any(c not in _HEX for c in value)):
        raise InferlockError(
            "%s must be 64 lowercase hex — an unpinnable artifact is "
            "unauditable" % field)


class ArtifactRegistry:
    """Governed model artifacts, as data. A model that is not pinned
    here cannot produce a clinical draft anywhere in the estate."""

    def __init__(self, *, licence_classes=DEFAULT_LICENCE_CLASSES):
        self.licence_classes = tuple(licence_classes)
        self.artifacts = {}     # (model_id, version) -> record

    def register(self, model_id, *, version, fmt, sha256, size_bytes,
                 licence_class, evidence_ref):
        if not isinstance(model_id, str) or not model_id:
            raise InferlockError("model_id must be a non-empty string")
        if not version:
            raise InferlockError(
                "unversioned models are unauditable")
        if fmt not in FORMATS:
            raise InferlockError(
                "unknown artifact format %r — the vocabulary is closed"
                % (fmt,))
        _check_digest(sha256, field="artifact sha256")
        if not isinstance(size_bytes, int) or size_bytes <= 0:
            raise InferlockError(
                "size_bytes must be a positive integer — absence is "
                "not a value")
        if licence_class not in self.licence_classes:
            raise InferlockError(
                "unknown licence class %r — licence decides WHERE a "
                "model may run" % (licence_class,))
        if not evidence_ref:
            raise InferlockError(
                "no validation evidence — a model without an audit "
                "trail does not touch patients, not even as a draft")
        key = (model_id, version)
        if key in self.artifacts:
            raise InferlockError(
                "duplicate artifact: %s@%s — a registry, not a "
                "rewrite" % key)
        rec = {"model_id": model_id, "version": version, "fmt": fmt,
               "sha256": sha256, "size_bytes": size_bytes,
               "licence_class": licence_class,
               "evidence_ref": evidence_ref}
        self.artifacts[key] = rec
        return dict(rec)

    def get(self, model_id, version):
        rec = self.artifacts.get((model_id, version))
        if rec is None:
            raise InferlockError(
                "unregistered artifact %s@%s — absence is stated, "
                "never faked" % (model_id, version))
        return dict(rec)


class InferenceContract:
    """Seed + decoding parameters, as data. The contract is digestible:
    two runs claim determinism only when this digest matches AND the
    config is non-sampling."""

    def __init__(self, *, seed, temperature=0.0, params=None):
        if not isinstance(seed, int):
            raise InferlockError(
                "seed must be an integer — an unseeded run is "
                "unrepeatable by construction")
        if not isinstance(temperature, (int, float)):
            raise InferlockError("temperature must be numeric")
        self.seed = seed
        self.temperature = float(temperature)
        self.params = dict(params or {})

    @property
    def sampling(self):
        return self.temperature != 0.0

    def as_dict(self):
        return {"seed": self.seed, "temperature": self.temperature,
                "params": self.params}

    def digest(self):
        return _canon(self.as_dict())


def run(registry, model_id, version, *, contract, input_obj, executor,
        executed_at):
    """Execute one inference under contract. The executor is injected:
    executor(contract, input_obj, artifact) -> output_obj. The returned
    record carries four digests — artifact, contract, input, output —
    so a re-run can be checked layer by layer."""
    artifact = registry.get(model_id, version)
    output = executor(contract, input_obj, artifact)
    return {
        "model_id": model_id, "version": version,
        "artifact_sha256": artifact["sha256"],
        "contract_sha256": contract.digest(),
        "sampling": contract.sampling,
        "input_sha256": _canon(input_obj),
        "output_sha256": _canon(output),
        "executed_at": executed_at,
    }


def verify_rerun(a: dict, b: dict) -> dict:
    """Compare two run records layer by layer. The first layer that
    moved names the drift; if nothing moved, the runs are bitwise
    identical. Sampling configs can never claim green — matching twice
    under temperature>0 is luck, stated as amber."""
    if a["artifact_sha256"] != b["artifact_sha256"]:
        return {"status": "drift", "severity": "red",
                "layer": "artifact",
                "detail": "the model artifact changed between runs",
                "a": a["artifact_sha256"], "b": b["artifact_sha256"]}
    if a["contract_sha256"] != b["contract_sha256"]:
        return {"status": "drift", "severity": "red",
                "layer": "config",
                "detail": "seed or decoding parameters changed "
                          "between runs",
                "a": a["contract_sha256"], "b": b["contract_sha256"]}
    if a["input_sha256"] != b["input_sha256"]:
        return {"status": "drift", "severity": "red",
                "layer": "input",
                "detail": "the INPUT changed between runs — the runs "
                          "are not comparable",
                "a": a["input_sha256"], "b": b["input_sha256"]}
    if a["output_sha256"] != b["output_sha256"]:
        return {"status": "drift", "severity": "red",
                "layer": "runtime",
                "detail": "same artifact, same contract, same input — "
                          "different output: floating-point or runtime "
                          "drift, named not asserted away",
                "a": a["output_sha256"], "b": b["output_sha256"]}
    if a["sampling"] or b["sampling"]:
        return {"status": "sampling-config", "severity": "amber",
                "detail": "outputs matched, but temperature != 0 — "
                          "matching twice under sampling is luck, not "
                          "determinism"}
    return {"status": "deterministic", "severity": "green",
            "detail": "bitwise-identical output under a pinned "
                      "artifact and a non-sampling contract"}
