import unittest

from voicebridge import BridgeSession, ConverseClient, Error


def fake_dispatcher(script):
    """script: list of responses to hand back, in order, per path."""
    calls = {"dtmf": [], "converse": []}
    responses = {"dtmf": list(script.get("dtmf", [])),
                 "converse": list(script.get("converse", []))}

    def poster(path, payload):
        kind = "dtmf" if path == "/webhooks/voice" else "converse"
        calls[kind].append(payload)
        if not responses[kind]:
            return {"received": False, "say": "Please try again."}
        return responses[kind].pop(0)

    return poster, calls


class TestSession(unittest.TestCase):
    def test_full_conversation_to_hangup(self):
        poster, calls = fake_dispatcher({"converse": [
            {"received": True, "expect_more": True, "triage": "red",
             "say": "I understand. Anything else?"},
            {"received": True, "expect_more": True,
             "say": "Is the patient with you? Say yes or no."},
            {"received": True, "done": True, "incident_id": "INC-00001",
             "triage": "red", "say": "Help is on the way."},
        ]})
        s = BridgeSession(ConverseClient("http://x", poster),
                          "+234803", "call-1", 6.52, 3.38)
        d = s.on_utterance("my husband collapsed")
        self.assertTrue(d["listen"])
        d = s.on_utterance("he is bleeding")
        self.assertTrue(d["listen"])
        d = s.on_utterance("yes")
        self.assertTrue(d["hangup"])
        self.assertEqual(s.incident_id, "INC-00001")
        # coordinates were forwarded on every turn
        for c in calls["converse"]:
            self.assertEqual((c["lat"], c["lng"]), (6.52, 3.38))

    def test_dtmf_fast_path_files_immediately(self):
        poster, calls = fake_dispatcher({"dtmf": [
            {"received": True, "incident_id": "INC-00009", "triage": "red",
             "play": "Your emergency has been registered."}]})
        s = BridgeSession(ConverseClient("http://x", poster), "+234803", "c2")
        d = s.on_dtmf("1")
        self.assertTrue(d["hangup"])
        self.assertEqual(s.incident_id, "INC-00009")
        self.assertEqual(len(calls["converse"]), 0)  # no conversation needed

    def test_done_session_reassures_and_hangs_up(self):
        poster, _ = fake_dispatcher({"converse": [
            {"received": True, "done": True, "incident_id": "INC-1",
             "say": "Help is on the way."}]})
        s = BridgeSession(ConverseClient("http://x", poster), "+234", "c3")
        s.on_utterance("emergency")
        d = s.on_utterance("hello?")
        self.assertTrue(d["hangup"])
        self.assertIn("already registered", d["say"])

    def test_empty_turns_escalate_to_dtmf_hint(self):
        poster, _ = fake_dispatcher({})
        s = BridgeSession(ConverseClient("http://x", poster), "+234", "c4")
        d1 = s.on_utterance("")
        self.assertIn("did not catch", d1["say"])
        d2 = s.on_utterance("   ")
        self.assertIn("press 1", d2["say"])
        self.assertFalse(d2["hangup"])  # never hang up

    def test_silence_never_hangs_up_open_call(self):
        poster, _ = fake_dispatcher({})
        s = BridgeSession(ConverseClient("http://x", poster), "+234", "c5")
        d = s.on_silence()
        self.assertTrue(d["listen"])
        self.assertFalse(d["hangup"])

    def test_silence_after_done_hangs_up_quietly(self):
        poster, _ = fake_dispatcher({"converse": [
            {"received": True, "done": True, "incident_id": "I", "say": "ok"}]})
        s = BridgeSession(ConverseClient("http://x", poster), "+234", "c6")
        s.on_utterance("help")
        d = s.on_silence()
        self.assertEqual(d, {"say": "", "listen": False, "hangup": True})

    def test_transport_failure_surface_as_retry_prompt(self):
        poster, _ = fake_dispatcher({})  # empty script -> received False
        s = BridgeSession(ConverseClient("http://x", poster), "+234", "c7")
        d = s.on_utterance("something")
        self.assertTrue(d["listen"])
        self.assertFalse(d["hangup"])

    def test_call_must_identify_itself(self):
        with self.assertRaises(Error):
            BridgeSession(ConverseClient("http://x", lambda p, x: {}), "", "c8")


if __name__ == "__main__":
    unittest.main()
