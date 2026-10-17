import unittest

from triage import (ApprovalGate, Decision, DecisionOutcome, triage)


class TestTriageProtocols(unittest.TestCase):
    def test_obstetric_danger_is_p0_emergency(self):
        p = triage({"pregnant", "vaginal_bleeding"}, "PT-1")
        self.assertEqual(p.content["assessment"]["disposition"], "REFER-EMERGENCY")
        self.assertEqual(p.content["assessment"]["priority"], "P0")

    def test_severe_malaria_refers_urgent(self):
        p = triage({"fever", "convulsions"}, "PT-2")
        self.assertEqual(p.content["assessment"]["protocol"], "MALARIA-SEVERE")

    def test_uncomplicated_malaria_treats_local(self):
        p = triage({"fever"}, "PT-3")
        self.assertEqual(p.content["assessment"]["disposition"], "TREAT-LOCAL")

    def test_no_protocol_is_routine(self):
        p = triage({"rash"}, "PT-4")
        self.assertEqual(p.content["assessment"]["disposition"], "ROUTINE")

    def test_priority_orders_multiple_fires(self):
        # fever+convulsions also matches uncomplicated; P1 must win over P3
        p = triage({"fever", "convulsions", "cough", "fast_breathing"}, "PT-5")
        self.assertEqual(p.content["assessment"]["priority"], "P1")
        self.assertIn("PNEUMONIA", p.content["assessment"]["also_fired"])


class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.gate = ApprovalGate()
        self.p = triage({"fever"}, "PT-1")
        self.gate.propose(self.p)

    def test_commit_without_decision_is_impossible(self):
        with self.assertRaises(ValueError):
            self.gate.commit(self.p.id, Decision(self.p.id, "", DecisionOutcome.APPROVE))

    def test_rejected_proposal_never_commits(self):
        d = Decision(self.p.id, "NURSE-01", DecisionOutcome.REJECT)
        self.gate.decide(d)
        with self.assertRaises(ValueError):
            self.gate.commit(self.p.id, d)
        self.assertIsNone(self.gate.record_of(self.p.id))

    def test_approval_commits_with_provenance(self):
        d = Decision(self.p.id, "NURSE-01", DecisionOutcome.APPROVE)
        self.gate.decide(d)
        c = self.gate.commit(self.p.id, d)
        self.assertEqual(c.record["provenance"]["decided_by"], "NURSE-01")
        self.assertEqual(c.record["provenance"]["proposed_by"], self.p.author)
        self.assertFalse(c.record["provenance"]["edited"])

    def test_edit_records_clinician_override(self):
        edited = {"protocol": "NONE", "disposition": "ROUTINE", "priority": "P4",
                  "note": "clinician override: afebrile on arrival"}
        d = Decision(self.p.id, "NURSE-01", DecisionOutcome.EDIT, edited_content=edited)
        self.gate.decide(d)
        c = self.gate.commit(self.p.id, d)
        self.assertEqual(c.record["assessment"]["note"], "clinician override: afebrile on arrival")
        self.assertTrue(c.record["provenance"]["edited"])

    def test_audit_chain_is_complete(self):
        d = Decision(self.p.id, "NURSE-01", DecisionOutcome.APPROVE)
        self.gate.decide(d)
        self.gate.commit(self.p.id, d)
        events = [e["event"] for e in self.gate.audit_log]
        self.assertEqual(events, ["PROPOSAL", "DECISION", "COMMIT"])

    def test_decision_on_unknown_proposal_fails(self):
        with self.assertRaises(ValueError):
            self.gate.decide(Decision("prop-nope", "NURSE-01", DecisionOutcome.APPROVE))


if __name__ == "__main__":
    unittest.main()
