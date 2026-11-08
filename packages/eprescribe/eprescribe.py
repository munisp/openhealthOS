"""eprescribe.py — national electronic prescription rail.

Portugal's PEM and Cabo Verde's Receitas Eletrónicas exist because a paper
prescription cannot be verified, cannot be cancelled, and tells the
prescriber nothing about whether the medicine was ever collected. This
package is the rail: the prescriber issues a signed, tokenised
prescription; any registered pharmacy redeems against the token; every
dispensation flows back to the prescriber.

Doctrine:
- THE TOKEN IS THE PRESCRIPTION: redemption is by token; a token that
  does not resolve refuses without confirming existence to the caller.
- PARTIAL DISPENSE IS RECORDED, NOT LOST: remaining quantities are
  stated on every event; an over-dispense refuses with the remainder.
- CANCELLATION IS FINAL AND REASONED: a cancelled prescription refuses
  every later redemption, naming its state — never silently failing.
- THE PRESCRIBER SEES THE OUTCOME: dispensation history is the feedback
  loop; a prescription nobody collected is a clinical fact, not noise.
- PAYERS AND PHARMACIES ARE ENTRIES: NHIA, INPS-CV or a private scheme
  are registry rows; adding a payer is data, never a branch.
"""

import hashlib
import json

VERSION = "eprescribe@1.0.0"

RX_STATES = ("issued", "partially-dispensed", "dispensed",
             "cancelled", "expired")

TERMINAL_STATES = ("dispensed", "cancelled", "expired")

MAX_VALID_DAYS = 180
MAX_QUANTITY = 1000


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class EPrescription:
    """One issued prescription. Items are immutable after issue — a
    changed item is a new prescription, not an edit."""

    def __init__(self, rx_id, patient_ref, prescriber, facility, day,
                 items, payer="private", valid_days=30):
        if not prescriber:
            raise Error("a prescription names its prescriber")
        if not items:
            raise Error("a prescription carries at least one item")
        if not (1 <= valid_days <= MAX_VALID_DAYS):
            raise Error(f"validity is bounded (1–{MAX_VALID_DAYS} days); "
                        "beyond that is a review, not a longer paper")
        self.rx_id = rx_id
        self.patient_ref = patient_ref
        self.prescriber = prescriber
        self.facility = facility
        self.day = day
        self.payer = payer
        self.valid_days = valid_days
        self.items = []
        for i, item in enumerate(items):
            drug = item.get("drug_code")
            qty = item.get("quantity")
            if not drug:
                raise Error(f"item {i} has no drug code")
            if not (isinstance(qty, int) and 1 <= qty <= MAX_QUANTITY):
                raise Error(f"item {i} quantity must be 1–{MAX_QUANTITY}")
            self.items.append({
                "drug_code": drug,
                "dose_text": item.get("dose_text", ""),
                "quantity": qty,
                "dispensed": 0,
            })
        self.state = "issued"
        self.events = [{"event": "issued", "day": day, "by": prescriber}]
        self.token = "RX-" + _sha({
            "rx_id": rx_id, "patient_ref": patient_ref, "day": day,
            "items": [(i["drug_code"], i["quantity"]) for i in self.items],
            "version": VERSION,
        })[:12].upper()

    def remaining(self, item_index):
        item = self.items[item_index]
        return item["quantity"] - item["dispensed"]

    def as_dict(self):
        return {
            "rx_id": self.rx_id, "patient_ref": self.patient_ref,
            "prescriber": self.prescriber, "facility": self.facility,
            "day": self.day, "payer": self.payer,
            "valid_days": self.valid_days, "state": self.state,
            "token": self.token, "items": self.items,
            "events": self.events, "version": VERSION,
        }

    def fingerprint(self):
        return _sha(self.as_dict())


class PrescriptionRail:
    """The national rail: pharmacies, payers, and the prescription
    lifecycle. A rail with an unregistered pharmacy is a rail that
    refuses — dispensing outside the network is not dispensation."""

    def __init__(self):
        self.pharmacies = {}
        self.payers = {"private": {"name": "Private / out-of-pocket",
                                   "active": True}}
        self.prescriptions = {}

    # --- registries -------------------------------------------------
    def register_pharmacy(self, pharmacy_id, name, *, day):
        if pharmacy_id in self.pharmacies:
            raise Error(f"pharmacy {pharmacy_id} already registered")
        if not name:
            raise Error("a pharmacy is registered by name")
        self.pharmacies[pharmacy_id] = {
            "name": name, "active": True, "registered_day": day}

    def deactivate_pharmacy(self, pharmacy_id, *, day, reason):
        ph = self._pharmacy(pharmacy_id)
        if not reason:
            raise Error("deactivation carries a stated reason")
        ph["active"] = False
        ph["deactivated"] = {"day": day, "reason": reason}

    def register_payer(self, code, name):
        if code in self.payers:
            raise Error(f"payer {code} already registered")
        self.payers[code] = {"name": name, "active": True}

    def _pharmacy(self, pharmacy_id):
        ph = self.pharmacies.get(pharmacy_id)
        if ph is None or not ph["active"]:
            raise Error("pharmacy not on the rail — dispensing outside "
                        "the network is not dispensation")
        return ph

    # --- lifecycle --------------------------------------------------
    def issue(self, rx_id, patient_ref, prescriber, facility, day,
              items, payer="private", valid_days=30):
        if rx_id in self.prescriptions:
            raise Error(f"prescription {rx_id} already exists")
        if payer not in self.payers or not self.payers[payer]["active"]:
            raise Error(f"unknown payer {payer!r}; payers are entries")
        rx = EPrescription(rx_id, patient_ref, prescriber, facility, day,
                           items, payer=payer, valid_days=valid_days)
        self.prescriptions[rx_id] = rx
        return rx

    def resolve_token(self, token):
        for rx in self.prescriptions.values():
            if rx.token == token:
                return rx
        raise Error("unknown token")  # never confirm non-existence richly

    def dispense(self, rx_id, pharmacy_id, item_index, quantity, *,
                 by, day):
        rx = self._open_rx(rx_id, day)
        self._pharmacy(pharmacy_id)
        if not by:
            raise Error("a dispensation names the dispensing pharmacist")
        if item_index >= len(rx.items):
            raise Error(f"no item {item_index} on this prescription")
        remaining = rx.remaining(item_index)
        if quantity < 1:
            raise Error("quantity must be positive")
        if quantity > remaining:
            raise Error(f"over-dispense refused: {remaining} unit(s) "
                        f"remain on this item")
        rx.items[item_index]["dispensed"] += quantity
        partial = any(rx.remaining(i) > 0 for i in range(len(rx.items)))
        rx.state = "partially-dispensed" if partial else "dispensed"
        rx.events.append({
            "event": "dispensed", "day": day, "by": by,
            "pharmacy": pharmacy_id, "item_index": item_index,
            "quantity": quantity,
            "remaining": rx.remaining(item_index),
        })
        return rx

    def cancel(self, rx_id, *, by, day, reason):
        rx = self._get(rx_id)
        if rx.state in ("dispensed", "expired"):
            raise Error(f"cannot cancel a {rx.state} prescription")
        if rx.state == "cancelled":
            raise Error("already cancelled — a cancellation is final")
        if by != rx.prescriber:
            raise Error("only the prescriber cancels their prescription")
        if not reason:
            raise Error("cancellation carries a stated reason")
        rx.state = "cancelled"
        rx.events.append({"event": "cancelled", "day": day, "by": by,
                          "reason": reason})

    def expire_sweep(self, day):
        """Past validity is expiry, stated — a stale token refuses, it
        does not quietly keep working."""
        expired = []
        for rx in self.prescriptions.values():
            if (rx.state in ("issued", "partially-dispensed")
                    and day > rx.day + rx.valid_days):
                rx.state = "expired"
                rx.events.append({"event": "expired", "day": day})
                expired.append(rx.rx_id)
        return sorted(expired)

    def request_renewal(self, rx_id, *, by, day, note):
        rx = self._get(rx_id)
        if rx.state not in TERMINAL_STATES:
            raise Error("renewal is for finished prescriptions; an open "
                        "one is still redeemable")
        if not note:
            raise Error("a renewal request states why")
        rx.events.append({"event": "renewal-requested", "day": day,
                          "by": by, "note": note})

    def decide_renewal(self, rx_id, *, by, day, approved, reason=""):
        rx = self._get(rx_id)
        if by != rx.prescriber:
            raise Error("only the prescriber decides renewal")
        if rx.events[-1]["event"] != "renewal-requested":
            raise Error("no renewal request to decide")
        if not approved and not reason:
            raise Error("a refused renewal states why")
        rx.events.append({"event": "renewal-approved" if approved
                          else "renewal-refused", "day": day, "by": by,
                          "reason": reason})
        return approved

    # --- feedback + audit -------------------------------------------
    def dispensation_history(self, rx_id):
        rx = self._get(rx_id)
        return [e for e in rx.events if e["event"] == "dispensed"]

    def never_collected(self, day):
        """Prescriptions issued, never redeemed, still open or lapsed —
        adherence signal for the prescriber, stated per patient."""
        out = []
        for rx in self.prescriptions.values():
            if not self.dispensation_history(rx.rx_id):
                out.append({"rx_id": rx.rx_id,
                            "patient_ref": rx.patient_ref,
                            "state": rx.state,
                            "issued_day": rx.day,
                            "days_open": day - rx.day})
        return sorted(out, key=lambda r: r["rx_id"])

    def audit(self, rx_id):
        rx = self._get(rx_id)
        return {"rx_id": rx.rx_id, "state": rx.state,
                "events": len(rx.events),
                "sha256": rx.fingerprint(), "version": VERSION}

    def _get(self, rx_id):
        rx = self.prescriptions.get(rx_id)
        if rx is None:
            raise Error(f"unknown prescription {rx_id}")
        return rx

    def _open_rx(self, rx_id, day):
        rx = self._get(rx_id)
        if rx.state == "cancelled":
            raise Error("prescription is cancelled — a cancellation "
                        "is final")
        if rx.state == "dispensed":
            raise Error("prescription fully dispensed")
        if rx.state == "expired":
            raise Error("prescription expired — issue a new one after "
                        "review")
        if day > rx.day + rx.valid_days:
            raise Error("prescription past validity — run expire_sweep "
                        "or treat as expired")
        return rx
