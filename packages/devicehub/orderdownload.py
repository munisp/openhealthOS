"""orderdownload.py — ORM^O01: orders flow DOWN to the analyzer.

Result-only integration forces bench staff to re-key the order at the
analyzer — and re-keying is where wrong-patient and wrong-test errors
are born. An order download closes that loop. Clean-room HL7 v2,
same discipline as the ORU codec:

  - placer order number is mandatory — an order without an identity
    cannot be cancelled, only orphaned;
  - priority rides the order (STAT at the bench means STAT at the
    machine);
  - the outbound message is parseable by our own parser discipline:
    round-trip tested, not assumed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicehub import DeviceHubError, _now_ts, escape  # noqa: E402

ORDER_VERSION = "order-download@1.0.0"
PRIORITIES = ("R", "S")  # routine / stat — the bench's whole vocabulary


def build_orm(*, message_id, placer_order_number, patient_id,
              test_code, priority="R", sender="openhealth",
              ordering_provider="", requested_ts=""):
    """Serialize a work order for the analyzer lane."""
    if not placer_order_number:
        raise DeviceHubError("an order without a placer order number "
                             "cannot be cancelled, only orphaned — "
                             "refused")
    if not patient_id or not test_code:
        raise DeviceHubError("order needs patient and test — an order "
                             "for nobody, for nothing, is not an order")
    if priority not in PRIORITIES:
        raise DeviceHubError(f"unknown priority {priority!r} — the "
                             f"bench vocabulary is {PRIORITIES}")
    return "\r".join([
        f"MSH|^~\\&|{sender}||ANALYZER||{_now_ts()}||ORM^O01|"
        f"{message_id}|P|2.5",
        f"PID|1||{escape(patient_id)}",
        f"ORC|NW|{escape(placer_order_number)}",
        f"OBR|1|{escape(placer_order_number)}||{escape(test_code)}|"
        f"{priority}|||{requested_ts}|||||||||"
        f"{escape(ordering_provider)}",
    ]) + "\r"


def parse_orm(message):
    """Validate/inbound-parse an ORM^O01 (order entry or cancel).
    ORC|XO cancels; anything but NW/XO refuses — an unhandled order
    control code half-applied is a wrong test on a patient."""
    segments = [s for s in message.replace("\n", "\r").split("\r")
                if s.strip()]
    if not segments or not segments[0].startswith("MSH"):
        raise DeviceHubError("not HL7 — refuse, don't guess")
    msh = segments[0].split("|")
    if len(msh) < 9 or not msh[8].startswith("ORM"):
        raise DeviceHubError(f"not an ORM message: "
                             f"{msh[8] if len(msh) > 8 else '?'}")
    pid = next((s.split("|") for s in segments if s.startswith("PID")),
               None)
    orc = next((s.split("|") for s in segments if s.startswith("ORC")),
               None)
    if pid is None or orc is None:
        raise DeviceHubError("ORM without PID or ORC — an order about "
                             "nobody, controlled by nothing")
    control = orc[1] if len(orc) > 1 else ""
    if control not in ("NW", "XO"):
        raise DeviceHubError(f"unhandled order control {control!r} — "
                             "NW (new) and XO (change) are the handled "
                             "set; extend the codec, never half-apply")
    obr = next((s.split("|") for s in segments if s.startswith("OBR")),
               None)
    return {"message_id": msh[9] if len(msh) > 9 else "",
            "patient_id": pid[3].split("^")[0] if len(pid) > 3 else "",
            "control": control,
            "placer_order_number": orc[2] if len(orc) > 2 else "",
            "test_code": obr[4].split("^")[0]
            if obr is not None and len(obr) > 4 else "",
            "priority": obr[5] if obr is not None and len(obr) > 5
            else "R",
            "version": ORDER_VERSION}
