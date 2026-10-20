import os
import unittest

os.environ["OH_ASSET_SECRET"] = "test-secret-key-123456"
import assettag


class TestTags(unittest.TestCase):
    def setUp(self):
        self.secret = os.environ["OH_ASSET_SECRET"].encode()

    def test_roundtrip(self):
        tag = assettag.mint("KDH-01", "MRI-03", self.secret, issued=1761000000)
        out = assettag.verify(tag, self.secret, now=1761000100)
        self.assertEqual(out["facility"], "KDH-01")
        self.assertEqual(out["asset"], "MRI-03")
        self.assertFalse(out["stale"])

    def test_tamper_rejected(self):
        tag = assettag.mint("KDH-01", "MRI-03", self.secret)
        evil = tag.replace("MRI-03", "MRI-99")
        with self.assertRaisesRegex(assettag.TagError, "signature"):
            assettag.verify(evil, self.secret)

    def test_wrong_secret_rejected(self):
        tag = assettag.mint("KDH-01", "MRI-03", self.secret)
        with self.assertRaises(assettag.TagError):
            assettag.verify(tag, b"another-secret-987654")

    def test_malformed_rejected(self):
        for bad in ("", "OHAST1.onlyone", "QRST1.A.B.1." + "0" * 16):
            with self.assertRaises(assettag.TagError):
                assettag.verify(bad, self.secret)

    def test_future_timestamp_rejected(self):
        tag = assettag.mint("KDH-01", "X-1", self.secret, issued=4_000_000_000)
        with self.assertRaisesRegex(assettag.TagError, "future"):
            assettag.verify(tag, self.secret, now=1_761_000_000)

    def test_stale_flagged_not_rejected(self):
        tag = assettag.mint("KDH-01", "X-1", self.secret, issued=1_000_000_000)
        out = assettag.verify(tag, self.secret, now=1_761_000_000)
        self.assertTrue(out["stale"])

    def test_dot_in_id_rejected(self):
        with self.assertRaises(assettag.TagError):
            assettag.mint("KD.H", "X", self.secret)

    def test_short_secret_rejected(self):
        os.environ["OH_ASSET_SECRET"] = "short"
        try:
            with self.assertRaises(assettag.TagError):
                assettag._secret("OH_ASSET_SECRET")
        finally:
            os.environ["OH_ASSET_SECRET"] = "test-secret-key-123456"


if __name__ == "__main__":
    unittest.main()
