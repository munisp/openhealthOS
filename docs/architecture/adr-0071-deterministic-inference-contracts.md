# ADR-0071: Deterministic inference contracts — proven per run, never asserted by framework

Date: 2026-11-10 · Status: accepted

## Context

The estate governs clinical AI at three layers already: `aigov`
(licence classes decide where a model may run; outputs are drafts;
override rates are monitored), `edgeai` (on-device drafting through a
manifest seam), and `writegate`/`agentwrite` (no model output reaches
the record without a named human decision). What none of these pins is
the RUN: which exact artifact, under which exact decoding contract,
produced which exact output — and whether tomorrow's run reproduces
today's bitwise.

External practice makes the gap concrete. Governed GGUF registries now
advertise "zero-drift" inference via seed locking plus a mathematical
framework said to eliminate floating-point divergence by construction,
with SHA-256 digests of paired runs published as evidence. The instinct
is right; the claim shape is wrong for this platform. Asserting drift
away with a framework is a trust-me argument. The estate's own doctrine
(the checksum is the contract; fail closed; absence is not a value)
demands the stronger shape: **prove equality per run, and when equality
breaks, name the layer that moved.**

## Decision

`packages/inferlock` (inferlock@1.0.0) adds the deterministic inference
contract:

1. **Artifacts are pinned or refused.** Registration requires sha256
   (64 lowercase hex — the string is the contract, row 201), positive
   size, a format from the closed vocabulary (gguf / tflite / onnx /
   ruleset), a licence class from the (injectable) closed vocabulary,
   and a validation-evidence reference. An unpinnable artifact is
   unauditable and does not register.
2. **Contracts are data.** Seed (required, integer) + temperature +
   decoding parameters digest to a single contract hash. Unseeded runs
   are unrepeatable by construction and refused.
3. **Runs record four digests**: artifact, contract, input, output.
   `verify_rerun` compares two records in order — artifact → config →
   input → runtime — and the first layer that moved names the drift.
   Input drift means the runs were never comparable (different
   patients are not a determinism failure).
4. **Sampling cannot claim green.** temperature != 0 caps the verdict
   at amber `sampling-config` even when outputs happen to match —
   matching twice under sampling is luck, not determinism.
5. **The executor is injected.** inferlock never embeds a runtime
   (ADR-0005); production injects a subprocess seam to an external
   model server, the demo injects a pure function.
6. **Sameness is not correctness.** inferlock guarantees only that
   what was reviewed is what runs. Every draft still passes writegate
   and a named human reviewer; a deterministically wrong answer is
   still wrong.

## Consequences

- A governed GGUF triage model, an ONNX segmentation model, or the
  current rule-based edge drafter all register through the same seam,
  as data — registry entries, never code branches.
- A run whose output digest changed under an unchanged artifact,
  contract and input surfaces as red `runtime` drift with both digests
  published — the honest version of "Δ = 0": measured per run, not
  claimed by construction.
- The demo surface (inferproof) attacks all four drift layers, the
  sampling smuggle, and unpinned registration — six attacks, six named
  verdicts.
