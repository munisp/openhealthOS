"""OTA / airline embed widget — distribution where the traveller is.

A partner (airline, OTA, booking engine) embeds the widget with an
issued embed token; the sale flows through the partner door (vhsales)
and the revenue share splits at the moment of sale — the platform's
take and the partner's commission are stated ledger lines, never a
monthly surprise.

Embed tokens are origin-bound: a token presented from the wrong origin
refuses — a widget that can be lifted is a key that can be copied.
"""

import hashlib
import secrets

WIDGET_VERSION = "vhwidget@1.0.0"


class WidgetError(Exception):
    pass


def _refuse(msg):
    raise WidgetError(msg)


class WidgetRegistry:
    def __init__(self, partners, *, id_fn=None):
        """partners: the vhsales PartnerRegistry — the widget never
        bypasses the partner door."""
        self.partners = partners
        # IDs stay secrets-unique in production; demos and
        # reproducibility harnesses inject a deterministic id_fn
        # (ADR-0079 — the checksum is the contract, so a demo must
        # be able to walk the REAL flow and still be byte-stable).
        self._id_fn = id_fn or (lambda: secrets.token_hex(16))
        self.tokens = {}          # token -> record
        self.audit = []

    def issue_token(self, partner_id, api_key, *, origin, revenue_share_bps,
                    day):
        """Origin-bound embed token with a stated revenue share in
        basis points (1% = 100). The share is in the token record so
        every sale can show its split."""
        p = self.partners.authenticate(partner_id, api_key)
        if not origin.startswith("https://"):
            _refuse("the widget rides TLS or it does not ride")
        if not (0 < revenue_share_bps <= 3000):
            _refuse("revenue share outside 0-30% — the band is the mandate")
        token = "wgt_" + self._id_fn()
        self.tokens[token] = {
            "partner_id": partner_id, "origin": origin,
            "revenue_share_bps": revenue_share_bps,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "state": "active", "issued_day": day}
        self.audit.append({"event": "widget_token_issued",
                           "partner": partner_id, "origin": origin,
                           "day": day})
        return token

    def authenticate_token(self, token, *, origin):
        rec = self.tokens.get(token)
        if rec is None or rec["state"] != "active":
            _refuse("unknown or dead token — the widget is a credential")
        if rec["origin"] != origin:
            _refuse("origin mismatch — a widget that can be lifted is a "
                    "key that can be copied")
        return rec

    def split_sale(self, token, *, origin, gross_minor):
        """The revenue split, stated: platform_minor + partner_minor ==
        gross_minor, always — the remainder rides with the platform."""
        rec = self.authenticate_token(token, origin=origin)
        if gross_minor <= 0:
            _refuse("a zero sale splits nothing")
        partner_take = gross_minor * rec["revenue_share_bps"] // 10_000
        split = {"gross_minor": gross_minor,
                 "partner_minor": partner_take,
                 "platform_minor": gross_minor - partner_take,
                 "partner_id": rec["partner_id"],
                 "bps": rec["revenue_share_bps"]}
        assert split["partner_minor"] + split["platform_minor"] == gross_minor
        self.audit.append({"event": "widget_sale_split", **split})
        return split

    def report(self):
        return {"version": WIDGET_VERSION,
                "tokens": len(self.tokens),
                "active": sum(1 for t in self.tokens.values()
                              if t["state"] == "active"),
                "splits": sum(1 for a in self.audit
                              if a["event"] == "widget_sale_split")}
