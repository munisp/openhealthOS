"""mdt.py — multidisciplinary team (tumour board) case conferences.

A cancer patient gets one chance at a correct first plan. The MDT is
where radiology, pathology, surgery and oncology meet; this module makes
that meeting auditable: cases require their evidence, decisions are
append-only per case, and a decision nobody executes is an open loop.

Doctrine: no case without its verified reports; decisions cite the
evidence; execution is tracked like teleicu actions.
"""

REQUIRED_MEMBER_ROLES = ("chair",)
QUORUM = 3


class MDTError(Exception):
    pass


class TumourBoard:
    def __init__(self, name):
        if not name:
            raise MDTError("board name required")
        self.name = name
        self.members = {}
        self.cases = {}
        self.meetings = []

    def add_member(self, member_id, *, role, specialty):
        if member_id in self.members:
            raise MDTError(f"duplicate member: {member_id}")
        self.members[member_id] = {"role": role, "specialty": specialty}
        return dict(self.members[member_id])

    def submit_case(self, case_id, *, patient_id, question,
                    evidence_refs, submitted_by, day):
        if case_id in self.cases:
            raise MDTError(f"duplicate case: {case_id}")
        if not question:
            raise MDTError("a board without a question is a social call")
        if not evidence_refs:
            raise MDTError(
                "no evidence refs — an MDT decides from verified "
                "reports (radiology/pathology), not from memory")
        self.cases[case_id] = {
            "patient_id": patient_id, "question": question,
            "evidence_refs": list(evidence_refs),
            "submitted_by": submitted_by, "day": day,
            "state": "awaiting-meeting", "decision": None}
        return dict(self.cases[case_id])

    def hold_meeting(self, meeting_id, *, day, chair, attendees,
                     case_ids):
        if chair not in self.members:
            raise MDTError(f"unknown chair: {chair}")
        if self.members[chair]["role"] != "chair":
            raise MDTError("only a registered chair convenes")
        unknown = [a for a in attendees if a not in self.members]
        if unknown:
            raise MDTError(f"unregistered attendees: {unknown}")
        if len(set(attendees)) < QUORUM:
            raise MDTError(
                f"quorum is {QUORUM} — a two-person 'board' is a "
                "corridor chat")
        for cid in case_ids:
            self._case(cid)
        m = {"meeting_id": meeting_id, "day": day, "chair": chair,
             "attendees": list(attendees), "case_ids": list(case_ids)}
        self.meetings.append(m)
        for cid in case_ids:
            self.cases[cid]["state"] = "in-meeting"
        return dict(m)

    def record_decision(self, meeting_id, case_id, *, day, decision,
                        actions, recorder):
        mtg = self._meeting(meeting_id)
        if recorder not in mtg["attendees"] and recorder != mtg["chair"]:
            raise MDTError("only an attendee records decisions")
        if case_id not in mtg["case_ids"]:
            raise MDTError("decision for a case not on this agenda")
        c = self._case(case_id)
        if c["decision"] is not None:
            raise MDTError(
                "decision already recorded — corrections are new "
                "decisions citing the old one, never edits")
        if not decision or not actions:
            raise MDTError("a decision without actions is a sentiment")
        c["decision"] = {"day": day, "decision": decision,
                         "actions": list(actions), "recorder": recorder,
                         "meeting_id": meeting_id}
        c["state"] = "decided"
        return dict(c["decision"])

    def execution_status(self, case_id, confirmed_actions):
        c = self._case(case_id)
        if not c["decision"]:
            raise MDTError("no decision yet")
        planned = c["decision"]["actions"]
        done = set(confirmed_actions)
        return {"planned": len(planned), "executed": len(done),
                "outstanding": [a for a in planned if a not in done]}

    def _case(self, cid):
        if cid not in self.cases:
            raise MDTError(f"unknown case: {cid}")
        return self.cases[cid]

    def _meeting(self, mid):
        for m in self.meetings:
            if m["meeting_id"] == mid:
                return m
        raise MDTError(f"unknown meeting: {mid}")
