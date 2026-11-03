"""astm.py — ASTM E1381/E1394 codec for the analyzer lane.

Older lab analyzers (and many point-of-care devices in exactly the
facilities this platform serves) speak ASTM, not HL7: ENQ to open,
checksum-verified data frames, EOT to close. The frame syntax is an
open, implementable convention; this codec is clean-room.

Doctrine:
  - the checksum is the contract: a frame whose checksum fails is
    NAK'd in spirit — refused, never half-parsed;
  - frame sequence numbers must be consecutive — a gap is a lost
    frame, and a lost frame is a finding;
  - result records (R) normalize into the same observation shape as
    the HL7 lane: one platform shape, two wires.
"""
STX, ETX, CR, LF = "\x02", "\x03", "\r", "\n"
ENQ, ACK, NAK, EOT = "\x05", "\x06", "\x15", "\x04"


class ASTMError(Exception):
    pass


def checksum(frame_body: str) -> str:
    """Two hex digits: sum of all bytes of 'FN text CR ETX' mod 256."""
    return f"{sum(frame_body.encode('ascii')) % 256:02X}"


def build_frame(seq: int, text: str) -> str:
    if not (0 <= seq <= 7):
        raise ASTMError("ASTM frame numbers are mod-8 — a sequence "
                        "outside 0..7 is not a frame")
    body = f"{seq}{text}{CR}{ETX}"
    return f"{STX}{body}{checksum(body)}{CR}{LF}"


def parse_frame(raw: str) -> dict:
    """One frame → {seq, text}. Checksum failure refuses."""
    if not raw.startswith(STX):
        raise ASTMError("frame without STX — not ASTM, or corrupted "
                        "in transit")
    try:
        etx = raw.index(ETX)
    except ValueError:
        raise ASTMError("frame without ETX — truncated")
    body = raw[1:etx + 1]
    sent = raw[etx + 1:etx + 3]
    if len(sent) != 2 or sent.upper() != checksum(body):
        raise ASTMError(f"checksum mismatch: frame says {sent!r}, "
                        f"bytes say {checksum(body)} — the checksum "
                        "is the contract; refuse, don't resync by "
                        "guessing")
    return {"seq": int(body[0]), "text": body[1:-2]}


def parse_session(stream: str) -> dict:
    """A whole ASTM session: ENQ ... frames ... EOT.

    Returns {records: [(type, fields)], observations: [...],
    frames: n}. Sequence gaps refuse — a missing frame is a missing
    result, and the session must say so."""
    if ENQ not in stream or EOT not in stream:
        raise ASTMError("session without ENQ/EOT framing — the "
                        "analyzer did not finish talking")
    body = stream[stream.index(ENQ) + 1: stream.rindex(EOT)]
    frames, i = [], 0
    while i < len(body):
        if body[i] != STX:
            raise ASTMError("noise between frames — refuse, don't "
                            "skip bytes you cannot name")
        end = body.index(LF, i)
        frames.append(parse_frame(body[i:end + 1]))
        i = end + 1
    for prev, cur in zip(frames, frames[1:]):
        if cur["seq"] != (prev["seq"] + 1) % 8:
            raise ASTMError(
                f"frame sequence gap: {prev['seq']} → {cur['seq']} — "
                "a lost frame is a lost result; retransmit, don't "
                "reconcile by hope")
    records = [f["text"].split("|") for f in frames]
    observations = []
    patient_id = None
    for rec in records:
        if rec[0] == "P":
            patient_id = rec[3] if len(rec) > 3 else None
        elif rec[0] == "R":
            # R|seq|code^^^|value|units|...|abnormal|...|status
            if len(rec) < 4 or not rec[3]:
                raise ASTMError("result record without a value — "
                                "absence is not a value")
            test_id = rec[2].split("^") if len(rec) > 2 else [""]
            # ASTM test ids carry the local code in component 4
            # (^^^CODE) when present
            code = test_id[0] or (test_id[3] if len(test_id) > 3
                                  else "")
            observations.append({
                "code": code,
                "value": rec[3],
                "units": rec[4] if len(rec) > 4 else "",
                "abnormal_flag": rec[6] if len(rec) > 6 else "",
                "status": (rec[9] if len(rec) > 9 else "") or "F",
                "panel": None,
            })
    return {"records": records, "observations": observations,
            "patient_id": patient_id, "frames": len(frames)}
