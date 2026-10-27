"""stockledger.py — essential medicines stock ledger.

A correctly-classified child still dies when the amoxicillin shelf is
empty. Every protocol pack prescribes; nothing checked whether the
medicine existed. This is the honest shelf: append-only movements,
never-negative balances, stock-outs surfaced as loudly as an overdue
contact.

Doctrine:
- BALANCES NEVER GO NEGATIVE: issuing more than the balance raises —
  a negative shelf is a data lie, and data lies kill procurement.
- APPEND-ONLY: movements are never edited; corrections are reversing
  entries that cite what they reverse.
- STOCK-OUT IS AN EVENT, NOT A ZERO: the day the balance hits zero is
  recorded with the item; the stock-out REPORT names days-out so a
  month of zeros can't hide inside an average.
- DAYS-OF-STOCK from actual consumption rate, with "learning" said
  out loud when history is too thin.
"""

REORDER_DAYS = 14      # below this many days of stock -> reorder flag
MIN_RATE_HISTORY = 7   # days of issue history before a rate is claimed


class StockError(Exception):
    pass


class Ledger:
    def __init__(self, facility):
        self.facility = facility
        self.entries = []            # append-only
        self.stockout_days = {}      # item -> [days at zero with demand]

    def _balance(self, item, upto_day=None):
        bal = 0
        for e in self.entries:
            if e["item"] != item:
                continue
            if upto_day is not None and e["day"] > upto_day:
                continue
            bal += e["qty"] if e["kind"] in ("receive", "correct-in") else -e["qty"]
        return bal

    def receive(self, item, qty, *, day, source, batch=None, expiry_day=None):
        if qty <= 0:
            raise StockError("receive qty must be positive")
        e = {"kind": "receive", "item": item, "qty": qty, "day": day,
             "source": source, "batch": batch, "expiry_day": expiry_day}
        self.entries.append(e)
        return dict(e)

    def issue(self, item, qty, *, day, to=None, protocol_action=None):
        if qty <= 0:
            raise StockError("issue qty must be positive")
        bal = self._balance(item)
        if qty > bal:
            # demand unmet at zero (or below) is a stock-out event
            if bal == 0:
                self.stockout_days.setdefault(item, [])
                if day not in self.stockout_days[item]:
                    self.stockout_days[item].append(day)
            raise StockError(
                f"cannot issue {qty} of {item}: balance {bal} "
                f"(fail-closed — shelf never goes negative)")
        e = {"kind": "issue", "item": item, "qty": qty, "day": day,
             "to": to, "protocol_action": protocol_action}
        self.entries.append(e)
        if self._balance(item) == 0:
            self.stockout_days.setdefault(item, [])
            if day not in self.stockout_days[item]:
                self.stockout_days[item].append(day)
        return dict(e)

    def correct(self, entry_index, *, day, reason, officer):
        """Reversing entry, citing the original. Never an edit."""
        try:
            orig = self.entries[entry_index]
        except IndexError:
            raise StockError(f"no entry #{entry_index}")
        if not reason:
            raise StockError("corrections require a reason")
        kind = "correct-in" if orig["kind"] in ("issue",) else "correct-out"
        e = {"kind": kind, "item": orig["item"], "qty": orig["qty"],
             "day": day, "reverses": entry_index, "reason": reason,
             "officer": officer}
        self.entries.append(e)
        return dict(e)

    def balance(self, item):
        return self._balance(item)

    def consumption_rate(self, item, *, as_of, window=30):
        """Units/day from issue history. Honest when thin."""
        issued = [e for e in self.entries
                  if e["item"] == item and e["kind"] == "issue"
                  and as_of - window <= e["day"] <= as_of]
        days_seen = len({e["day"] for e in issued})
        if days_seen < MIN_RATE_HISTORY:
            return {"rate": None, "state": "learning",
                    "days_of_history": days_seen}
        return {"rate": sum(e["qty"] for e in issued) / float(window),
                "state": "ok", "days_of_history": days_seen}

    def report(self, items, *, as_of):
        rows = {}
        for item in items:
            bal = self._balance(item)
            rate = self.consumption_rate(item, as_of=as_of)
            days_of_stock = None
            reorder = None
            if rate["state"] == "ok":
                days_of_stock = (round(bal / rate["rate"], 1)
                                 if rate["rate"] > 0 else float("inf"))
                reorder = days_of_stock < REORDER_DAYS
            rows[item] = {
                "balance": bal,
                "days_of_stock": days_of_stock,
                "reorder_flag": reorder,
                "rate_state": rate["state"],
                "stockout_days_recorded": len(self.stockout_days.get(item, [])),
            }
        return {"facility": self.facility, "as_of": as_of, "items": rows,
                "note": "stockout_days_recorded counts days demand met an empty shelf"}
