"""restock.py — auto-drafted restock orders from consumption velocity.

The user edits quantities and taps once; nobody retypes 60 line items.
Velocity is learned from the facility's own consumption records —
aggregate stock data, zero patient content by construction.

Honesty rules:
- The draft shows its working: current stock, daily velocity, days of
  cover, and the target. A quantity with no derivation is a guess; a
  guess is never drafted.
- New items (no history) are listed as "no-velocity" — flagged for a
  human, never auto-filled with a fake number.
- Negative stock (data corruption) fails closed as an exception row.
"""

TARGET_DAYS = 30     # restock to a month of cover
REORDER_DAYS = 14    # draft when cover drops under a fortnight
ROUND_TO = 10        # pharmacy reality: round packs


def velocity(history):
    """Daily consumption from [(day_ts, qty)] records. Needs >= 2 points
    spanning >= 3 days, else honest None."""
    if len(history) < 2:
        return None
    history = sorted(history)
    span = (history[-1][0] - history[0][0]) / 86400
    if span < 3:
        return None
    return sum(q for _, q in history) / span


def draft(stock, consumption, target_days=TARGET_DAYS,
          reorder_days=REORDER_DAYS, round_to=ROUND_TO):
    """stock: {item: on_hand}; consumption: {item: [(ts, qty), ...]}.
    Returns {lines, no_velocity, exceptions} — never a bare order."""
    lines, no_vel, exc = [], [], []
    for item, on_hand in sorted(stock.items()):
        if on_hand < 0:
            exc.append({"item": item, "reason": "negative-stock-data-error"})
            continue
        v = velocity(consumption.get(item, []))
        if v is None or v <= 0:
            no_vel.append(item)
            continue
        cover = on_hand / v
        if cover >= reorder_days:
            continue  # healthy stock is not drafted — silence is correct
        need = v * target_days - on_hand
        qty = int(-(-need // round_to)) * round_to  # ceil to pack size
        lines.append({"item": item, "suggested_qty": qty,
                      "derivation": {"on_hand": on_hand,
                                     "daily_velocity": round(v, 3),
                                     "days_cover": round(cover, 1),
                                     "target_days": target_days}})
    return {"lines": lines, "no_velocity": no_vel, "exceptions": exc,
            "state": "draft"}  # a draft, until a human confirms
