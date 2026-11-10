"""vhsupport.py — the 24/7 helpdesk and the multilingual content layer:
the traveller in distress at 03:00 gets a ticket with a clock, and the
traveller reading their cover gets it in their language — with the
fallback stated, never silent.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.8/§6.7):
  - FR-HLP-01: 24/7 helpdesk — tickets by channel and category, SLA
    clocks for first response and resolution, breaches named.
  - FR-ADM-02: 5-language content (PT/EN/FR/DE/NL) — versioned
    bundles, per-language coverage with denominators, and a missing
    translation falls back WITH THE FALLBACK STATED.

Doctrines:
- An unanswered ticket past SLA is a named breach, not a full inbox.
- A language without full coverage reports its gaps; shipping a page
  silently half-translated is how a Portuguese traveller signs an
  English waiver.
"""

import secrets

VHSUPPORT_VERSION = "vhc-support@1.0.0"

HELP_RULES = {
    "vhc-support@1.0.0": {
        "channels": ("phone", "portal", "whatsapp", "email", "kiosk"),
        "categories": ("quote", "payment", "verification", "claim",
                       "gop", "exemption", "other"),
        "sla": {"urgent": {"first_response_h": 1, "resolve_h": 8},
                "standard": {"first_response_h": 4, "resolve_h": 48},
                "low": {"first_response_h": 24, "resolve_h": 120}},
    },
}

CONTENT_LANGS = ("pt", "en", "fr", "de", "nl")
DEFAULT_LANG = "en"

TICKET_STATES = ("open", "responded", "resolved", "closed")


class SupportError(Exception):
    pass


def _refuse(msg):
    raise SupportError(msg)


class Helpdesk:
    def __init__(self, *, rules_version="vhc-support@1.0.0", id_fn=None):
        if rules_version not in HELP_RULES:
            _refuse("unknown helpdesk rules version")
        self.rules = dict(HELP_RULES[rules_version])
        # Ticket IDs are deliberately unique in production (secrets).
        # Like the injectable clocks elsewhere, the ID source is an
        # injectable seam: demos and reproducibility harnesses pass a
        # deterministic id_fn; production never does.
        self._id_fn = id_fn or (lambda: secrets.token_hex(4).upper())
        self.tickets = {}
        self.audit = []

    def open_ticket(self, *, channel, category, priority, subject,
                    cover_ref=None, now_h, day):
        if channel not in self.rules["channels"]:
            _refuse("unknown channel — the door list is config")
        if category not in self.rules["categories"]:
            _refuse("unknown category — the triage list is versioned")
        if priority not in self.rules["sla"]:
            _refuse("unknown priority")
        if not subject:
            _refuse("a ticket without a subject is a shrug with a "
                    "timestamp")
        tid = "HLP-" + self._id_fn()
        if tid in self.tickets:
            _refuse("ticket id collision — the id source must be unique")
        self.tickets[tid] = {
            "ticket_id": tid, "channel": channel, "category": category,
            "priority": priority, "subject": subject,
            "cover_ref": cover_ref, "state": "open",
            "opened_h": now_h, "opened_day": day, "breaches": [],
            "responded_h": None, "resolved_h": None,
        }
        self.audit.append({"event": "ticket_opened", "ticket": tid,
                           "channel": channel, "priority": priority,
                           "day": day})
        return self.tickets[tid]

    def respond(self, ticket_id, *, by, note, now_h):
        t = self._t(ticket_id)
        if t["state"] != "open":
            _refuse("only an open ticket takes a first response")
        if not by or not note:
            _refuse("a response names the agent and the substance")
        elapsed = now_h - t["opened_h"]
        sla = self.rules["sla"][t["priority"]]
        if elapsed > sla["first_response_h"]:
            t["breaches"].append({"clock": "first_response",
                                  "elapsed_h": elapsed,
                                  "budget_h": sla["first_response_h"]})
        t["responded_h"] = now_h
        t["state"] = "responded"
        self.audit.append({"event": "ticket_responded",
                           "ticket": ticket_id, "by": by})
        return t

    def resolve(self, ticket_id, *, by, resolution, now_h):
        t = self._t(ticket_id)
        if t["state"] not in ("open", "responded"):
            _refuse("resolution serves open work")
        if not by or not resolution:
            _refuse("a resolution names who and what resolved it")
        elapsed = now_h - t["opened_h"]
        sla = self.rules["sla"][t["priority"]]
        if elapsed > sla["resolve_h"]:
            t["breaches"].append({"clock": "resolve",
                                  "elapsed_h": elapsed,
                                  "budget_h": sla["resolve_h"]})
        t["resolved_h"] = now_h
        t["state"] = "resolved"
        self.audit.append({"event": "ticket_resolved",
                           "ticket": ticket_id, "by": by})
        return t

    def close(self, ticket_id, *, by, day):
        t = self._t(ticket_id)
        if t["state"] != "resolved":
            _refuse("a ticket closes from resolved — closing open work "
                    "is how travellers fall out of the system")
        t["state"] = "closed"
        self.audit.append({"event": "ticket_closed", "ticket": ticket_id,
                           "by": by, "day": day})
        return t

    def report(self):
        total = len(self.tickets)
        return {"tickets": total,
                "by_state": {s: sum(1 for t in self.tickets.values()
                                    if t["state"] == s)
                             for s in TICKET_STATES},
                "first_response_sla": {
                    "compliant": sum(
                        1 for t in self.tickets.values()
                        if t["responded_h"] is not None and
                        not any(b["clock"] == "first_response"
                                for b in t["breaches"])),
                    "responded": sum(1 for t in self.tickets.values()
                                     if t["responded_h"] is not None)},
                "open_breaches": sum(
                    1 for t in self.tickets.values()
                    if t["breaches"] and t["state"] != "closed")}

    def _t(self, ticket_id):
        t = self.tickets.get(ticket_id)
        if t is None:
            _refuse("unknown ticket")
        return t


class ContentLayer:
    """5-language content: versioned bundles, stated fallbacks, and a
    coverage report that names its gaps."""

    def __init__(self):
        self.bundles = {}      # version -> {lang: {key: text}}

    def publish(self, version, *, lang, entries, by):
        if lang not in CONTENT_LANGS:
            _refuse("unknown language — the scheme speaks pt/en/fr/"
                    "de/nl")
        if not entries or not by:
            _refuse("a bundle names its publisher and its entries")
        self.bundles.setdefault(version, {})[lang] = dict(entries)
        return {"version": version, "lang": lang,
                "keys": len(entries)}

    def get(self, key, *, lang, version):
        bundle = self.bundles.get(version, {})
        if lang in bundle and key in bundle[lang]:
            return {"text": bundle[lang][key], "lang": lang,
                    "fallback": False}
        fallback = bundle.get(DEFAULT_LANG, {}).get(key)
        if fallback is None:
            _refuse("key absent even in the default language — a "
                    "content gap, not a render")
        return {"text": fallback, "lang": DEFAULT_LANG,
                "fallback": True,
                "note": "translation missing in %s — default shown, "
                        "stated" % lang}

    def coverage(self, version):
        bundle = self.bundles.get(version, {})
        universe = set()
        for entries in bundle.values():
            universe |= set(entries)
        per_lang = {}
        for lang in CONTENT_LANGS:
            have = set(bundle.get(lang, {}))
            per_lang[lang] = {"translated": len(have & universe),
                              "total_keys": len(universe),
                              "missing": sorted(universe - have)}
        return {"version": version, "languages": per_lang}
