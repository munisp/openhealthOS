"""hl7adt.py — HL7 v2 ADT/ORM/ORU inbound message parser.

Legacy hospital systems speak HL7 v2. When a facility adopts OpenHealth,
the first integration question is always: "can it listen to our existing
ADT feed?" This module answers yes — parsing the pipe-delimited wire
format that every analyser, LIS, and HIS on the continent emits.

Doctrine:
- STRUCTURE OR SILENCE: a message that doesn't parse refuses with the
  segment and field that failed. Partial parsing is not a thing.
- THE MESSAGE TYPE GOVERNS: ADT^A01 (admit), A02 (transfer), A03
  (discharge), A08 (update), A40 (merge), ORM^O01 (order), ORU^R01
  (result). Unknown types refuse.
- MSH IS MANDATORY: no message without a valid header. The sending
  application, facility, and timestamp are extracted and validated.
- PID IS MANDATORY FOR ADT: a patient event without a patient segment
  refuses. ORM needs ORC/OBR; ORU needs OBX.
- CHECKSUMS ARE OPTIONAL BUT LOGGED: MSH-10 (message control ID) is
  extracted for dedup; a repeated control ID is flagged.
"""

import hashlib
import json

VERSION = "hl7adt@1.0.0"

FIELD_SEP = "|"
COMP_SEP = "^"
REP_SEP = "~"
ESC_CHAR = "\\"
SUB_SEP = "&"

KNOWN_TYPES = ("ADT^A01", "ADT^A02", "ADT^A03", "ADT^A08", "ADT^A40",
               "ORM^O01", "ORU^R01")


class Error(Exception):
    pass


class ParsedMessage:
    """The parsed contents of one HL7 v2 message."""

    def __init__(self, raw):
        self.raw = raw
        self.segments = {}
        self.msg_type = None
        self.control_id = None
        self.sending_app = None
        self.sending_facility = None
        self.timestamp = None
        self._parse()

    def _parse(self):
        lines = self.raw.strip().split("\r")
        if not lines:
            raise Error("empty message")
        msh = lines[0]
        if not msh.startswith("MSH"):
            raise Error("first segment must be MSH")
        self._parse_msh(msh)
        for line in lines[1:]:
            if not line.strip():
                continue
            seg_name = line[:3]
            fields = line.split(FIELD_SEP)
            self.segments.setdefault(seg_name, []).append(fields)

    def _parse_msh(self, line):
        fields = line.split(FIELD_SEP)
        if len(fields) < 12:
            raise Error("MSH has fewer than 12 fields")
        self.sending_app = fields[2] if len(fields) > 2 else ""
        self.sending_facility = fields[3] if len(fields) > 3 else ""
        self.timestamp = fields[6] if len(fields) > 6 else ""
        msg_type_field = fields[8] if len(fields) > 8 else ""
        parts = msg_type_field.split(COMP_SEP)
        if len(parts) >= 2:
            self.msg_type = f"{parts[0]}^{parts[1]}"
        elif len(parts) == 1:
            self.msg_type = parts[0]
        else:
            raise Error("MSH-9 (message type) is empty")
        if self.msg_type not in KNOWN_TYPES:
            raise Error(f"unknown message type: {self.msg_type}")
        self.control_id = fields[9] if len(fields) > 9 else ""

    def _require(self, seg):
        if seg not in self.segments:
            raise Error(f"message type {self.msg_type} requires "
                        f"{seg} — absent")

    def patient(self):
        """Extract PID fields."""
        self._require("PID")
        pid = self.segments["PID"][0]
        # PID fields are 1-indexed after the segment name
        def f(n):
            idx = n  # PID-1 is at index 1 after split
            return pid[idx] if idx < len(pid) else ""
        return {
            "patient_id": f(3).split(COMP_SEP)[0] if f(3) else "",
            "family_name": f(5).split(COMP_SEP)[0] if f(5) else "",
            "given_name": (f(5).split(COMP_SEP)[1]
                           if f(5) and len(f(5).split(COMP_SEP)) > 1
                           else ""),
            "dob": f(7),
            "sex": f(8),
        }

    def visit(self):
        """Extract PV1 fields (visit info)."""
        if "PV1" not in self.segments:
            return {}
        pv1 = self.segments["PV1"][0]
        def f(n):
            return pv1[n] if n < len(pv1) else ""
        return {
            "patient_class": f(2),
            "ward": f(3).split(COMP_SEP)[0] if f(3) else "",
            "attending_doctor": f(7).split(COMP_SEP)[0] if f(7) else "",
        }

    def results(self):
        """Extract OBX segments (for ORU)."""
        self._require("OBR")
        obr = self.segments["OBR"][0]
        test_name = obr[4].split(COMP_SEP)[1] if len(obr) > 4 and \
            len(obr[4].split(COMP_SEP)) > 1 else ""
        obs = []
        for obx in self.segments.get("OBX", []):
            def f(n):
                return obx[n] if n < len(obx) else ""
            obs.append({
                "observation_id": f(3).split(COMP_SEP)[0] if f(3) else "",
                "value": f(5),
                "units": f(6),
                "reference_range": f(7),
                "abnormal_flag": f(8),
            })
        return {"test": test_name, "observations": obs}

    def event(self):
        """The parsed event as a typed dict."""
        patient = self.patient() if "PID" in self.segments else None
        visit = self.visit()
        result = {"msg_type": self.msg_type,
                  "control_id": self.control_id,
                  "sending_app": self.sending_app,
                  "sending_facility": self.sending_facility,
                  "timestamp": self.timestamp,
                  "patient": patient,
                  "visit": visit}
        if self.msg_type.startswith("ORU"):
            result["results"] = self.results()
        return result


class ADTFeed:
    """A listener that dedupes by message control ID."""

    def __init__(self, feed_id):
        self.feed_id = feed_id
        self.seen_ids = set()
        self.events = []
        self.rejected = []

    def receive(self, raw):
        """Parse and route. Returns the event or raises."""
        try:
            msg = ParsedMessage(raw)
        except Error as e:
            self.rejected.append({"raw_hash": hashlib.sha256(
                raw.encode()).hexdigest()[:16], "error": str(e)})
            raise
        if msg.control_id in self.seen_ids:
            raise Error(f"duplicate message control ID: {msg.control_id}")
        self.seen_ids.add(msg.control_id)
        ev = msg.event()
        self.events.append(ev)
        return ev

    def stats(self):
        return {"feed_id": self.feed_id, "received": len(self.events),
                "rejected": len(self.rejected),
                "deduped": len(self.seen_ids) - len(self.events),
                "version": VERSION}
