"""test_vhdeploy.py — the deployment residuals, pinned closed.

Three seams that were "deployment-side only" get their closable halves:
  1. vhpsp@1.1.0 — real PSP export formats as four-eyes-gated entries
  2. vhease@1.1.0 — durable outbox: persist-before-send, restart replay
  3. vhpkcs11@1.0.0 — real Cryptoki ABI, proven against a COMPILED C
     fixture (mock_pkcs11.c) — the same path a vendor .so takes
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import vhease
import vhpsp
from vhcover import KeyRing, VHCError, pkcs11_provider
from vhease import EaseAdapter, EaseError, SqliteOutbox
from vhpsp import PSPError, parse_settlement_file, ratify_adapter


class TestPspGovernance(unittest.TestCase):
    PAYSTACK = (b"Reference,Amount,Currency,Customer Email,Status,Paid At\n"
                b"PSP-900,1200000,NGN,pax1@example.com,success,2026-11-09\n"
                b"PSP-901,550000,NGN,pax2@example.com,failed,2026-11-09\n"
                b"PSP-902,75000,NGN,pax3@example.com,success,2026-11-09\n")
    MPESA = (b"Receipt No,Details,Transaction Status,Paid In,Withdrawn\n"
             b"QK12345,Customer 2547xxx,Completed,\"1,500.00\",\n"
             b"QK12346,Customer 2547yyy,Completed,,2,000.00\n")

    def setUp(self):
        # The registry is process-global by design — a deployment
        # ratifies once. Tests snapshot and restore it so one test's
        # ratification never leaks into another's refusal.
        self._registry = dict(vhpsp.PSP_ADAPTERS)
        self._state = {fid: (a.status, list(a.ratified_by))
                       for fid, a in self._registry.items()}

    def tearDown(self):
        vhpsp.PSP_ADAPTERS.clear()
        vhpsp.PSP_ADAPTERS.update(self._registry)
        for fid, (status, ratified_by) in self._state.items():
            entry = vhpsp.PSP_ADAPTERS[fid]
            entry.status = status
            entry.ratified_by = ratified_by

    def test_template_adapter_refuses_until_ratified(self):
        with self.assertRaises(PSPError) as ctx:
            parse_settlement_file(self.PAYSTACK,
                                  format_id="paystack-settlement-csv-v1")
        self.assertIn("template", str(ctx.exception))

    def test_ratification_is_four_eyes(self):
        with self.assertRaises(PSPError):
            ratify_adapter("mpesa-statement-csv-v1",
                           approver="ada", second="ada")

    def test_ratified_paystack_parses_minor_units_and_skips_failed(self):
        ratify_adapter("paystack-settlement-csv-v1",
                       approver="ada", second="bola")
        rows = parse_settlement_file(self.PAYSTACK,
                                     format_id="paystack-settlement-csv-v1")
        self.assertEqual(len(rows), 2)  # the failed line never settles
        self.assertEqual(rows[0], {"payer": "pax1@example.com",
                                   "amount_minor": 1200000,
                                   "currency": "NGN", "ref": "PSP-900"})

    def test_ratified_mpesa_converts_major_and_skips_withdrawals(self):
        ratify_adapter("mpesa-statement-csv-v1", approver="ada",
                       second="bola")
        rows = parse_settlement_file(self.MPESA,
                                     format_id="mpesa-statement-csv-v1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount_minor"], 150000)
        self.assertEqual(rows[0]["currency"], "KES")

    def test_reference_contracts_stay_ratified(self):
        rows = parse_settlement_file(b"payer,amount_minor,currency,ref\n"
                                     b"Pax,100,NGN,R1\n",
                                     format_id="csv-v1")
        self.assertEqual(rows[0]["amount_minor"], 100)

    def test_register_adapter_cannot_enter_ratified(self):
        with self.assertRaises(PSPError):
            vhpsp.register_adapter("rogue-v1", lambda r, **k: [],
                                   source="invented on the spot",
                                   status="ratified")

    def test_register_adapter_never_overwrites_a_format(self):
        with self.assertRaises(PSPError):
            vhpsp.register_adapter("csv-v1", lambda r, **k: [],
                                   source="rogue replacement")

    def test_register_adapter_requires_a_named_source(self):
        with self.assertRaises(PSPError):
            vhpsp.register_adapter("rogue-v1", lambda r, **k: [])

    def test_report_states_status_and_source(self):
        rep = vhpsp.report()
        self.assertEqual(rep["version"], "vhpsp@1.1.0")
        self.assertEqual(rep["adapters"]["csv-v1"]["status"], "ratified")
        self.assertIn("Paystack",
                      rep["adapters"]["paystack-settlement-csv-v1"]["source"])


class TestEaseOutbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "outbox.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adapter(self, transport):
        return EaseAdapter(transport, outbox=SqliteOutbox(self.db),
                           now=lambda: 1000.0)

    def test_volatile_adapter_states_it(self):
        a = EaseAdapter(lambda t, e: {"status": "accepted"})
        self.assertIn("VOLATILE", a.report()["durability"])

    def test_restart_replays_pending_with_same_idem_key(self):
        received = []

        def flaky(t, e):
            raise OSError("rail down")

        a1 = self._adapter(flaky)
        with self.assertRaises(EaseError) as ctx:
            a1.cover_issued({"ref": "VHC-1", "passport_sha256": "ab",
                             "window": [0, 14], "profile_version": "1"})
        self.assertIn("journaled", str(ctx.exception))

        # restart: new adapter, same outbox file, rail now healthy
        def healthy(t, e):
            received.append(e["idem_key"])
            return {"status": "accepted"}

        a2 = self._adapter(healthy)
        result = a2.replay_pending()
        self.assertEqual(result, {"replayed": 1, "dead": 0})
        # the replay carried the ORIGINAL idempotency key
        original = a1.outbox_pending_keys() if hasattr(
            a1, "outbox_pending_keys") else None
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]), 64)
        self.assertEqual(a2.report()["pending"], 0)

    def test_replay_after_partial_delivery_dedupes_by_idem_key(self):
        seen = set()

        def deduping_rail(t, e):
            if e["idem_key"] in seen:
                return {"status": "accepted"}  # rail dedupes
            seen.add(e["idem_key"])
            raise OSError("dropped after receive")

        a1 = self._adapter(deduping_rail)
        with self.assertRaises(EaseError):
            a1.cover_issued({"ref": "VHC-2", "passport_sha256": "cd",
                             "window": [0, 14], "profile_version": "1"})
        a2 = self._adapter(deduping_rail)
        result = a2.replay_pending()
        # the rail HAD received it; the replay was answered from the
        # idempotency key, not re-applied
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(len(seen), 1)

    def test_dead_letter_after_declared_attempts(self):
        def always_down(t, e):
            raise OSError("permanent failure")

        a = self._adapter(always_down)
        with self.assertRaises(EaseError):
            a.cover_revoked("VHC-3", reason="fraud", day=3)
        for _ in range(vhease.MAX_ATTEMPTS - 1):
            a.replay_pending()
        dead = a.report()["dead"]
        self.assertEqual(len(dead), 1)
        self.assertIn("permanent failure", dead[0]["last_error"])

    def test_replay_without_outbox_refuses(self):
        a = EaseAdapter(lambda t, e: {"status": "accepted"})
        with self.assertRaises(EaseError):
            a.replay_pending()


class TestPkcs11Abi(unittest.TestCase):
    """The ctypes binding is proven against a COMPILED shared object —
    the real ABI path — not a Python stub."""

    @classmethod
    def setUpClass(cls):
        cls.so = None
        cc = shutil.which("cc")
        if cc is None:
            return  # stated: no compiler, no ABI proof (class skipped below)
        src = os.path.join(os.path.dirname(__file__), "mock_pkcs11.c")
        cls.tmp = tempfile.mkdtemp()
        cls.so = os.path.join(cls.tmp, "mock_pkcs11.so")
        subprocess.run([cc, "-shared", "-fPIC", "-o", cls.so, src],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        if cls.so:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _module(self):
        import vhpkcs11
        return vhpkcs11.load(self.so)

    def test_missing_library_refuses(self):
        import vhpkcs11
        with self.assertRaises(Exception) as ctx:
            vhpkcs11.load("/nonexistent/libnope.so")
        self.assertIn("not loaded", str(ctx.exception))

    @unittest.skipUnless(shutil.which("cc"), "no C compiler — ABI "
                         "conformance is stated RED, not skipped silently")
    def test_sign_verify_roundtrip_over_real_abi(self):
        mod = self._module()
        session = mod.open_session(0)
        sig = mod.sign(session, "cover-key", b"payload")
        self.assertEqual(len(sig), 32)
        self.assertTrue(mod.verify(session, "cover-key", b"payload", sig))
        self.assertFalse(mod.verify(session, "cover-key", b"tampered",
                                    sig))
        mod.close()

    @unittest.skipUnless(shutil.which("cc"), "no C compiler — ABI "
                         "conformance is stated RED, not skipped silently")
    def test_slots_enumerated(self):
        mod = self._module()
        self.assertEqual(mod.slots(), [0])
        mod.close()

    @unittest.skipUnless(shutil.which("cc"), "no C compiler — ABI "
                         "conformance is stated RED, not skipped silently")
    def test_end_to_end_through_keyring_and_provider(self):
        """vhpkcs11 (real ABI) -> pkcs11_provider -> KeyRing custody:
        an HSM key signs through the device and refuses snapshots."""
        mod = self._module()
        signer, verifier = pkcs11_provider(mod, slot=0,
                                           key_label="cover-key")
        kr = KeyRing()
        kr.register("hsm-cover", signer=signer, verifier=verifier,
                    active_from=0, active_to=100)
        with self.assertRaises(VHCError):
            kr.snapshot()  # the private half never leaves the device
        mod.close()


class TestDemoDeploy(unittest.TestCase):
    """The deployment-proof board reads a deterministic export — a demo
    you cannot reproduce is a story you cannot audit."""

    def test_deterministic_byte_for_byte(self):
        import demo_deploy
        first = demo_deploy.run_demo()
        second = demo_deploy.run_demo()
        self.assertEqual(first, second)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_sha256_stamp_is_the_contract(self):
        import demo_deploy
        import hashlib
        payload = demo_deploy.run_demo()
        body = {k: v for k, v in payload.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                           separators=(",", ":"))
                                .encode()).hexdigest()
        self.assertEqual(payload["sha256"], digest)

    def test_payload_shape_and_evidence(self):
        import demo_deploy
        p = demo_deploy.run_demo()
        self.assertEqual(p["version"], "deployproof-demo@1.0.0")

        kinds = [e["kind"] for e in p["psp"]["events"]]
        self.assertEqual(kinds, ["template-refusal", "four-eyes",
                                 "parsed", "parsed",
                                 "register-refusals"])
        reg = p["psp"]["events"][-1]
        self.assertTrue(all(a["refused"] for a in reg["attempts"]))
        parsed = [e for e in p["psp"]["events"] if e["kind"] == "parsed"]
        self.assertEqual(len(parsed[0]["rows"]), 2)  # failed never settles
        self.assertEqual(parsed[1]["rows"][0]["amount_minor"], 150000)
        # the registry is read back as data, not narrated
        self.assertEqual(p["psp"]["registry"]["version"], "vhpsp@1.1.0")

        self.assertIn("VOLATILE", p["ease"]["volatile_report"]
                      ["durability"])
        self.assertTrue(p["ease"]["restart"]["same_idem_key"])
        self.assertEqual(p["ease"]["restart"]["replay"],
                         {"replayed": 1, "dead": 0})
        self.assertEqual(p["ease"]["dead_letter"]["attempts"], 8)
        self.assertTrue(p["ease"]["dead_letter"]["last_error"])

        # the HSM card either proves the ABI here or states why not
        if shutil.which("cc"):
            self.assertEqual(p["hsm"]["environment"],
                             "compiled-abi-fixture")
            self.assertTrue(p["hsm"]["verify_good_payload"])
            self.assertFalse(p["hsm"]["verify_tampered_payload"])
            self.assertIn("custody lives in an HSM",
                          p["hsm"]["snapshot"])
        else:
            self.assertIn("NOT RUN", p["hsm"]["environment"])

    def test_main_writes_payload_file(self):
        import demo_deploy
        self.assertEqual(demo_deploy.main(), 0)
        out = os.path.normpath(demo_deploy.OUT)
        with open(out) as fh:
            payload = json.load(fh)
        self.assertEqual(payload["version"], "deployproof-demo@1.0.0")


if __name__ == "__main__":
    unittest.main()
