"""vhsettle.py — daily settlement files and reconciliation: the platform's
ledger and the PSP's file meet every day, and any disagreement is a named
break, measured against a tolerance, never rounded into agreement.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.2):
  - FR-PAY-02: duplicate charges are auto-refunded — idempotency prevents
    the race, and when a duplicate slips through anyway, the refund is
    automatic and reasoned, not a support ticket.
  - FR-PAY-03: daily settlement file per PSP (rows, totals, checksum) and
    reconciliation with break detection against a 0.1% tolerance —
    BO-5's number, kept as config.

Doctrines:
- A reconciliation that cannot name its breaks is a hope, not a control.
- The checksum is the contract: a settlement file carries its sha256.
- A break rate past tolerance is a named breach with denominators —
  never a quiet write-off.
- An auto-refund carries the evidence of the duplicate; a refund
  without evidence is a gift.
- Currency is a dimension of the money: mixed-currency sums refuse,
  and a settlement file is per currency, never a blended number.
"""

import hashlib
import json
import secrets

VHSETTLE_VERSION = "vhc-settle@1.0.0"

SETTLE_RULES = {
    "vhc-settle@1.0.0": {
        # 0.1% expressed as parts per million — the tolerance is config
        "break_tolerance_ppm": 1000,
        "duplicate_window_days": 1,
    },
}


class SettleError(Exception):
    pass


def _refuse(msg):
    raise SettleError(msg)


def _row_currency(t):
    return t.get("currency")


def _filter_currency(rows, currency, *, side):
    """Currency is a dimension of the money, not an annotation. With an
    explicit currency, keep only its rows; without one, mixed-currency
    input refuses — a sum over two currencies is not a number."""
    present = {_row_currency(r) for r in rows if _row_currency(r)}
    if currency is not None:
        foreign = sorted(c for c in present if c != currency)
        if foreign and side == "psp":
            _refuse("PSP file mixes currencies (%s) into a %s "
                    "reconciliation — split the file per currency"
                    % ("/".join(foreign), currency))
        return [r for r in rows
                if _row_currency(r) in (None, currency)]
    if len(present) > 1:
        _refuse("mixed-currency sums refuse — currencies present: %s; "
                "settle per currency" % "/".join(sorted(present)))
    return list(rows)


def settlement_file(txns, *, day, psp, currency=None, id_fn=None):
    """The daily file for one PSP: rows, totals, checksum. `txns` are
    the platform's ledger rows: {ref, amount_minor, psp, day,
    currency?}. The file is per currency — a mixed-currency sum is
    refused, never rounded into a single number."""
    mine = [t for t in txns
            if t.get("psp") == psp and t.get("day") == day]
    mine = _filter_currency(mine, currency, side="platform")
    rows = sorted(({"ref": t["ref"], "amount_minor": t["amount_minor"]}
                   for t in mine),
                  key=lambda r: r["ref"])
    currencies = sorted({_row_currency(t) for t in mine
                         if _row_currency(t)})
    _id = id_fn or (lambda: secrets.token_hex(3))
    body = {"file_id": "STL-%s-%d-%s" % (psp, day,
                                         _id().upper()),
            "psp": psp, "day": day,
            "currency": currency or (currencies[0]
                                     if currencies else None),
            "rows": rows,
            "count": len(rows),
            "total_minor": sum(r["amount_minor"] for r in rows)}
    body["file_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def reconcile(txns, *, day, psp, psp_rows, currency=None,
              rules_version="vhc-settle@1.0.0"):
    """The platform ledger meets the PSP's file. Every break is named:
    missing on the PSP side, missing on the platform side, or an amount
    mismatch. The break rate is measured in ppm against the tolerance —
    past tolerance is a breach, stated, not smoothed. Reconciliation is
    per currency: a PSP row in the wrong currency refuses the file."""
    rules = SETTLE_RULES.get(rules_version)
    if rules is None:
        _refuse("unknown settle rules version")
    ours_txns = _filter_currency(
        [t for t in txns if t.get("psp") == psp and t.get("day") == day],
        currency, side="platform")
    ours = {t["ref"]: t["amount_minor"] for t in ours_txns}
    psp_rows = _filter_currency(list(psp_rows), currency, side="psp")
    theirs = {}
    for row in psp_rows:
        if not row.get("ref") or row.get("amount_minor") is None:
            _refuse("a PSP row without ref and amount is not a row")
        if row["ref"] in theirs:
            _refuse("duplicate ref in the PSP file — their file, "
                    "their finding")
        theirs[row["ref"]] = row["amount_minor"]
    breaks = []
    for ref, amt in sorted(ours.items()):
        if ref not in theirs:
            breaks.append({"ref": ref, "kind": "missing-in-psp",
                           "platform_minor": amt})
        elif theirs[ref] != amt:
            breaks.append({"ref": ref, "kind": "amount-mismatch",
                           "platform_minor": amt,
                           "psp_minor": theirs[ref]})
    for ref, amt in sorted(theirs.items()):
        if ref not in ours:
            breaks.append({"ref": ref, "kind": "missing-in-platform",
                           "psp_minor": amt})
    total = max(len(ours), len(theirs))
    rate_ppm = (len(breaks) * 1_000_000 // total) if total else 0
    breach = bool(total) and rate_ppm > rules["break_tolerance_ppm"]
    return {"day": day, "psp": psp, "currency": currency, "breaks": breaks,
            "break_count": len(breaks), "txn_count": total,
            "break_rate_ppm": rate_ppm,
            "tolerance_ppm": rules["break_tolerance_ppm"],
            "breach": breach,
            "note": ("break rate past tolerance — escalate to the PSP "
                     "with this file" if breach else
                     "within tolerance" if total else
                     "no transactions — stated, not assumed")}


def auto_refund_duplicates(txns, *, day, window_days=None,
                           rules_version="vhc-settle@1.0.0",
                           id_fn=None):
    """S-008: the same payer charged the same amount twice inside the
    window is a duplicate — keep the first, refund the rest, each
    refund carrying the evidence. Idempotency prevents the race; this
    is the net under the tightrope."""
    rules = SETTLE_RULES.get(rules_version)
    if rules is None:
        _refuse("unknown settle rules version")
    window = window_days if window_days is not None \
        else rules["duplicate_window_days"]
    seen, refunds = {}, []
    for t in sorted(txns, key=lambda t: (t.get("day", 0), t["ref"])):
        # the same amount in two currencies is not a duplicate — the
        # currency rides in the dedup key
        key = (t.get("payer_key"), t["amount_minor"],
               t.get("currency"))
        first = seen.get(key)
        if first is not None and t["day"] - first["day"] <= window:
            refunds.append({
                "refund_ref": "ARF-" + (id_fn or (lambda: secrets.token_hex(4)))().upper(),
                "duplicate_ref": t["ref"], "kept_ref": first["ref"],
                "amount_minor": t["amount_minor"], "day": day,
                "reason": "duplicate charge — same payer, same amount, "
                          "inside %dd window (S-008)" % window,
            })
        else:
            seen[key] = t
    return refunds
