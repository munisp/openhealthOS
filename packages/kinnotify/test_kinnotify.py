
import unittest
from kinnotify import KinRegistry, Notifier, KinError


def _setup():
    reg = KinRegistry()
    reg.propose("p1", "c1", name="Mama", phone="+234801", relation="mother")
    reg.propose("p1", "c2", name="Uncle", phone="+234802", relation="uncle")
    reg.activate("p1", "c1", basis="patient-consent")
    return reg


class TestKinNotify(unittest.TestCase):
    def test_unconsented_contact_not_notified(self):
        reg = _guard = KinRegistry()
        reg.propose("p1", "c2", name="Uncle", phone="+234802",
                    relation="uncle")  # never activated
        sent = []
        n = Notifier(reg, lambda ch, ph, m: sent.append((ch, ph)) or "delivered")
        out = n.notify("p1", message_class="critical",
                       event_ref="ev-9", callback="0800OH")
        self.assertEqual(out["notified"], 0)

    def test_critical_walks_channel_plan_until_delivered(self):
        reg = _setup()
        calls = []
        def sender(ch, ph, m):
            calls.append(ch)
            return "failed" if ch == "voice" else "delivered"
        n = Notifier(reg, sender)
        out = n.notify("p1", message_class="critical",
                       event_ref="e1", callback="0800OH")
        self.assertEqual(calls, ["voice", "sms"])  # stops at first delivery
        self.assertEqual(out["notified"], 1)

    def test_all_channels_fail_is_stated(self):
        reg = _setup()
        n = Notifier(reg, lambda ch, ph, m: "failed")
        out = n.notify("p1", message_class="critical",
                       event_ref="e1", callback="0800OH")
        self.assertEqual(out["notified"], 0)
        self.assertEqual(len(n.log), 3)  # all 3 channels attempted, recorded

    def test_channel_exception_is_failure_not_crash(self):
        reg = _setup()
        def boom(ch, ph, m):
            raise ConnectionError("telco down")
        n = Notifier(reg, boom)
        out = n.notify("p1", message_class="admission",
                       event_ref="e2", callback="0800OH")
        self.assertEqual(out["notified"], 0)
        self.assertTrue(all(l["outcome"] == "failed" for l in n.log))

    def test_message_carries_no_clinical_detail(self):
        reg = _setup()
        captured = []
        n = Notifier(reg, lambda ch, ph, m: captured.append(m) or "delivered")
        n.notify("p1", message_class="critical", event_ref="e1",
                 callback="0800OH")
        self.assertNotIn("diagnos", captured[0].lower())
        self.assertIn("e1", captured[0])

    def test_unknown_message_class_refused(self):
        reg = _setup()
        n = Notifier(reg, lambda *a: "delivered")
        with self.assertRaises(KinError):
            n.notify("p1", message_class="gossip", event_ref="e1",
                     callback="0800OH")

    def test_activation_basis_validated(self):
        reg = KinRegistry()
        reg.propose("p1", "c1", name="X", phone="+1", relation="friend")
        with self.assertRaises(KinError):
            reg.activate("p1", "c1", basis="vibes")


if __name__ == "__main__":
    unittest.main()
