import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devicereg"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vitals"))
from devicereg import DeviceRegistry
from vitals import VitalsIngest
from triggers import Rule, TriggerEngine, TriggerError


def _engine():
    reg = DeviceRegistry()
    reg.register("oxi-1", device_type="pulse-oximeter", serial="S",
                 facility="phc-1", attested_by="m", day=0,
                 calibration_day=0)
    reg.register("old-cuff", device_type="bp-cuff", serial="S2",
                 facility="phc-1", attested_by="m", day=0,
                 calibration_day=0)
    v = VitalsIngest(reg)
    return reg, v, TriggerEngine(v)


SPO2_RULE = Rule("spo2-low", version=1, vital="spo2", kind="threshold",
                 severity="critical", below=92, condition="pneumonia",
                 action_hint="assess for oxygen, consider referral")
HTN_RULE = Rule("htn-sustained", version=1, vital="systolic-bp",
                kind="sustained", severity="high", above=140,
                sustained_n=3, action_hint="start hypertension-followup pack")
SPO2_TREND = Rule("spo2-falling", version=1, vital="spo2", kind="trend",
                  severity="high", window_days=3, drop_per_day=1.0,
                  action_hint="deteriorating — review today")
SILENCE_RULE = Rule("bp-silence", version=1, vital="systolic-bp",
                    kind="silence", severity="moderate", window_days=7,
                    condition="hypertension",
                    action_hint="CHW follow-up visit")


class TestThreshold(unittest.TestCase):
    def test_critical_threshold_fires(self):
        _, v, e = _engine()
        e.add_rule(SPO2_RULE)
        v.record("p1", "spo2", 89, day=10, ts="t", device_id="oxi-1")
        fired = e.evaluate("p1", "spo2", day=10, condition="pneumonia")
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["severity"], "critical")
        self.assertEqual(fired[0]["status"], "drafted-for-human")
        self.assertIn("human confirms", fired[0]["note"])

    def test_normal_reading_silent(self):
        _, v, e = _engine()
        e.add_rule(SPO2_RULE)
        v.record("p1", "spo2", 97, day=10, ts="t", device_id="oxi-1")
        self.assertEqual(e.evaluate("p1", "spo2", day=10), [])

    def test_critical_no_cooldown_high_has_cooldown(self):
        _, v, e = _engine()
        e.add_rule(SPO2_RULE)
        e.add_rule(HTN_RULE)
        v.record("p1", "spo2", 89, day=10, ts="t")
        f1 = e.evaluate("p1", "spo2", day=10, condition="pneumonia")
        v.record("p1", "spo2", 88, day=10, ts="t2")
        f2 = e.evaluate("p1", "spo2", day=10, condition="pneumonia")
        self.assertEqual(len(f1), 1)
        self.assertEqual(len(f2), 1)  # critical: no cooldown, refires
        for d in (10, 11, 12):
            v.record("p1", "systolic-bp", 150, day=d, ts="t")
        fired = e.evaluate("p1", "systolic-bp", day=12)
        self.assertEqual(len(fired), 1)
        fired2 = e.evaluate("p1", "systolic-bp", day=12)
        self.assertEqual(fired2, [])  # high-severity cooldown 1 day


class TestSustainedAndTrend(unittest.TestCase):
    def test_sustained_needs_n(self):
        _, v, e = _engine()
        e.add_rule(HTN_RULE)
        for d in (10, 11):
            v.record("p1", "systolic-bp", 150, day=d, ts="t")
        self.assertEqual(e.evaluate("p1", "systolic-bp", day=11), [])
        v.record("p1", "systolic-bp", 148, day=12, ts="t")
        fired = e.evaluate("p1", "systolic-bp", day=12)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["evidence"]["readings"], [150, 150, 148])

    def test_trend_catches_fall_within_normal_range(self):
        _, v, e = _engine()
        e.add_rule(SPO2_TREND)
        for d, val in ((10, 97), (11, 95), (12, 93)):
            v.record("p1", "spo2", val, day=d, ts="t")
        fired = e.evaluate("p1", "spo2", day=12)
        self.assertEqual(len(fired), 1)
        self.assertLess(fired[0]["evidence"]["rate_per_day"], 0)


class TestStaleAndSilence(unittest.TestCase):
    def test_stale_device_downgrades_to_confirm_first(self):
        reg, v, e = _engine()
        e.add_rule(SPO2_RULE)
        # oxi-1 calibration stale by day 400
        v.record("p1", "spo2", 89, day=400, ts="t", device_id="oxi-1")
        fired = e.evaluate("p1", "spo2", day=400, condition="pneumonia")
        self.assertEqual(fired[0]["severity"], "moderate")
        self.assertIn("verified device", fired[0]["action_hint"])

    def test_silence_rule(self):
        _, v, e = _engine()
        e.add_rule(SILENCE_RULE)
        v.record("p1", "systolic-bp", 130, day=1, ts="t")
        fired = e.check_silence("p1", day=10,
                                enrolled_vitals=["systolic-bp"],
                                condition="hypertension")
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["evidence"]["days_since_last"], 9)
        self.assertEqual(e.check_silence("p1", day=5,
                                         enrolled_vitals=["systolic-bp"]), [])


class TestAckAndEscalation(unittest.TestCase):
    def test_ack_flow_and_reescalation(self):
        _, v, e = _engine()
        e.add_rule(SPO2_RULE)
        v.record("p1", "spo2", 89, day=10, ts="t")
        ev = e.evaluate("p1", "spo2", day=10, condition="pneumonia")[0]
        un = e.unacknowledged(as_of=11)
        self.assertEqual(len(un), 1)
        self.assertTrue(un[0]["reescalate"])
        e.acknowledge(ev["id"], day=11, by="nurse-j", note="oxygen started")
        self.assertEqual(e.unacknowledged(as_of=15), [])
        with self.assertRaises(TriggerError):
            e.acknowledge(ev["id"], day=12, by="x")

    def test_unversioned_rule_refused(self):
        with self.assertRaises(TriggerError):
            Rule("bad", version=None, vital="spo2", kind="threshold",
                 severity="critical", below=90, action_hint="x")


if __name__ == "__main__":
    unittest.main()
