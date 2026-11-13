"""storageport contract tests (ADR-0089).

The port is gated: opendal is the MAIN engine (the suite goes red if
it is absent), degraded mode is declared and narrow, refusals are
stated and pre-network, absence is typed, and credentials never live
in the shipped registry.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import storageport  # noqa: E402


def _registry(root):
    return {"t": {"scheme": "fs", "config": {"root": root}}}


class TestMainAdapter(unittest.TestCase):
    """Main means main: opendal absent is a red gate, not a quiet
    fallback."""

    def test_main_adapter_is_opendal(self):
        self.assertEqual(storageport.ADAPTER, "opendal")
        self.assertFalse(storageport.DEGRADED)
        self.assertIsNotNone(storageport.OPENDAL_VERSION)

    def test_retry_policy_is_data(self):
        self.assertEqual(storageport.RETRY_TIMES, 3)


class TestContract(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.store = storageport.connect(
            "t", registry=_registry(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_roundtrip_byte_exact(self):
        receipt = self.store.put("a/b.bin", b"\x00\x01\x02" * 1000)
        self.assertEqual(receipt["size"], 3000)
        self.assertEqual(self.store.get("a/b.bin"), b"\x00\x01\x02" * 1000)
        self.assertEqual(receipt["adapter"], "opendal")
        self.assertEqual(receipt["scheme"], "fs")
        self.assertEqual(self.store.checksum("a/b.bin"),
                         receipt["sha256"])

    def test_range_read(self):
        self.store.put("r.bin", bytes(range(256)) * 4)
        self.assertEqual(self.store.get_range("r.bin", 10, 5),
                         (bytes(range(256)) * 4)[10:15])

    def test_range_refuses_bad_args(self):
        self.store.put("r.bin", b"0123456789")
        with self.assertRaises(storageport.StorageRefusal):
            self.store.get_range("r.bin", -1, 5)
        with self.assertRaises(storageport.StorageRefusal):
            self.store.get_range("r.bin", 0, 0)

    def test_absent_is_typed(self):
        with self.assertRaises(storageport.AbsentKey):
            self.store.get("never/here.bin")
        with self.assertRaises(storageport.AbsentKey):
            self.store.stat("never/here.bin")
        self.assertFalse(self.store.exists("never/here.bin"))

    def test_stat_and_list(self):
        self.store.put("d/x.bin", b"x" * 7)
        self.store.put("d/y.bin", b"yy")
        self.assertEqual(self.store.stat("d/x.bin")["content_length"], 7)
        listing = self.store.list("d/")
        self.assertTrue(any(p.endswith("x.bin") for p in listing))
        self.assertTrue(any(p.endswith("y.bin") for p in listing))

    def test_delete_idempotent(self):
        self.store.put("z.bin", b"z")
        self.store.delete("z.bin")
        self.store.delete("z.bin")  # second delete is not an error
        self.assertFalse(self.store.exists("z.bin"))

    def test_key_hygiene(self):
        with self.assertRaises(storageport.StorageRefusal):
            self.store.put("../escape", b"e")
        with self.assertRaises(storageport.StorageRefusal):
            self.store.get("/absolute")
        with self.assertRaises(storageport.StorageRefusal):
            self.store.put("", b"e")

    def test_capability_live_read(self):
        cap = self.store.capability()
        for op in ("read", "write", "stat", "delete", "list"):
            self.assertTrue(cap[op])
        self.assertFalse(cap["presign"])


class TestRefusals(unittest.TestCase):
    def test_unknown_entry_names_registry(self):
        with self.assertRaises(storageport.StorageRefusal) as ctx:
            storageport.connect("nope")
        self.assertIn("ministry-docs", str(ctx.exception))

    def test_missing_secrets_refused_prenetwork(self):
        with self.assertRaises(storageport.StorageRefusal) as ctx:
            storageport.connect("cloud-archive")
        msg = str(ctx.exception)
        self.assertIn("access_key_id", msg)
        self.assertIn("secret_access_key", msg)

    def test_presign_refused_on_fs_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            store = storageport.connect("t", registry=_registry(td))
            store.put("f.bin", b"f")
            with self.assertRaises(storageport.StorageRefusal) as ctx:
                store.presign_read("f.bin", 60)
            self.assertIn("no presign capability", str(ctx.exception))


class TestDegradedMode(unittest.TestCase):
    """The stdlib fallback exists — declared, narrow, and stamped."""

    def test_degraded_fs_roundtrip_stamped(self):
        with tempfile.TemporaryDirectory() as td:
            store = storageport.connect("t", registry=_registry(td),
                                        force_degraded=True)
            self.assertEqual(store.adapter, "stdlib-fs")
            self.assertTrue(store.degraded)
            receipt = store.put("c.bin", b"cache")
            self.assertEqual(receipt["adapter"], "stdlib-fs")
            self.assertEqual(store.get("c.bin"), b"cache")
            self.assertEqual(store.get_range("c.bin", 1, 3), b"ach")

    def test_degraded_memory(self):
        reg = {"m": {"scheme": "memory", "config": {}}}
        store = storageport.connect("m", registry=reg,
                                    force_degraded=True)
        store.put("k", b"v")
        self.assertEqual(store.get("k"), b"v")
        self.assertEqual(store.list(), ["k"])

    def test_degraded_refuses_cloud_by_name(self):
        with self.assertRaises(storageport.StorageRefusal) as ctx:
            storageport.connect(
                "cloud-archive",
                secrets={"access_key_id": "x", "secret_access_key": "y"},
                force_degraded=True)
        self.assertIn("degraded mode", str(ctx.exception))

    def test_unknown_scheme_refused(self):
        reg = {"w": {"scheme": "webdav-Imaginary", "config": {}}}
        with self.assertRaises(storageport.StorageRefusal):
            storageport.connect("w", registry=reg)


class TestRegistryHygiene(unittest.TestCase):
    """Credentials are never values in the shipped registry."""

    def test_no_secret_values_in_registry(self):
        for name, entry in storageport.REGISTRY.items():
            for key, value in entry.get("config", {}).items():
                self.assertNotIn("secret", key.lower(), name)
                self.assertNotIn("access_key", key.lower(), name)
                self.assertIsInstance(value, str, name)

    def test_cloud_entry_declares_requires(self):
        self.assertEqual(
            storageport.REGISTRY["cloud-archive"]["requires"],
            ["access_key_id", "secret_access_key"])

    def test_census_shape(self):
        c = storageport.census()
        self.assertEqual(c["adapter_active"], "opendal")
        self.assertFalse(c["degraded"])
        self.assertEqual(c["registry_entries"], 3)
        self.assertEqual(c["schemes"], ["fs", "memory", "s3"])
        self.assertEqual(c["entries_requiring_secrets"],
                         ["cloud-archive"])


if __name__ == "__main__":
    unittest.main()
