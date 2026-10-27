"""medevac.py — evacuation case management: ambulance to air-evac.

The referrals module closes the loop between facilities; this module
handles the harder case — evacuation to another facility's COUNTRY,
with an aircraft, a medical escort, and a receiving hospital that must
accept before wheels-up. The doctrine is the same, the stakes are
higher: a patient in the air with no accepting facility is a crisis.

Lifecycle: requested -> accepted -> cleared -> in-transit -> handed-over.
   (refused stays open for re-routing, like referrals)

Doctrine:
- NO WHEELS-UP WITHOUT ACCEPTANCE: dispatch refuses until a named
  receiving facility/clinician has confirmed a bed. Verbal "probably"
  is not acceptance — the confirmation has an id and a timestamp.
- CROSS-BORDER IS GATED: an international evacuation cannot be
  dispatched without a cross-border release id (consent-gated patient
  summary, see xrelease) AND the IPS bundle hash it covers. The data
  travels with the patient or the patient doesn't travel.
- FIT-TO-FLY IS A CLINICAL DECISION ON RECORD: clearance requires a
  named clinician, and altitude/oxygen constraints are recorded, not
  implied.
- EN-ROUTE LOG IS APPEND-ONLY: vitals, interventions, deterioration
  events with timestamps. The handover bundle includes ALL of it —
  the receiving team gets the flight, not a summary of the flight.
"""

ACCEPT_WINDOW_HOURS = 6        # emergency acceptance window
STATES = ("requested", "accepted", "cleared", "in-transit",
          "handed-over", "refused")


class MedevacError(Exception):
    pass


class EvacuationRegister:
    def __init__(self):
        self.cases = {}
        self._n = 0

    def request(self, *, patient_id, from_facility, reason, urgency,
                requested_day, destination_country=None,
                to_facility=None, flight_required=False):
        if urgency not in ("emergency", "urgent", "routine"):
            raise MedevacError(f"unknown urgency: {urgency}")
        if not reason:
            raise MedevacError("clinical reason required")
        self._n += 1
        cid = f"EVAC-{self._n}"
        self.cases[cid] = {
            "id": cid, "patient_id": patient_id,
            "from_facility": from_facility, "reason": reason,
            "urgency": urgency, "requested_day": requested_day,
            "destination_country": destination_country,
            "to_facility": to_facility, "flight_required": flight_required,
            "state": "requested", "history": [("requested", requested_day)],
            "transport_log": [],
        }
        return dict(self.cases[cid])

    def accept(self, cid, *, day, receiving_facility, receiving_clinician,
               bed_confirmation_id):
        c = self._case(cid)
        self._require(c, "requested")
        if not bed_confirmation_id:
            raise MedevacError(
                "acceptance requires a bed confirmation id — "
                "verbal 'probably' is not acceptance")
        c["receiving_facility"] = receiving_facility
        c["receiving_clinician"] = receiving_clinician
        c["bed_confirmation_id"] = bed_confirmation_id
        self._set(c, "accepted", day)
        return dict(c)

    def refuse(self, cid, *, day, reason):
        c = self._case(cid)
        self._require(c, "requested")
        c["refusal_reason"] = reason
        self._set(c, "refused", day)
        return dict(c)

    def clear_fit_to_fly(self, cid, *, day, clinician, constraints=()):
        """Medical clearance. Constraints (altitude restriction, oxygen
        flow, stretcher) are recorded — the flight crew needs them."""
        c = self._case(cid)
        self._require(c, "accepted")
        c["clearance"] = {"clinician": clinician, "day": day,
                          "constraints": list(constraints)}
        self._set(c, "cleared", day)
        return dict(c)

    def dispatch(self, cid, *, day, transport_kind, escort,
                 release_id=None, ips_bundle_hash=None):
        """Wheels-up. Cross-border is gated on the release + IPS hash."""
        c = self._case(cid)
        self._require(c, "cleared" if c["flight_required"] else "accepted")
        if transport_kind not in ("ground-ambulance", "rotary", "fixed-wing"):
            raise MedevacError(f"unknown transport: {transport_kind}")
        if c["flight_required"] and transport_kind == "ground-ambulance":
            raise MedevacError("flight required for this case")
        if c["destination_country"]:
            if not release_id:
                raise MedevacError(
                    "cross-border dispatch requires a cross-border "
                    "release id — consent-gated summary travels with "
                    "the patient or the patient doesn't travel")
            if not ips_bundle_hash:
                raise MedevacError(
                    "cross-border dispatch requires the IPS bundle hash "
                    "the release covers")
        c["transport"] = {"kind": transport_kind, "escort": escort,
                          "release_id": release_id,
                          "ips_bundle_hash": ips_bundle_hash}
        self._set(c, "in-transit", day)
        return dict(c)

    def log_en_route(self, cid, *, ts, entry, vitals=None,
                     intervention=None):
        """Append-only flight/transport log."""
        c = self._case(cid)
        self._require(c, "in-transit")
        if not entry:
            raise MedevacError("empty log entry refused")
        rec = {"ts": ts, "entry": entry, "vitals": vitals,
               "intervention": intervention}
        c["transport_log"].append(rec)
        return dict(rec)

    def handover(self, cid, *, day, receiving_clinician,
                 handover_document_ref):
        """Loop closes only when the receiving team confirms AND takes
        the full record (transport log + IPS)."""
        c = self._case(cid)
        self._require(c, "in-transit")
        if not handover_document_ref:
            raise MedevacError(
                "handover requires the document reference — the "
                "receiving team gets the flight, not a summary of it")
        c["handover"] = {"day": day,
                         "receiving_clinician": receiving_clinician,
                         "document_ref": handover_document_ref,
                         "transport_log_entries": len(c["transport_log"])}
        self._set(c, "handed-over", day)
        return dict(c)

    def awaiting_acceptance(self, as_of):
        return [{"id": c["id"], "patient_id": c["patient_id"],
                 "urgency": c["urgency"], "days_waiting": as_of - c["requested_day"]}
                for c in self.cases.values()
                if c["state"] in ("requested", "refused")]

    def stats(self, as_of):
        by_state = {}
        for c in self.cases.values():
            by_state[c["state"]] = by_state.get(c["state"], 0) + 1
        return {"as_of": as_of, "total": len(self.cases),
                "by_state": by_state,
                "awaiting_acceptance": len(self.awaiting_acceptance(as_of)),
                "cross_border": sum(1 for c in self.cases.values()
                                    if c["destination_country"])}

    def _case(self, cid):
        if cid not in self.cases:
            raise MedevacError(f"unknown case: {cid}")
        return self.cases[cid]

    @staticmethod
    def _require(c, state):
        if c["state"] != state:
            raise MedevacError(f"{c['id']} is {c['state']}, expected {state}")

    @staticmethod
    def _set(c, state, day):
        c["state"] = state
        c["history"].append((state, day))
