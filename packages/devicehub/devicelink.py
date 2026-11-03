"""devicelink.py — the reliability layer between the wire and the
record: spool, dedupe, result lifecycle, feed health, identity.

Doctrine:
  - A NETWORK BLIP LOSES NOTHING: when the downstream link is down,
    results queue in an append-only spool and replay on reconnect.
    Three failed replays dead-letter the entry — loudly listed, never
    silently dropped.
  - RETRIES ARE SAFE: (message_id, content hash) dedupe means a
    re-sent message is acknowledged again, never double-posted.
  - THE RESULT HAS A LIFECYCLE: preliminary → final → corrected.
    Corrections supersede by append-only chain — the chain is the
    audit; `latest()` answers, the history stays.
  - SILENCE IS A FINDING: a feed that stops talking within its
    expected interval surfaces as stale; a feed that sends garbage is
    quarantined until a human clears it with a reason.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicehub import DeviceHubError  # noqa: E402

DEVICELINK_VERSION = "device-link@1.0.0"
MAX_REPLAY_ATTEMPTS = 3
RESULT_STATUSES = ("P", "F", "C", "X")  # prelim/final/corrected/cancelled


# ----------------------------------------------------------------
# Spool: store-and-forward. The PACS doctrine, generalized to devices.

class Spool:
    def __init__(self):
        self.entries = []  # append-only
        self._seq = 0

    def enqueue(self, session_id, direction, payload):
        self._seq += 1
        entry = {"spool_id": f"SP-{self._seq}", "session_id": session_id,
                 "direction": direction, "payload": payload,
                 "state": "queued", "attempts": 0}
        self.entries.append(entry)
        return entry

    def pending(self, session_id=None):
        return [e for e in self.entries if e["state"] == "queued"
                and (session_id is None
                     or e["session_id"] == session_id)]

    def dead_letters(self):
        return [e for e in self.entries if e["state"] == "dead"]

    def replay(self, session_id, deliver):
        """Try to deliver every queued entry for a session. Failures
        stay queued; MAX_REPLAY_ATTEMPTS strikes dead-letter the entry
        — a dead letter is a named loss, not a silent one."""
        replayed, failed = 0, 0
        for entry in self.pending(session_id):
            entry["attempts"] += 1
            try:
                deliver(entry["payload"])
                entry["state"] = "replayed"
                replayed += 1
            except Exception as exc:
                failed += 1
                if entry["attempts"] >= MAX_REPLAY_ATTEMPTS:
                    entry["state"] = "dead"
                    entry["dead_reason"] = str(exc)
        return {"session_id": session_id, "replayed": replayed,
                "failed": failed,
                "dead_total": len(self.dead_letters())}


# ----------------------------------------------------------------
# Dedupe + lifecycle: the result store.

def _content_hash(parsed):
    body = "|".join(f"{o.get('code')}={o.get('value')}"
                    f"@{o.get('timestamp','')}"
                    for o in parsed.get("observations", []))
    return hashlib.sha256(body.encode()).hexdigest()


class ResultStore:
    """Append-only result chains keyed by (patient, code, panel).

    post() returns a verdict dict: posted | duplicate | superseded |
    cancelled. Corrections (status C) append a new link and point
    `latest` at it; the old link stays — you cannot verify what you
    cannot see."""

    def __init__(self):
        self.chains = {}       # (patient, code, panel) -> [links]
        self.seen = {}         # (message_id, hash) -> verdict summary
        self.posted_total = 0

    def post(self, parsed):
        mid = parsed.get("message_id")
        if not mid:
            raise DeviceHubError("result message without a message id "
                                 "— undedupeable results are unsafe; "
                                 "refuse, don't hope")
        h = _content_hash(parsed)
        key = (mid, h)
        if key in self.seen:
            return {"verdict": "duplicate", "message_id": mid,
                    "note": "same message, same content — acknowledged "
                            "again, posted never; retries are safe "
                            "here"}
        verdicts = []
        for obs in parsed.get("observations", []):
            status = (obs.get("status") or "F")
            if status not in RESULT_STATUSES:
                raise DeviceHubError(
                    f"unknown result status {status!r} — the lifecycle "
                    f"vocabulary is {RESULT_STATUSES}")
            if status == "X":
                verdicts.append("cancelled")
                continue  # a cancellation posts nothing
            chain_key = (parsed.get("patient_id"), obs.get("code"),
                         obs.get("panel"))
            chain = self.chains.setdefault(chain_key, [])
            superseded = bool(chain) and status == "C"
            link = {"obs": dict(obs), "message_id": mid,
                    "supersedes": (chain[-1]["message_id"]
                                   if superseded else None)}
            chain.append(link)
            self.posted_total += 1
            verdicts.append("superseded" if superseded else "posted")
        summary = {"verdict": "posted" if any(v in ("posted",
                                                  "superseded")
                                              for v in verdicts)
                   else verdicts[0] if verdicts else "empty",
                   "message_id": mid, "observations": verdicts,
                   "content_hash": h}
        self.seen[key] = summary
        return summary

    def latest(self, patient_id, code, panel=None):
        chain = self.chains.get((patient_id, code, panel), [])
        return chain[-1]["obs"] if chain else None

    def history(self, patient_id, code, panel=None):
        return [dict(l) for l in
                self.chains.get((patient_id, code, panel), [])]


# ----------------------------------------------------------------
# Feed health: silence is a finding, garbage is a quarantine.

class FeedHealth:
    """Per-session liveness. expected_silence_s is per-deployment;
    a feed with no expectation is never called stale — and the report
    says that, rather than implying coverage we do not have."""

    def __init__(self):
        self.last_seen = {}     # session_id -> monotonic marker
        self.expectations = {}  # session_id -> max silence seconds
        self.quarantined = {}   # session_id -> {reason, by, at}
        self.parse_failures = {}  # session_id -> consecutive count

    QUARANTINE_AFTER_FAILURES = 3

    def set_expectation(self, session_id, max_silence_s):
        if max_silence_s <= 0:
            raise DeviceHubError("a non-positive silence expectation "
                                 "is no expectation")
        self.expectations[session_id] = max_silence_s

    def seen(self, session_id, at):
        self.last_seen[session_id] = at
        self.parse_failures[session_id] = 0

    def note_parse_failure(self, session_id):
        n = self.parse_failures.get(session_id, 0) + 1
        self.parse_failures[session_id] = n
        if n >= self.QUARANTINE_AFTER_FAILURES:
            self.quarantined[session_id] = {
                "reason": f"{n} consecutive parse failures — a feed "
                          "speaking garbage is quarantined until a "
                            "human looks at it",
                "automatic": True}

    def check(self, session_id):
        """Refuse loudly if quarantined. Called before every ingest."""
        if session_id in self.quarantined:
            q = self.quarantined[session_id]
            raise DeviceHubError(
                f"{session_id} is quarantined ({q['reason']}) — "
                "clear it with a reason, not a shrug")

    def clear_quarantine(self, session_id, *, by, reason):
        if session_id not in self.quarantined:
            raise DeviceHubError("no quarantine on this session — "
                                 "absence is not a clearance either")
        if not by or not reason:
            raise DeviceHubError("clearing a quarantine needs WHO and "
                                 "WHY — the audit is the point")
        del self.quarantined[session_id]
        self.parse_failures[session_id] = 0
        return {"session_id": session_id, "cleared_by": by,
                "reason": reason}

    def report(self, as_of):
        rows = []
        for sid, seen_at in sorted(self.last_seen.items()):
            exp = self.expectations.get(sid)
            silence = (as_of - seen_at).total_seconds()
            if sid in self.quarantined:
                state = "quarantined"
            elif exp is None:
                state = "unmonitored"  # stated, not implied
            else:
                state = "stale" if silence > exp else "live"
            rows.append({"session_id": sid,
                         "silent_for_s": silence,
                         "expected_silence_s": exp, "state": state})
        return {"feeds": rows,
                "stale": [r["session_id"] for r in rows
                          if r["state"] == "stale"],
                "quarantined": sorted(self.quarantined),
                "version": DEVICELINK_VERSION}
