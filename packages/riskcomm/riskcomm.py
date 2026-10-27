"""riskcomm.py — risk communication with receipt tracking.

The public advisory on a webpage reaches whoever reads webpages. During
an outbreak the platform must push targeted, language-matched messages
to named cohorts — contacts under monitoring, facilities in an affected
LGA, border staff — and track ACKNOWLEDGEMENT, because the honest metric
is delivered != read != understood, and pretending otherwise gets people
killed by assumption.

Doctrine:
  - broadcasts are versioned artifacts (a message that changed mid-
    outbreak is two messages, each auditable);
  - per-recipient states: queued -> delivered -> acknowledged; "read"
    and "understood" are NOT claimed — only an explicit ack action moves
    a recipient to acknowledged;
  - quiet is honest: the report names who never acked — the
    unacknowledged cohort IS the follow-up work list;
  - languages are constrained to the platform i18n set; a message that
    cannot be rendered in the recipient's language is queued in the
    default and FLAGGED, never silently English.
"""
from __future__ import annotations

LANGUAGES = ("en", "pcm", "ha", "yo", "ig")


class RiskCommError(Exception):
    pass


class Campaign:
    def __init__(self, campaign_id: str, version: str,
                 messages: dict[str, str]):
        if "en" not in messages:
            raise RiskCommError("an English rendering is the minimum — "
                                "no default, no broadcast")
        bad = set(messages) - set(LANGUAGES)
        if bad:
            raise RiskCommError(f"unsupported languages: {sorted(bad)}")
        self.id = campaign_id
        self.version = version
        self.messages = messages
        self.recipients = {}  # id -> {lang, state, flagged_language}

    def target(self, recipient_id: str, language: str = "en"):
        if language not in LANGUAGES:
            raise RiskCommError(f"unsupported language: {language}")
        if recipient_id in self.recipients:
            raise RiskCommError(f"duplicate recipient: {recipient_id}")
        self.recipients[recipient_id] = {
            "lang": language, "state": "queued",
            "flagged_language": language not in self.messages}

    def mark_delivered(self, recipient_id: str):
        r = self._r(recipient_id)
        if r["state"] != "queued":
            raise RiskCommError(f"cannot deliver from {r['state']}")
        r["state"] = "delivered"

    def acknowledge(self, recipient_id: str):
        """The recipient's explicit action. Only this moves to acked."""
        r = self._r(recipient_id)
        if r["state"] not in ("queued", "delivered"):
            raise RiskCommError(f"already {r['state']}")
        r["state"] = "acknowledged"

    def message_for(self, recipient_id: str) -> dict:
        r = self._r(recipient_id)
        lang = r["lang"] if r["lang"] in self.messages else "en"
        return {"text": self.messages[lang], "language": lang,
                "language_flagged": r["flagged_language"]}

    def report(self) -> dict:
        states = {}
        for r in self.recipients.values():
            states[r["state"]] = states.get(r["state"], 0) + 1
        unacked = sorted(rid for rid, r in self.recipients.items()
                         if r["state"] != "acknowledged")
        return {
            "campaign": self.id, "version": self.version,
            "recipients": len(self.recipients),
            "by_state": states,
            "language_fallbacks": sum(1 for r in self.recipients.values()
                                      if r["flagged_language"]),
            # delivered != read != understood: only acked counts
            "acknowledged": states.get("acknowledged", 0),
            "unacknowledged_followup_list": unacked,
            "honest_note": "delivered means the device confirmed receipt; "
                           "acknowledged means the person tapped. Nothing "
                           "here claims anyone understood."}

    def _r(self, rid):
        if rid not in self.recipients:
            raise RiskCommError(f"no such recipient: {rid}")
        return self.recipients[rid]
