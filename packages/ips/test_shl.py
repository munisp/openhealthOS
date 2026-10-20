#!/usr/bin/env python3
import json
import time
import unittest

from shl import encode_link, main, mint_payload, parse_link


class TestSHL(unittest.TestCase):
    def test_mint_shape(self):
        p = mint_payload("https://e.example/shl/abc", "IPS — Amina", days=7, passcode=True)
        self.assertEqual(p["flag"], "P")
        self.assertGreater(p["exp"], int(time.time()) + 6 * 86400)
        import base64
        self.assertEqual(len(base64.urlsafe_b64decode(p["key"] + "==")), 32)

    def test_roundtrip(self):
        p = mint_payload("https://e.example/shl/xyz", "IPS — test")
        link = encode_link(p)
        self.assertTrue(link.startswith("shlink:/"))
        back = parse_link(link)
        self.assertEqual(back["url"], p["url"])
        self.assertEqual(back["key"], p["key"])
        self.assertNotIn("_expired", back)

    def test_two_mints_differ(self):
        a = mint_payload("https://e.example/1", "a")
        b = mint_payload("https://e.example/1", "a")
        self.assertNotEqual(a["key"], b["key"])  # fresh key per link

    def test_fail_closed(self):
        with self.assertRaises(ValueError):
            parse_link("https://not-a-shlink")
        bad = encode_link({"url": "https://e.example/x"})  # no key
        with self.assertRaises(ValueError):
            parse_link(bad)
        short = encode_link({"url": "https://e.example/x", "key": "AAAA"})
        with self.assertRaises(ValueError):
            parse_link(short)

    def test_expired_flagged(self):
        p = mint_payload("https://e.example/x", "old", days=1)
        p["exp"] = int(time.time()) - 10
        back = parse_link(encode_link(p))
        self.assertTrue(back["_expired"])

    def test_bad_url_rejected(self):
        with self.assertRaises(ValueError):
            mint_payload("ftp://nope", "x")

    def test_cli(self):
        self.assertEqual(main(["mint", "--url", "https://e.example/s", "--label", "t"]), 0)
        p = mint_payload("https://e.example/s", "t")
        self.assertEqual(main(["parse", encode_link(p)]), 0)


if __name__ == "__main__":
    unittest.main()
