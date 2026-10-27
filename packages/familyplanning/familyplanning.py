"""familyplanning.py — counselling, method provision, method mix.

Unmet need for contraception drives maternal mortality and high-parity
births; a platform that prevents child deaths but ignores birth spacing
is solving half the equation. Minimal module: counselling recorded,
method choice respected, stock linkage honest, resupply visits tracked
like every other schedule on the platform.

Doctrine:
- CHOICE IS THE CLIENT'S: a method recorded without counselling is
  refused — coercion by omission is still coercion.
- STOCK HONESTY: providing a method checks the stock ledger when one
  is attached; a stock-out during a FP visit is recorded as unmet
  demand, not a shrug.
- RESUPPLY IS A SCHEDULE: injectables and pills have return windows;
  overdue resupply = missed-contact work item, same doctrine as PNC.
"""

METHODS = {
    "implant": {"duration_years": 3, "resupply_days": None},
    "iud": {"duration_years": 10, "resupply_days": None},
    "injectable-depo": {"duration_years": None, "resupply_days": 84},
    "pills-coc": {"duration_years": None, "resupply_days": 28},
    "condom": {"duration_years": None, "resupply_days": 28},
    "sterilization": {"duration_years": None, "resupply_days": None},
    "natural-lam": {"duration_years": None, "resupply_days": 180},
}
RESUPPLY_GRACE_DAYS = 7


class FPError(Exception):
    pass


class FPRegister:
    def __init__(self, stock_ledger=None):
        self.clients = {}
        self.stock = stock_ledger
        self.unmet_demand = []  # stock-out refusals, counted loudly

    def counsel(self, client_id, *, day, counsellor, discussed,
                postpartum=False, note=""):
        if not discussed:
            raise FPError("counselling record must name discussed methods")
        self.clients.setdefault(client_id, {"counselling": [],
                                            "methods": []})
        rec = {"day": day, "counsellor": counsellor,
               "discussed": list(discussed), "postpartum": bool(postpartum),
               "note": note}
        self.clients[client_id]["counselling"].append(rec)
        return dict(rec)

    def provide(self, client_id, method, *, day, provider, cadre,
                stock_item=None):
        if method not in METHODS:
            raise FPError(f"unknown method: {method}")
        c = self.clients.get(client_id)
        if not c or not c["counselling"]:
            raise FPError(
                "no counselling on record — choice must precede provision")
        if cadre not in ("nurse", "doctor", "chw", "midwife"):
            raise FPError(f"unknown provider cadre: {cadre}")
        if method in ("implant", "iud", "sterilization") and cadre == "chw":
            raise FPError(f"{method} requires clinical cadre, not chw")
        if stock_item and self.stock is not None:
            try:
                self.stock.issue(stock_item, 1, day=day,
                                 to=f"fp:{client_id}")
            except Exception:
                self.unmet_demand.append(
                    {"client_id": client_id, "method": method,
                     "stock_item": stock_item, "day": day})
                raise FPError(
                    f"stock-out: {stock_item} — unmet demand recorded")
        resupply = METHODS[method]["resupply_days"]
        rec = {"method": method, "day": day, "provider": provider,
               "cadre": cadre,
               "resupply_due_day": day + resupply if resupply else None}
        c["methods"].append(rec)
        return dict(rec)

    def discontinue(self, client_id, *, day, reason):
        c = self._client(client_id)
        if not c["methods"]:
            raise FPError("no active method to discontinue")
        c["methods"][-1]["discontinued"] = {"day": day, "reason": reason}
        return dict(c["methods"][-1])

    def resupply_overdue(self, as_of):
        rows = []
        for cid, c in self.clients.items():
            if not c["methods"]:
                continue
            m = c["methods"][-1]
            if m.get("discontinued") or m["resupply_due_day"] is None:
                continue
            overdue = as_of - m["resupply_due_day"] - RESUPPLY_GRACE_DAYS
            if overdue > 0:
                rows.append({"client_id": cid, "method": m["method"],
                             "days_overdue": overdue})
        rows.sort(key=lambda r: -r["days_overdue"])
        return rows

    def method_mix(self):
        mix = {}
        for c in self.clients.values():
            for m in c["methods"]:
                if not m.get("discontinued"):
                    mix[m["method"]] = mix.get(m["method"], 0) + 1
        return {"active_methods": mix,
                "clients_counselled": len(self.clients),
                "unmet_demand_events": len(self.unmet_demand),
                "note": "unmet demand is stock failing the client — count it"}

    def _client(self, cid):
        if cid not in self.clients:
            raise FPError(f"unknown client: {cid}")
        return self.clients[cid]
