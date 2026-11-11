"""inferproof-demo@1.0.0 — the deterministic-inference evidence surface.

Answers "can we trust the model's answer to be the same answer
tomorrow?" the only honest way: pin the artifact, pin the contract,
run twice, compare four digests, and name the layer if anything moved.
The drills attack every layer: a swapped artifact, a changed seed, a
changed input, simulated floating-point drift, a sampling config
claiming determinism, and an unpinned model trying to register.

The executor is a deterministic pure function standing in for an
external runtime — the seam is the real one; the machinery exercised
(registry, contracts, rerun verification) is the production code.

Same contract as every demo exporter: fixed clock, registry order,
byte-identical reruns, compact-contract sha256 stamp.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

from inferlock import (INFERLOCK_VERSION, ArtifactRegistry,
                       InferenceContract, InferlockError, run,
                       verify_rerun, _canon)

DEMO_VERSION = "inferproof-demo@1.0.0"
CLOCK = lambda: "2026-11-10T12:00:00Z"

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "inferproof.json")

# Fixed synthetic artifact digests — the checksum is the contract, so
# the demo pins its own fixtures as 64-hex strings.
_TRIAGE_SHA = _canon({"fixture": "triage-gguf-bytes", "n": 1})
_SEG_SHA = _canon({"fixture": "segmentation-onnx-bytes", "n": 1})
_TRIAGE_SHA_V2 = _canon({"fixture": "triage-gguf-bytes", "n": 2})

_CASE = {"case_id": "SIM-0001",
         "presentation": ["fever", "lethargy", "localized inflammation"],
         "note": "synthetic fixture — never a real patient"}


def _executor(contract, input_obj, artifact):
    """Deterministic stand-in for an external inference runtime:
    output is a pure function of artifact + contract + input. A real
    deployment injects a subprocess call to the model server through
    this exact seam."""
    material = {"artifact": artifact["sha256"],
                "contract": contract.as_dict(), "input": input_obj}
    digest = _canon(material)
    return {"assessment": "synthetic draft — immune-response "
                          "differential, allergy/autoimmune/toxicology "
                          "workup suggested",
            "confidence_bp": 8000,
            "trace": digest}


def _drifting_executor(contract, input_obj, artifact):
    """Simulates floating-point drift across a heterogeneous handoff:
    same everything, one bit of difference in the trace."""
    out = _executor(contract, input_obj, artifact)
    out["trace"] = out["trace"][:-1] + ("0" if out["trace"][-1] != "0"
                                        else "1")
    return out


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**payload, "sha256": digest}


def _row(name, verdict, note):
    return {"attack": name, "verdict": verdict["status"],
            "severity": verdict["severity"],
            "layer": verdict.get("layer"),
            "note": note}


def run_demo():
    reg = ArtifactRegistry()
    triage = reg.register(
        "synthetic-triage", version="1.0.0", fmt="gguf",
        sha256=_TRIAGE_SHA, size_bytes=4_100_000_000,
        licence_class="embed-clean",
        evidence_ref="demo fixture — registration contract only")
    seg = reg.register(
        "synthetic-segmentation", version="2.1.0", fmt="onnx",
        sha256=_SEG_SHA, size_bytes=180_000_000,
        licence_class="embed-clean",
        evidence_ref="demo fixture — registration contract only")

    contract = InferenceContract(seed=123, temperature=0.0,
                                 params={"n_gpu_layers": -1})

    # The control: same artifact, same contract, same input — twice.
    a = run(reg, "synthetic-triage", "1.0.0", contract=contract,
            input_obj=_CASE, executor=_executor,
            executed_at=CLOCK())
    b = run(reg, "synthetic-triage", "1.0.0", contract=contract,
            input_obj=_CASE, executor=_executor,
            executed_at=CLOCK())
    control = verify_rerun(a, b)

    drills = []

    # 1. The artifact moved.
    drifted = dict(b, artifact_sha256=_TRIAGE_SHA_V2)
    v = verify_rerun(a, drifted)
    drills.append(_row(
        "model artifact swapped between runs", v,
        "the layer is named: artifact — the weights you audited are "
        "not the weights that ran"))

    # 2. The contract moved.
    other_contract = InferenceContract(seed=124, temperature=0.0,
                                       params={"n_gpu_layers": -1})
    c = run(reg, "synthetic-triage", "1.0.0", contract=other_contract,
            input_obj=_CASE, executor=_executor,
            executed_at=CLOCK())
    v = verify_rerun(a, c)
    drills.append(_row(
        "seed changed between runs", v,
        "the layer is named: config — a different seed is a different "
        "run, stated not merged"))

    # 3. The input moved.
    d = run(reg, "synthetic-triage", "1.0.0", contract=contract,
            input_obj={**_CASE, "case_id": "SIM-0002"},
            executor=_executor, executed_at=CLOCK())
    v = verify_rerun(a, d)
    drills.append(_row(
        "input changed between runs", v,
        "the layer is named: input — runs over different patients are "
        "not comparable"))

    # 4. Nothing moved but the output did — runtime drift.
    e = run(reg, "synthetic-triage", "1.0.0", contract=contract,
            input_obj=_CASE, executor=_drifting_executor,
            executed_at=CLOCK())
    v = verify_rerun(a, e)
    drills.append(_row(
        "floating-point drift across a hybrid handoff", v,
        "same artifact, contract and input, different output — the "
        "gate names runtime drift instead of asserting a framework "
        "made it impossible"))

    # 5. Sampling cannot claim determinism.
    sampling = InferenceContract(seed=123, temperature=0.7,
                                 params={"n_gpu_layers": -1})
    f = run(reg, "synthetic-triage", "1.0.0", contract=sampling,
            input_obj=_CASE, executor=_executor,
            executed_at=CLOCK())
    g = run(reg, "synthetic-triage", "1.0.0", contract=sampling,
            input_obj=_CASE, executor=_executor,
            executed_at=CLOCK())
    v = verify_rerun(f, g)
    drills.append(_row(
        "temperature 0.7 presented as deterministic", v,
        "outputs matched by luck under sampling — amber, never green"))

    # 6. An unpinned model tries to register.
    try:
        reg.register("rogue-model", version="0.1", fmt="gguf",
                     sha256="not-a-digest", size_bytes=1,
                     licence_class="embed-clean",
                     evidence_ref="none")
        verdict, severity, note = ("accepted", "green",
                                   "an unpinned model registered — "
                                   "the gate is broken")
    except InferlockError as exc:
        verdict, severity, note = "refused", "red", str(exc)
    drills.append({"attack": "model with no checksum tries to "
                             "register",
                   "verdict": verdict, "severity": severity,
                   "layer": None, "note": note})

    payload = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK(),
        "registry": {"artifacts": [triage, seg]},
        "control": {"status": control["status"],
                    "severity": control["severity"],
                    "run_a_output_sha256": a["output_sha256"],
                    "run_b_output_sha256": b["output_sha256"],
                    "contract_sha256": a["contract_sha256"],
                    "sampling": a["sampling"]},
        "drills": drills,
        "flags": {
            "control_deterministic":
                control["status"] == "deterministic",
            "drills_named": all(
                d["severity"] in ("red", "amber") for d in drills),
        },
        "honesty": {
            "sameness_not_correctness":
                "determinism proves the answer is the SAME, not that "
                "it is RIGHT — a deterministically wrong answer is "
                "still wrong; the clinical gate remains writegate plus "
                "a named human reviewer",
            "proven_not_asserted":
                "this platform does not claim a mathematical framework "
                "that eliminates drift by construction; it proves "
                "bitwise equality per run and names the layer that "
                "moved when equality breaks",
            "executor_seam":
                "the demo's model is a pure function; production "
                "injects an external runtime through the same seam — "
                "no runtime is ever embedded (ADR-0005)",
        },
        "versions": {"inferlock": INFERLOCK_VERSION,
                     "demo": DEMO_VERSION},
    }
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    payloadio.write_text(out, json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")


if __name__ == "__main__":
    main()
