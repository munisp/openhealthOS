"""test_vhtamper.py — the tamper-space contract for the offline QR.

Doctrines under test:
- The checksum is the contract — and the contract covers the STRING
  presented at the border, not merely the bytes it decodes to.
- Base64url without padding carries non-significant low bits in the
  final character of any segment whose decoded length is not a
  multiple of three. A tampered string that decodes to identical bytes
  must still be RED ("non-canonical encoding"), never green.
- verify() never raises, for any input whatsoever — a bad QR is a red
  verdict with named reasons, not a crash.
- The contract holds across the ENTIRE single-character tamper space,
  not just at one sampled position.
"""
import json
import os
import random
import string
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhcover import (KeyRing, VHCCoverRegistry, _b64u,  # noqa: E402
                     _b64u_dec)

CV = "cv-vhc@1.0.0"
B64U_ALPHABET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "abcdefghijklmnopqrstuvwxyz0123456789-_")
# valid b64url chars, the separator, stripped padding, an invalid char
TAMPER_ALPHABET = "Ax0-_.=~"


def _reg():
    ring = KeyRing()
    ring.register("k1", "secret-one", active_from=0)
    return VHCCoverRegistry(profile_version=CV, key_ring=ring,
                            issuer="cv-vhc")


def _cover(reg):
    q = reg.quote(days=40, age_years=35, day=10)
    return reg.purchase(quote_id=q["quote_id"], passport_ref="P12345",
                        full_name="Ada Lovelace", entry_day=10, day=10,
                        key_id="k1")


def _noncanonical_twin(token, seg_idx):
    """Return (twin_token, original_seg) where the twin's segment decodes
    to the IDENTICAL bytes but differs in the final character's
    non-significant low bits. None when the segment is exact-length
    (decoded length a multiple of three — no spare bits exist)."""
    parts = token.split(".")
    seg = parts[seg_idx]
    raw = _b64u_dec(seg)
    if len(raw) % 3 == 0:
        return None
    idx = B64U_ALPHABET.index(seg[-1])
    twin_char = B64U_ALPHABET[idx ^ 1]     # differs in the lowest bit
    twin = ".".join(parts[:seg_idx] + [seg[:-1] + twin_char]
                    + parts[seg_idx + 1:])
    return twin, seg


class TestCanonicalForm(unittest.TestCase):
    def test_genuine_cover_is_canonical_and_green(self):
        reg = _reg()
        qr = _cover(reg)["qr"]
        for seg in qr.split("."):
            self.assertEqual(_b64u(_b64u_dec(seg)), seg,
                             "the issuer emits canonical segments")
        self.assertEqual(reg.verify(qr, day=20)["verdict"], "green")

    def test_decode_identical_signature_twin_is_red(self):
        """The hole this wave closes: swapping the final signature
        character for another with the same significant bits decodes to
        the SAME 32 bytes — without canonical validation this verified
        GREEN. The string is not the issued string; the contract says
        red."""
        reg = _reg()
        qr = _cover(reg)["qr"]
        twin, seg = _noncanonical_twin(qr, 2)
        self.assertIsNotNone(twin, "a 32-byte signature always has "
                                   "2 spare bits in its final char")
        self.assertEqual(_b64u_dec(twin.split(".")[2]),
                         _b64u_dec(seg))   # identical bytes — proven
        self.assertNotEqual(twin, qr)      # different string — proven
        v = reg.verify(twin, day=20)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("unverifiable: non-canonical encoding",
                      v["reasons"])

    def test_decode_identical_header_or_payload_twin_is_red(self):
        reg = _reg()
        qr = _cover(reg)["qr"]
        for seg_idx in (0, 1):
            found = _noncanonical_twin(qr, seg_idx)
            if found is None:
                continue  # exact-length segment has no spare bits
            twin, seg = found
            self.assertEqual(_b64u_dec(twin.split(".")[seg_idx]),
                             _b64u_dec(seg))
            v = reg.verify(twin, day=20)
            self.assertEqual(v["verdict"], "red")
            self.assertIn("unverifiable: non-canonical encoding",
                          v["reasons"])


class TestExhaustiveTamper(unittest.TestCase):
    def test_every_single_char_tamper_is_red_and_never_raises(self):
        """Every position, every tamper alphabet character: no exception,
        never amber, never green, and a red verdict always names its
        reasons. This pins the contract the sampled tamper test only
        spot-checks."""
        reg = _reg()
        qr = _cover(reg)["qr"]
        checked = 0
        for pos in range(len(qr)):
            for ch in TAMPER_ALPHABET:
                if ch == qr[pos]:
                    continue
                bad = qr[:pos] + ch + qr[pos + 1:]
                v = reg.verify(bad, day=20)     # must never raise
                self.assertEqual(
                    v["verdict"], "red",
                    "tamper at pos %d with %r escaped red: %r"
                    % (pos, ch, v))
                self.assertTrue(v["reasons"],
                                "a red verdict names its reasons")
                checked += 1
        self.assertGreater(checked, 1000,
                           "the sweep must cover the whole token")

    def test_tamper_sweep_is_deterministic(self):
        """The same sweep over the same token twice yields the same
        verdict/reason sequence — determinism applies to verdicts too.
        (One shared token: cover refs are secrets.token_hex — every
        purchase is deliberately unique.)"""
        reg = _reg()
        qr = _cover(reg)["qr"]

        def sweep():
            return [(v["verdict"], tuple(v["reasons"]))
                    for p in range(0, len(qr), 7)
                    for c in TAMPER_ALPHABET if c != qr[p]
                    for v in [reg.verify(qr[:p] + c + qr[p + 1:],
                                         day=20)]]
        first = sweep()
        second = sweep()
        self.assertEqual(first, second)
        self.assertTrue(all(v == "red" for v, _ in first))


class TestGarbageFuzz(unittest.TestCase):
    def test_garbage_never_raises_never_green(self):
        """Seeded garbage of every length and alphabet — including
        separators, padding, whitespace, and non-ASCII — is always a
        red verdict with reasons, never an exception, never green."""
        rng = random.Random(20261110)
        pool = (string.ascii_letters + string.digits + "-_.=~!@#$%^&*"
                " \t\n¡£§")
        reg = _reg()
        for _ in range(5000):
            n = rng.randrange(0, 400)
            s = "".join(rng.choice(pool) for _ in range(n))
            v = reg.verify(s, day=20)           # must never raise
            self.assertEqual(v["verdict"], "red",
                             "garbage escaped red: %r -> %r" % (s, v))
            self.assertTrue(v["reasons"])

    def test_empty_and_separator_only_tokens(self):
        reg = _reg()
        for s in ("", ".", "..", "...", "a.b", "a.b.c.d"):
            v = reg.verify(s, day=20)
            self.assertEqual(v["verdict"], "red")
            self.assertTrue(v["reasons"])


if __name__ == "__main__":
    unittest.main()


class TestDemoTamper(unittest.TestCase):
    """The tamper-gate board reads a deterministic export — a demo you
    cannot reproduce is a story you cannot audit."""

    def test_deterministic_byte_for_byte(self):
        import demo_tamper
        first = demo_tamper.run_demo()
        second = demo_tamper.run_demo()
        self.assertEqual(first, second)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_sha256_stamp_is_the_contract(self):
        import demo_tamper
        import hashlib
        payload = demo_tamper.run_demo()
        body = {k: v for k, v in payload.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                           separators=(",", ":"))
                                .encode()).hexdigest()
        self.assertEqual(payload["sha256"], digest)

    def test_payload_shape_and_evidence(self):
        import demo_tamper
        p = demo_tamper.run_demo()
        self.assertEqual(p["version"], "tamperproof-demo@1.0.0")
        self.assertTrue(p["gate"]["control_green"])
        self.assertEqual(p["gate"]["control"]["verdict"], "green")
        self.assertTrue(p["gate"]["all_gates_red"])
        names = [g["gate"] for g in p["gate"]["gates"]]
        self.assertEqual(names[:4], ["tail tamper", "decode-identical twin",
                                     "mid-signature forgery", "unknown key"])
        twin = p["gate"]["gates"][1]
        self.assertTrue(twin["decoded_bytes_identical"])
        self.assertIn("unverifiable: non-canonical encoding",
                      twin["reasons"])
        for g in p["gate"]["gates"]:
            self.assertEqual(g["verdict"], "red", g["gate"])
            self.assertTrue(g["reasons"], g["gate"])
        t, g = p["sweep"]["tamper"], p["sweep"]["garbage"]
        self.assertGreater(t["cases"], 1500)
        self.assertEqual(t["red"], t["cases"])
        self.assertEqual(t["raises"], 0)
        self.assertEqual(t["non_red"], 0)
        self.assertEqual(g["cases"], 5000)
        self.assertEqual(g["red"], 5000)
        self.assertEqual(g["raises"], 0)
        ev = p["cross_module"]["evidence"]
        self.assertTrue(p["cross_module"]["all_fail_closed"])
        self.assertEqual([e["refused_with"] for e in ev],
                         ["TrustError", "TrustError", "TagError"])
        self.assertIn("vhcover", p["versions"])

    def test_main_writes_payload_file(self):
        import demo_tamper
        self.assertEqual(demo_tamper.main(), 0)
        with open(os.path.normpath(demo_tamper.OUT),
                  encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"], demo_tamper.run_demo()["sha256"])
