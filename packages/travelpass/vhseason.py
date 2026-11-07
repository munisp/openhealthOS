"""Season-aware pricing and surveillance-driven prevention.

Two governed outputs, one module:

1. Seasonal tariff tables — versioned entries keyed by (profile,
   season window); the effective price reads the table in force on the
   day, never reprices a bought policy mid-term.
2. Prevention campaigns — a BR-10 surveillance alert with named
   acknowledgement can trigger a versioned prevention push through the
   notification outbox, funded from the prevention bucket. An
   unacknowledged alert triggers nothing — you do not spend past a
   red finding either.
"""

import hashlib
import json

SEASON_VERSION = "vhseason@1.0.0"


class SeasonError(Exception):
    pass


def _refuse(msg):
    raise SeasonError(msg)


class SeasonalTables:
    """(profile_id) -> versioned seasons: [{from_day, to_day, factor,
    version, ratified_by, approved_by}]. Four-eyes on every table."""

    def __init__(self):
        self.tables = {}

    def ratify_table(self, profile_id, *, seasons, version, ratified_by,
                     approved_by, day):
        if not ratified_by or not approved_by or ratified_by == approved_by:
            _refuse("the hand that proposes never approves")
        if not seasons:
            _refuse("an empty table prices nothing")
        for s in seasons:
            if s["to_day"] <= s["from_day"]:
                _refuse("a season whose end precedes its start is not "
                        "a season")
            if s["factor"] <= 0:
                _refuse("a zero factor prices cover at zero — refused")
        if profile_id in self.tables and \
                version <= self.tables[profile_id]["version"]:
            _refuse("a tariff table moves forward or not at all")
        self.tables[profile_id] = {"version": version,
                                   "seasons": list(seasons),
                                   "ratified_by": ratified_by,
                                   "approved_by": approved_by,
                                   "day": day}
        return dict(self.tables[profile_id])

    def factor_for(self, profile_id, *, day):
        t = self.tables.get(profile_id)
        if t is None:
            return 1.0                    # no table: profile base price
        for s in t["seasons"]:
            if s["from_day"] <= day < s["to_day"]:
                return s["factor"]
        return 1.0                        # outside every season: base

    def effective_price(self, profile_id, base_minor, *, day):
        return int(round(base_minor * self.factor_for(profile_id, day=day)))


class PreventionCampaigns:
    """BR-10 acknowledged -> versioned campaign through the outbox."""

    def __init__(self, outbox):
        self.outbox = outbox      # vhnotify.NotificationOutbox
        self.campaigns = []

    def trigger(self, *, alert, channel, lang, day, funded_by):
        if not alert.get("acknowledged"):
            _refuse("the alert is unacknowledged — you do not spend "
                    "past a red finding")
        if not funded_by:
            _refuse("a campaign without a funder is a wish — the "
                    "prevention bucket signs")
        camp = {"alert_ref": alert.get("ref"),
                "channel": channel, "lang": lang, "day": day,
                "funded_by": funded_by}
        canon = json.dumps(camp, sort_keys=True).encode()
        camp["campaign_sha256"] = hashlib.sha256(canon).hexdigest()
        self.outbox.emit("prevention-campaign", ref=alert.get("ref"),
                         channel=channel, lang=lang, day=day,
                         detail={"campaign": camp["campaign_sha256"]})
        self.campaigns.append(camp)
        return dict(camp)

    def report(self):
        return {"version": SEASON_VERSION,
                "campaigns": len(self.campaigns)}


def report(tables, campaigns):
    return {"version": SEASON_VERSION,
            "tables": len(tables.tables),
            "campaigns": len(campaigns.campaigns)}
