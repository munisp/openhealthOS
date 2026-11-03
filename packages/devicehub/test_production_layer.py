"""Tests for persist.py, TLS posture, seriallane.py, orderdownload.py
and crosslink seam 19 (feed health → oversight)."""
import datetime
import io
import os
import sys
import tempfile
import unittest

PKG = os.path.join(os.path.dirname(__file__), "..")
for p in ("devicehub", "crosslink", "drugkb", "teleicu", "teleread"):
    sys.path.insert(0, os.path.join(PKG, p))

import astm  # noqa: E402
import mllp  # noqa: E402
from crosslink import feed_health_to_oversight  # noqa: E402
from devicelink import FeedHealth  # noqa: E402
from devicehub import DeviceHubError, parse_oru  # noqa: E402
from orderdownload import build_orm, parse_orm  # noqa: E402
from persist import (DurableFeedHealth, DurableIdentityMap,  # noqa: E402
                     DurableResultStore, DurableSpool, Journal)
from seriallane import read_session_from  # noqa: E402
from teleicu import TeleICU  # noqa: E402
from teleread import ReaderRegistry  # noqa: E402

ORU = ("MSH|^~\\&|A|B|C|D|2026||ORU^R01|MSG-1|P|2.5\r"
       "PID|1||p-1\r"
       "OBX|1|NM|K^Potassium||5.2|mmol/L||N|||F|||20261103082500\r")


class TestJournalDurability(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "journal.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def test_spool_survives_restart(self):
        spool = DurableSpool(Journal(self.path))
        spool.enqueue("DH-1", "outbound", {"x": 1})
        # "restart"
        spool2 = DurableSpool(Journal(self.path))
        self.assertEqual(len(spool2.pending("DH-1")), 1)
        delivered = []
        spool2.replay("DH-1", delivered.append)
        self.assertEqual(delivered, [{"x": 1}])
        # and the replayed state survives too
        spool3 = DurableSpool(Journal(self.path))
        self.assertEqual(len(spool3.pending()), 0)

    def test_result_store_survives_restart_with_chains(self):
        store = DurableResultStore(Journal(self.path))
        store.post(parse_oru(ORU))
        store2 = DurableResultStore(Journal(self.path))
        self.assertEqual(store2.latest("p-1", "K")["value"], "5.2")
        # dedupe survives too — reposting the same message is a
        # duplicate, not a second post
        self.assertEqual(store2.post(parse_oru(ORU))["verdict"],
                         "duplicate")

    def test_identity_map_survives_restart(self):
        im = DurableIdentityMap(Journal(self.path))
        im.register("MRN-1", "plat-1", by="reg")
        im2 = DurableIdentityMap(Journal(self.path))
        self.assertEqual(im2.resolve("MRN-1"), "plat-1")

    def test_corrupt_journal_refuses_boot(self):
        with open(self.path, "w") as fh:
            fh.write('{"op":"enqueue"}\n{corrupt\n')
        with self.assertRaises(DeviceHubError) as cm:
            DurableSpool(Journal(self.path))
        self.assertIn("refusing to boot", str(cm.exception))

    def test_feed_health_quarantine_survives(self):
        fh = DurableFeedHealth(Journal(self.path))
        fh.note_parse_failure("DH-1")
        fh.note_parse_failure("DH-1")
        fh.note_parse_failure("DH-1")
        fh2 = DurableFeedHealth(Journal(self.path))
        with self.assertRaises(DeviceHubError):
            fh2.check("DH-1")


class TestTLSPosture(unittest.TestCase):
    def test_plaintext_routable_refuses(self):
        with self.assertRaises(mllp.MLLPError) as cm:
            mllp.check_tls_posture("192.168.1.50", None)
        self.assertIn("cleartext", str(cm.exception))

    def test_plaintext_exception_needs_reason(self):
        with self.assertRaises(mllp.MLLPError):
            mllp.check_tls_posture("192.168.1.50", None,
                                   allow_plaintext=True)
        out = mllp.check_tls_posture("192.168.1.50", None,
                                     allow_plaintext=True,
                                     plaintext_reason="isolated bench "
                                     "VLAN, no PHI leaves segment")
        self.assertIn("plaintext-exception", out)

    def test_loopback_and_tls_pass(self):
        self.assertEqual(mllp.check_tls_posture("127.0.0.1", None),
                         "loopback-plaintext")
        self.assertEqual(mllp.check_tls_posture("10.0.0.5", object()),
                         "tls")


class TestSerialLane(unittest.TestCase):
    def _session_bytes(self, *texts):
        frames = "".join(astm.build_frame((i + 1) % 8, t)
                         for i, t in enumerate(texts))
        return (f"{astm.ENQ}{frames}{astm.EOT}").encode("latin-1")

    def test_reads_session_from_stream(self):
        src = io.BytesIO(self._session_bytes(
            "P|1||p-1", "R|1|^^^GLU|95|mg/dL||N||||F"))
        out = read_session_from(src)
        self.assertEqual(out["observations"][0]["code"], "GLU")

    def test_line_noise_counted_not_silently_forgiven(self):
        src = io.BytesIO(b"\x00\xff\x00" + self._session_bytes(
            "R|1|^^^K|5.0|mmol/L||N||||F"))
        out = read_session_from(src)
        self.assertEqual(out["line_noise_bytes"], 3)

    def test_silent_mid_session_refuses(self):
        src = io.BytesIO(astm.ENQ.encode("latin-1"))  # ENQ then nothing
        with self.assertRaises(DeviceHubError) as cm:
            read_session_from(src)
        self.assertIn("silent mid-session", str(cm.exception))


class TestOrderDownload(unittest.TestCase):
    def test_build_and_parse_round_trip(self):
        msg = build_orm(message_id="ORD-1", placer_order_number="PO-77",
                        patient_id="p-1", test_code="CHEM7",
                        priority="S", ordering_provider="dr-1")
        out = parse_orm(msg)
        self.assertEqual(out["placer_order_number"], "PO-77")
        self.assertEqual(out["priority"], "S")
        self.assertEqual(out["control"], "NW")

    def test_no_placer_number_refuses(self):
        with self.assertRaises(DeviceHubError):
            build_orm(message_id="O", placer_order_number="",
                      patient_id="p-1", test_code="CHEM7")

    def test_unknown_priority_refuses(self):
        with self.assertRaises(DeviceHubError):
            build_orm(message_id="O", placer_order_number="PO-1",
                      patient_id="p-1", test_code="CHEM7",
                      priority="WHENEVER")

    def test_unhandled_control_refuses(self):
        msg = build_orm(message_id="ORD-2", placer_order_number="PO-1",
                        patient_id="p-1", test_code="CHEM7")
        with self.assertRaises(DeviceHubError):
            parse_orm(msg.replace("ORC|NW", "ORC|CA"))


class TestFeedHealthOversightSeam(unittest.TestCase):
    def test_stale_and_quarantined_open_oversight_idempotently(self):
        fh = FeedHealth()
        fh.seen("DH-1", datetime.datetime(2026, 1, 1, 8, 0))
        fh.set_expectation("DH-1", 1)  # 1 second — instantly stale
        for _ in range(3):
            fh.note_parse_failure("DH-2")
        fh.seen("DH-2", datetime.datetime.now())
        readers = ReaderRegistry()
        readers.register("icu-1", licence_id="MDCN-I1",
                         licence_expiry_day=400,
                         scopes=("intensivist",))
        icu = TeleICU(readers)
        opened = {}
        out = feed_health_to_oversight(fh, icu, facility="phc-1", day=1,
                                       opened=opened)
        self.assertEqual(len(out["opened"]), 2)
        again = feed_health_to_oversight(fh, icu, facility="phc-1",
                                         day=1, opened=opened)
        self.assertEqual(len(again["opened"]), 0)
        self.assertEqual(len(again["already_open"]), 2)


if __name__ == "__main__":
    unittest.main()
