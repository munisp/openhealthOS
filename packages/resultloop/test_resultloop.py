"""test_resultloop.py — the loop, the refusals, the ladder."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from resultloop import ChannelAdapter, LoopError, ResultLoop  # noqa: E402

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.loop = ResultLoop()

    def _dispatch(self, **kw):
        args = dict(case_id="C1", facility="phc-1", referrer="mo-1",
                    critical=False, day=0, content_ref="report:R1#abc123")
        args.update(kw)
        return self.loop.dispatch(**args)

    def test_dispatch_queues_without_channel(self):
        d = self._dispatch()
        self.assertEqual(d["state"], "queued")

    def test_no_referrer_refused(self):
        with self.assertRaises(LoopError):
            self._dispatch(referrer="")

    def test_no_content_ref_refused(self):
        with self.assertRaises(LoopError):
            self._dispatch(content_ref="")

    def test_dispatch_is_idempotent_per_case(self):
        d1 = self._dispatch()
        d2 = self._dispatch()
        self.assertEqual(d1["delivery_id"], d2["delivery_id"])
        self.assertEqual(len(self.loop.deliveries), 1)

    def test_channel_claims_sent_only_with_endpoint(self):
        loop = ResultLoop(ChannelAdapter("http://rail/notify"))
        d = loop.dispatch(case_id="C1", facility="f", referrer="mo-1",
                          critical=False, day=0, content_ref="r:1")
        self.assertEqual(d["state"], "sent")
        # and the honest spike refuses to claim otherwise
        with self.assertRaises(LoopError):
            self.loop.channel.send(d)


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.loop = ResultLoop()
        self.d = self.loop.dispatch(case_id="C1", facility="f",
                                    referrer="mo-1", critical=True, day=0,
                                    content_ref="r:1#h")
        self.did = self.d["delivery_id"]

    def test_forward_only(self):
        self.loop.mark_sent(self.did, day=0, receipt_ref="rail:msg-1")
        with self.assertRaises(LoopError):
            self.loop.mark_sent(self.did, day=0, receipt_ref="rail:msg-2")

    def test_receipt_required(self):
        with self.assertRaises(LoopError):
            self.loop.mark_delivered(self.did, day=0, receipt_ref="")

    def test_full_path_to_ack(self):
        self.loop.mark_sent(self.did, day=0, receipt_ref="rail:1")
        self.loop.mark_delivered(self.did, day=0, receipt_ref="rcpt:1")
        out = self.loop.acknowledge(self.did, day=0, by="mo-1")
        self.assertFalse(out["flags"])  # clean path, no flags
        with self.assertRaises(LoopError):
            self.loop.acknowledge(self.did, day=1, by="mo-1")

    def test_off_channel_ack_is_flagged_not_blocked(self):
        out = self.loop.acknowledge(self.did, day=0, by="mo-1")
        self.assertIn("acked-off-channel", out["flags"])

    def test_delegate_ack_requires_reason(self):
        with self.assertRaises(LoopError):
            self.loop.acknowledge(self.did, day=0, by="nurse-9")
        out = self.loop.acknowledge(self.did, day=0, by="nurse-9",
                                    delegate_reason="mo-1 in theatre; "
                                    "read back result verbatim")
        self.assertIn("delegate-ack", out["flags"])


class TestLoopWatchesItself(unittest.TestCase):
    def test_unclosed_is_critical_first_overdue_first(self):
        loop = ResultLoop()
        loop.dispatch(case_id="routine-1", facility="f", referrer="mo-1",
                      critical=False, day=0, content_ref="r:1")
        loop.dispatch(case_id="crit-1", facility="f", referrer="mo-1",
                      critical=True, day=2, content_ref="r:2")
        loop.dispatch(case_id="crit-2", facility="f", referrer="mo-1",
                      critical=True, day=0, content_ref="r:3")
        out = loop.unclosed(as_of=5)
        self.assertEqual([u["case_id"] for u in out],
                         ["crit-2", "crit-1", "routine-1"])
        self.assertTrue(all(u["overdue"] for u in out[:2]))

    def test_escalation_ladder_climbs_daily(self):
        loop = ResultLoop()
        loop.dispatch(case_id="crit-1", facility="f", referrer="mo-1",
                      critical=True, day=0, content_ref="r:1")
        d1 = loop.escalate(as_of=1)
        self.assertEqual([e["to"] for e in d1], ["facility-head"])
        d2 = loop.escalate(as_of=2)
        self.assertEqual([e["to"] for e in d2], ["on-call-mo"])
        d3 = loop.escalate(as_of=3)
        self.assertEqual([e["to"] for e in d3], ["on-call-mo"])
        self.assertEqual(d3[0]["status"], "drafted-for-human")

    def test_acknowledged_critical_stops_climbing(self):
        loop = ResultLoop()
        d = loop.dispatch(case_id="crit-1", facility="f",
                          referrer="mo-1", critical=True, day=0,
                          content_ref="r:1")
        loop.acknowledge(d["delivery_id"], day=1, by="mo-1")
        self.assertEqual(loop.escalate(as_of=5), [])

    def test_stats_ship_denominators(self):
        loop = ResultLoop()
        a = loop.dispatch(case_id="c1", facility="f", referrer="mo-1",
                          critical=True, day=0, content_ref="r:1")
        loop.dispatch(case_id="c2", facility="f", referrer="mo-1",
                      critical=False, day=0, content_ref="r:2")
        loop.acknowledge(a["delivery_id"], day=2, by="mo-1")
        s = loop.loop_stats()
        self.assertEqual(s["dispatched"], 2)
        self.assertEqual(s["acknowledged"], 1)
        self.assertEqual(s["critical"],
                         {"dispatched": 1, "acknowledged": 1})
        self.assertEqual(s["median_days_to_ack"], 2)


class TestCLI(unittest.TestCase):
    def _run(self, db, cmd, args, env_extra=None):
        import json as j
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        p = subprocess.run([sys.executable, CLI, db, cmd, j.dumps(args)],
                           capture_output=True, text=True, env=env)
        return p.returncode, j.loads(p.stdout)

    def test_dispatch_ack_across_processes(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "loop.json")
            code, out = self._run(db, "dispatch",
                                  {"case_id": "C1", "facility": "f",
                                   "referrer": "mo-1", "critical": True,
                                   "day": 0, "content_ref": "r:1#h"})
            self.assertEqual(code, 0)
            did = out["result"]["delivery_id"]
            self.assertEqual(out["result"]["state"], "queued")
            code, out = self._run(db, "ack",
                                  {"delivery_id": did, "day": 1,
                                   "by": "mo-1"})
            self.assertEqual(code, 0)
            code, out = self._run(db, "stats", {})
            self.assertEqual(out["result"]["acknowledged"], 1)

    def test_domain_error_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "loop.json")
            code, out = self._run(db, "ack",
                                  {"delivery_id": "DLV-99", "day": 1,
                                   "by": "mo-1"})
            self.assertEqual(code, 2)
            self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
