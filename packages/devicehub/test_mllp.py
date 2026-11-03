"""Tests for mllp.py — framing, listener, client, registry."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import mllp  # noqa: E402
from devicehub import build_ack, parse_oru  # noqa: E402

ORU = ("MSH|^~\\&|COBAS|ROCHE|OH|LAB|20261103083000||ORU^R01|MSG-1|P|"
       "2.5\rPID|1||P-1\r"
       "OBX|1|NM|K^Potassium||5.2|mmol/L||N|||F|||20261103082500\r")


class TestFraming(unittest.TestCase):
    def test_frame_round_trip(self):
        msg, rest = mllp.unframe(mllp.frame(ORU))
        self.assertEqual(msg, ORU)
        self.assertEqual(rest, b"")

    def test_partial_frame_waits(self):
        full = mllp.frame(ORU)
        msg, rest = mllp.unframe(full[:20])
        self.assertIsNone(msg)
        self.assertEqual(rest, full[:20])

    def test_two_frames_in_one_buffer(self):
        buf = mllp.frame(ORU) + mllp.frame(ORU.replace("MSG-1", "MSG-2"))
        m1, buf = mllp.unframe(buf)
        m2, buf = mllp.unframe(buf)
        self.assertIn("MSG-1", m1)
        self.assertIn("MSG-2", m2)
        self.assertEqual(buf, b"")

    def test_garbage_stream_refuses(self):
        with self.assertRaises(mllp.MLLPError):
            mllp.unframe(b"\x00\x01garbage")

    def test_empty_message_refuses(self):
        with self.assertRaises(mllp.MLLPError):
            mllp.frame("")


class TestListener(unittest.TestCase):
    def test_end_to_end_over_localhost(self):
        received = []

        def dispatch(message):
            parsed = parse_oru(message)
            received.append(parsed)
            return build_ack(parsed["message_id"])

        listener = mllp.MLLPListener("127.0.0.1", 0, dispatch)
        listener.serve_in_thread()
        port = listener.server_address[1]
        try:
            ack = mllp.send("127.0.0.1", port, ORU)
            self.assertIn("MSA|AA|MSG-1", ack)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["observations"][0]["code"],
                             "K")
        finally:
            listener.shutdown()
            listener.server_close()
        # the registry saw the connection open and close
        events = [e["event"] for e in listener.registry.events]
        self.assertIn("connect", events)
        self.assertIn("disconnect", events)

    def test_bad_dispatch_gets_honest_error_ack(self):
        def dispatch(message):
            raise ValueError("boom")

        listener = mllp.MLLPListener("127.0.0.1", 0, dispatch)
        listener.serve_in_thread()
        port = listener.server_address[1]
        try:
            ack = mllp.send("127.0.0.1", port, ORU)
            self.assertIn("MSA|AE|", ack)
            self.assertIn("boom", ack)
        finally:
            listener.shutdown()
            listener.server_close()


if __name__ == "__main__":
    unittest.main()
