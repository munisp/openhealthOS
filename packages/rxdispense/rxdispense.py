"""rxdispense.py — the pharmacy dispensing workflow.

eprescribe gets the prescription to the pharmacy; stockledger knows
what is on the shelf. This package is the counter in between: partial
fills with a tracked balance, generic substitution that is STATED on
the label, compounding against ratified formulas, lot discipline with
FEFO, and a second pharmacist on high-alert items.

Doctrine:
- SUBSTITUTIONS ARE RATIFIED ENTRIES: brand -> generic equivalence is
  a four-eyes registry entry; an unlisted substitution refuses, and a
  ratified one still prints on the label ("dispensed as X for Y").
- PARTIAL FILLS CARRY A BALANCE: dispensing less than prescribed is a
  stated partial with remaining quantity owed — never silent shortfall.
- FEFO IS ENFORCED, LOTS ARE LAW: expired lots refuse; the picker
  suggests first-expiry-first-out; a dispense names its lot.
- HIGH-ALERT IS TWO PAIRS OF EYES: a high-alert prescription is
  dispensed by one pharmacist and verified by another.
- COMPOUNDING IS A BATCH RECORD: formula must be a ratified entry;
  the batch carries a checksum — the checksum is the contract.
"""

import hashlib
import json

RXDISPENSE_VERSION = "rxdispense@1.0.0"


class Error(Exception):
    pass


class SubstitutionRegistry:
    """Brand -> generic equivalence, ratified four-eyes."""

    def __init__(self):
        self.entries = {}      # brand_code -> entry
        self.audit = []

    def propose(self, brand_code, generic_code, *, by, day,
                note=None):
        if brand_code in self.entries:
            raise Error(f"substitution for {brand_code} exists; "
                        "register a revision, never edit in place")
        if brand_code == generic_code:
            raise Error("a substitution to itself is not a "
                        "substitution")
        self.entries[brand_code] = {
            "brand": brand_code, "generic": generic_code,
            "note": note, "status": "proposed", "proposed_by": by}
        self.audit.append({"op": "propose", "brand": brand_code,
                           "by": by, "day": day})

    def ratify(self, brand_code, *, by, day):
        e = self.entries.get(brand_code)
        if not e or e["status"] != "proposed":
            raise Error(f"no proposed substitution for {brand_code}")
        if by == e["proposed_by"]:
            raise Error("the hand that proposes never ratifies")
        e["status"] = "ratified"
        e["ratified_by"] = by
        self.audit.append({"op": "ratify", "brand": brand_code,
                           "by": by, "day": day})

    def equivalent(self, brand_code):
        e = self.entries.get(brand_code)
        if not e or e["status"] != "ratified":
            raise Error(f"no ratified substitution for {brand_code} "
                        "— an unlisted substitution refuses")
        return e["generic"]


class FormulaRegistry:
    """Compounding formulas, ratified four-eyes; every batch is
    checksummed against the ratified ingredient list."""

    def __init__(self):
        self.formulas = {}

    def propose(self, formula_id, ingredients, *, by, day):
        if formula_id in self.formulas:
            raise Error(f"formula {formula_id} exists; version up, "
                        "never edit in place")
        if not ingredients:
            raise Error("a formula with no ingredients compounds "
                        "nothing — refused")
        for ing in ingredients:
            if "product" not in ing or "quantity" not in ing:
                raise Error("every ingredient names product and "
                            "quantity")
        self.formulas[formula_id] = {
            "id": formula_id, "ingredients": list(ingredients),
            "status": "proposed", "proposed_by": by}

    def ratify(self, formula_id, *, by, day):
        f = self.formulas.get(formula_id)
        if not f or f["status"] != "proposed":
            raise Error(f"no proposed formula {formula_id}")
        if by == f["proposed_by"]:
            raise Error("the hand that proposes never ratifies")
        f["status"] = "ratified"
        f["ratified_by"] = by
        f["sha256"] = hashlib.sha256(json.dumps(
            f["ingredients"], sort_keys=True,
            ensure_ascii=False).encode()).hexdigest()
        return f

    def live(self, formula_id):
        f = self.formulas.get(formula_id)
        if not f or f["status"] != "ratified":
            raise Error(f"formula {formula_id} is not ratified — "
                        "compounding against an unratified recipe "
                        "refuses")
        return f


class LotShelf:
    """Stock by lot with expiry. FEFO: the shelf suggests the lot
    that expires first; expired lots refuse outright."""

    def __init__(self):
        self.lots = {}         # product -> [lot dicts]

    def receive(self, product, lot_no, *, quantity, expiry_day, day):
        if quantity < 1:
            raise Error("a received lot holds a positive quantity")
        lot = {"lot_no": lot_no, "quantity": quantity,
               "expiry_day": expiry_day, "received_day": day}
        self.lots.setdefault(product, []).append(lot)
        return lot

    def pick_fefo(self, product, quantity, *, today):
        """First-expiry-first-out suggestion. Refuses when stock is
        short or only expired lots remain — never picks expired."""
        candidates = [l for l in self.lots.get(product, [])
                      if l["expiry_day"] >= today and l["quantity"] > 0]
        if not candidates:
            raise Error(f"no unexpired stock of {product} — stockout "
                        "is stated, not silently backordered")
        candidates.sort(key=lambda l: l["expiry_day"])
        available = sum(l["quantity"] for l in candidates)
        if available < quantity:
            raise Error(f"only {available} units of {product} "
                        f"unexpired against {quantity} requested — "
                        "a partial fill is the honest answer, not a "
                        "quiet shortfall")
        return candidates[0]

    def draw(self, product, lot_no, quantity, *, today):
        for lot in self.lots.get(product, []):
            if lot["lot_no"] == lot_no:
                if lot["expiry_day"] < today:
                    raise Error(f"lot {lot_no} of {product} expired "
                                f"on day {lot['expiry_day']} — "
                                "expired stock is never dispensed")
                if lot["quantity"] < quantity:
                    raise Error(f"lot {lot_no} holds "
                                f"{lot['quantity']} against "
                                f"{quantity} requested")
                lot["quantity"] -= quantity
                return lot
        raise Error(f"unknown lot {lot_no} for {product}")


class DispensingDesk:
    def __init__(self, *, substitutions=None, formulas=None,
                 shelf=None):
        self.substitutions = substitutions or SubstitutionRegistry()
        self.formulas = formulas or FormulaRegistry()
        self.shelf = shelf or LotShelf()
        self.prescriptions = {}   # rx_ref -> record
        self.fills = []           # dispense events
        self.batches = []         # compounding batch records

    def register_prescription(self, rx_ref, *, product, quantity,
                              high_alert=False, prescriber, day):
        if rx_ref in self.prescriptions:
            raise Error(f"prescription {rx_ref} already registered — "
                        "a refill is a new event, not an overwrite")
        if quantity < 1:
            raise Error("a prescription orders a positive quantity")
        rx = {"ref": rx_ref, "product": product,
              "quantity_prescribed": quantity,
              "quantity_outstanding": quantity,
              "high_alert": high_alert, "prescriber": prescriber,
              "registered_day": day, "state": "open"}
        self.prescriptions[rx_ref] = rx
        return rx

    def dispense(self, rx_ref, *, quantity, product=None, lot_no=None,
                 by, day, verified_by=None):
        """Dispense against a prescription. Substitution must be
        ratified and is stated on the label; partial fills track the
        outstanding balance; high-alert needs a second pharmacist."""
        rx = self.prescriptions.get(rx_ref)
        if not rx or rx["state"] != "open":
            raise Error(f"no open prescription {rx_ref}")
        if quantity < 1 or quantity > rx["quantity_outstanding"]:
            raise Error(f"quantity {quantity} against outstanding "
                        f"{rx['quantity_outstanding']} — overfill "
                        "refuses, zero-fill refuses")
        product = product or rx["product"]
        label_note = None
        if product != rx["product"]:
            generic = self.substitutions.equivalent(rx["product"])
            if product != generic:
                raise Error(f"{product} is not the ratified generic "
                            f"for {rx['product']} — refused")
            label_note = (f"dispensed as {product} (generic) for "
                          f"{rx['product']} (brand) — substitution "
                          "stated, not hidden")
        if rx["high_alert"] and (verified_by is None
                                 or verified_by == by):
            raise Error("high-alert dispensing is two pairs of eyes "
                        "— a second named pharmacist verifies")
        lot = self.shelf.pick_fefo(product, quantity, today=day) \
            if lot_no is None else None
        lot_no = lot_no or lot["lot_no"]
        self.shelf.draw(product, lot_no, quantity, today=day)
        partial = quantity < rx["quantity_outstanding"]
        rx["quantity_outstanding"] -= quantity
        if rx["quantity_outstanding"] == 0:
            rx["state"] = "filled"
        fill = {"rx_ref": rx_ref, "product": product,
                "quantity": quantity, "lot_no": lot_no,
                "dispensed_by": by, "verified_by": verified_by,
                "day": day, "partial": partial,
                "balance_owed": rx["quantity_outstanding"],
                "label_note": label_note}
        self.fills.append(fill)
        return fill

    def compound(self, rx_ref, *, formula_id, by, day,
                 verified_by=None):
        """Extemporaneous compounding against a ratified formula —
        the batch record is checksummed; the checksum is the contract."""
        rx = self.prescriptions.get(rx_ref)
        if not rx or rx["state"] != "open":
            raise Error(f"no open prescription {rx_ref}")
        formula = self.formulas.live(formula_id)
        if verified_by is None or verified_by == by:
            raise Error("a compounded batch is verified by a second "
                        "pharmacist — the hand that mixes never "
                        "checks alone")
        for ing in formula["ingredients"]:
            self.shelf.draw(ing["product"],
                            self._lot_for(ing["product"], day),
                            ing["quantity"], today=day)
        batch = {"rx_ref": rx_ref, "formula": formula_id,
                 "ingredients": formula["ingredients"],
                 "mixed_by": by, "verified_by": verified_by,
                 "day": day, "formula_sha256": formula["sha256"]}
        batch["sha256"] = hashlib.sha256(json.dumps(
            {k: v for k, v in batch.items() if k != "sha256"},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rx["state"] = "filled"
        rx["quantity_outstanding"] = 0
        self.batches.append(batch)
        return batch

    def _lot_for(self, product, day):
        return self.shelf.pick_fefo(product, 1, today=day)["lot_no"]

    def owed_balances(self):
        """Every open prescription with an outstanding balance —
        shortfalls stated, never forgotten."""
        return [{"rx_ref": r["ref"], "owed": r["quantity_outstanding"]}
                for r in self.prescriptions.values()
                if r["state"] == "open"
                and r["quantity_outstanding"] < r["quantity_prescribed"]]
