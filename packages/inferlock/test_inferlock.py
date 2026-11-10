"""test_inferlock.py — the deterministic-inference contract.

Doctrines under test:
- Registration fails closed: closed format vocabulary, canonical
  digests only (the string is the contract), positive size, licence
  class from the closed vocabulary, evidence required, duplicates
  refused.
- Contracts are data: digest-stable, seed required, sampling detected.
- Runs record four digests; rerun verification names the layer that
  moved — artifact / config / input / runtime — and never crashes on
  drift.
- Sampling configs can never claim green, even when outputs match.
- The demo surface is deterministic, self-stamped, and its drills all
  land on their named verdicts.
"""
import json
import os
import unittest

import inferlock
from inferlock import (FORMATS, INFERLOCK_VERSION, ArtifactRegistry,
                       InferenceContract, InferlockError, run,
                       verify_rerun, _canon)
import demo_inferlock

SHA_A = _canon({"artifact": "a"})
SHA_B = _canon({"artifact": "b"})


def _registry():
    reg = ArtifactRegistry()
    reg.register("m", version="1", fmt="gguf", sha256=SHA_A,
                 size_bytes=1024, licence_class="embed-clean",
                 evidence_ref="test")
    return reg


def _executor(contract, input_obj, artifact):
    return {"out": _canon({"a": artifact["sha256"],
                           "c": contract.as_dict(),
                           "i": input_obj})}


class TestRegistration(unittest.TestCase):

    def test_register_and_get(self):
        reg = _registry()
        rec = reg.get("m", "1")
        self.assertEqual(rec["sha256"], SHA_A)
        self.assertEqual(rec["fmt"], "gguf")

    def test_formats_closed(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="keras",
                         sha256=SHA_A, size_bytes=1,
                         licence_class="embed-clean",
                         evidence_ref="x")

    def test_uppercase_digest_refused_never_compared(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf",
                         sha256=SHA_A.upper(), size_bytes=1,
                         licence_class="embed-clean",
                         evidence_ref="x")

    def test_short_digest_refused(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf",
                         sha256="abcd", size_bytes=1,
                         licence_class="embed-clean",
                         evidence_ref="x")

    def test_unversioned_refused(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="", fmt="gguf", sha256=SHA_A,
                         size_bytes=1, licence_class="embed-clean",
                         evidence_ref="x")

    def test_no_evidence_refused(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError) as ctx:
            reg.register("m", version="1", fmt="gguf", sha256=SHA_A,
                         size_bytes=1, licence_class="embed-clean",
                         evidence_ref="")
        self.assertIn("audit trail", str(ctx.exception))

    def test_unknown_licence_refused(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf", sha256=SHA_A,
                         size_bytes=1, licence_class="whatever",
                         evidence_ref="x")

    def test_zero_size_refused(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf", sha256=SHA_A,
                         size_bytes=0, licence_class="embed-clean",
                         evidence_ref="x")

    def test_duplicate_refused(self):
        reg = _registry()
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf", sha256=SHA_B,
                         size_bytes=1, licence_class="embed-clean",
                         evidence_ref="x")

    def test_get_absent_is_named(self):
        reg = ArtifactRegistry()
        with self.assertRaises(InferlockError) as ctx:
            reg.get("ghost", "9")
        self.assertIn("absence is stated", str(ctx.exception))

    def test_injected_licence_vocabulary(self):
        reg = ArtifactRegistry(licence_classes=("sovereign-only",))
        with self.assertRaises(InferlockError):
            reg.register("m", version="1", fmt="gguf", sha256=SHA_A,
                         size_bytes=1, licence_class="embed-clean",
                         evidence_ref="x")


class TestContract(unittest.TestCase):

    def test_seed_required_int(self):
        with self.assertRaises(InferlockError):
            InferenceContract(seed="abc")

    def test_digest_stable_and_sensitive(self):
        a = InferenceContract(seed=1, temperature=0.0)
        b = InferenceContract(seed=1, temperature=0.0)
        c = InferenceContract(seed=2, temperature=0.0)
        self.assertEqual(a.digest(), b.digest())
        self.assertNotEqual(a.digest(), c.digest())

    def test_sampling_detected(self):
        self.assertFalse(InferenceContract(seed=1).sampling)
        self.assertTrue(
            InferenceContract(seed=1, temperature=0.7).sampling)


class TestRunAndRerun(unittest.TestCase):

    def _run(self, reg, seed=123, temp=0.0, case=None,
             executor=_executor):
        return run(reg, "m", "1",
                   contract=InferenceContract(seed=seed,
                                              temperature=temp),
                   input_obj=case or {"case": 1},
                   executor=executor, executed_at="T")

    def test_run_records_four_digests(self):
        r = self._run(_registry())
        for field in ("artifact_sha256", "contract_sha256",
                      "input_sha256", "output_sha256"):
            self.assertRegex(r[field], r"^[0-9a-f]{64}$")
        self.assertEqual(r["executed_at"], "T")   # caller's clock

    def test_identical_runs_are_green(self):
        reg = _registry()
        v = verify_rerun(self._run(reg), self._run(reg))
        self.assertEqual(v["status"], "deterministic")
        self.assertEqual(v["severity"], "green")

    def test_artifact_drift_named(self):
        reg = _registry()
        a = self._run(reg)
        b = dict(self._run(reg), artifact_sha256=SHA_B)
        v = verify_rerun(a, b)
        self.assertEqual((v["status"], v["layer"]), ("drift",
                                                     "artifact"))

    def test_config_drift_named(self):
        reg = _registry()
        v = verify_rerun(self._run(reg, seed=1), self._run(reg, seed=2))
        self.assertEqual((v["status"], v["layer"]), ("drift",
                                                     "config"))

    def test_input_drift_named(self):
        reg = _registry()
        v = verify_rerun(self._run(reg),
                         self._run(reg, case={"case": 2}))
        self.assertEqual((v["status"], v["layer"]), ("drift",
                                                     "input"))

    def test_runtime_drift_named(self):
        reg = _registry()
        def drifting(contract, input_obj, artifact):
            return {"out": "different"}
        v = verify_rerun(self._run(reg),
                         self._run(reg, executor=drifting))
        self.assertEqual((v["status"], v["layer"]), ("drift",
                                                     "runtime"))

    def test_sampling_match_is_amber_never_green(self):
        reg = _registry()
        v = verify_rerun(self._run(reg, temp=0.7),
                         self._run(reg, temp=0.7))
        self.assertEqual(v["status"], "sampling-config")
        self.assertEqual(v["severity"], "amber")


class TestDemoInferproof(unittest.TestCase):

    def test_deterministic_byte_identical(self):
        a = demo_inferlock.run_demo()
        b = demo_inferlock.run_demo()
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_stamp_contract(self):
        p = demo_inferlock.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        self.assertEqual(p["sha256"], _canon(body))
        self.assertRegex(p["sha256"], r"^[0-9a-f]{64}$")

    def test_payload_shape(self):
        p = demo_inferlock.run_demo()
        self.assertTrue(p["flags"]["control_deterministic"])
        self.assertTrue(p["flags"]["drills_named"])
        self.assertEqual(p["control"]["status"], "deterministic")
        self.assertEqual(p["control"]["run_a_output_sha256"],
                         p["control"]["run_b_output_sha256"])
        self.assertEqual([d["layer"] for d in p["drills"][:4]],
                         ["artifact", "config", "input", "runtime"])
        self.assertEqual(p["drills"][4]["verdict"], "sampling-config")
        self.assertEqual(p["drills"][5]["verdict"], "refused")
        self.assertIn("sameness_not_correctness", p["honesty"])

    def test_file_emission(self):
        demo_inferlock.main()
        out = os.path.normpath(demo_inferlock.OUT)
        with open(out) as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_inferlock.DEMO_VERSION)
        self.assertEqual(back["versions"]["inferlock"],
                         INFERLOCK_VERSION)


if __name__ == "__main__":
    unittest.main()
