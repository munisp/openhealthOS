"""devicereg.py — registry of medical devices allowed to feed vitals.

A BP cuff of unknown provenance is not a sensor, it's a rumour with
batteries. Before any reading enters the platform, the device it came
from must be registered, attested, and in calibration — same doctrine
as the capacity board: attestations expire, and stale calibration
degrades the device to unverified with the number visible.

Doctrine:
- REGISTRATION IS ATTESTATION: device type, serial, owner facility,
  and the attesting officer are required. No anonymous sensors.
- CALIBRATION EXPIRES: readings from a device past its calibration
  date are flagged unverified — never blocked (a stale cuff still
  sees the hypertensive crisis) but never trusted silently.
- RETIRED DEVICES GO QUIET LOUDLY: a retired device keeps its history
  (past readings stand, marked as from a retired device) but new
  readings refuse.
- OFFLINE DEVICES ARE A FINDING: a device expected to report daily
  that goes silent for SILENCE_DAYS days is surfaced — hub silence
  is not patient silence, and device silence is not patient health.
"""

CALIBRATION_VALID_DAYS = 365
SILENCE_DAYS = 7

DEVICE_TYPES = ("bp-cuff", "pulse-oximeter", "thermometer", "glucometer",
                "scale", "muac-tape", "fetoscope", "hemoglobin-meter")


class DeviceError(Exception):
    pass


class DeviceRegistry:
    def __init__(self):
        self.devices = {}
        self.last_seen = {}   # device_id -> last reading day

    def register(self, device_id, *, device_type, serial, facility,
                 attested_by, day, calibration_day):
        if device_id in self.devices:
            raise DeviceError(f"duplicate device: {device_id}")
        if device_type not in DEVICE_TYPES:
            raise DeviceError(f"unknown device type: {device_type}")
        if not serial:
            raise DeviceError("serial required — no anonymous sensors")
        if calibration_day > day:
            raise DeviceError("calibration in the future?")
        self.devices[device_id] = {
            "type": device_type, "serial": serial, "facility": facility,
            "attested_by": attested_by, "registered_day": day,
            "calibration_day": calibration_day, "retired": False,
        }
        return dict(self.devices[device_id])

    def recalibrate(self, device_id, *, day, technician, certificate=None):
        d = self._dev(device_id)
        if d["retired"]:
            raise DeviceError("retired devices cannot be recalibrated")
        d["calibration_day"] = day
        d["calibration_certificate"] = certificate
        return dict(d)

    def retire(self, device_id, *, day, reason, officer):
        if not reason:
            raise DeviceError("retirement requires a reason")
        d = self._dev(device_id)
        d["retired"] = True
        d["retired_day"] = day
        d["retired_reason"] = reason
        d["retired_by"] = officer
        return dict(d)

    def reading_status(self, device_id, day):
        """What a reading from this device, on this day, means.
        Returns a status the vitals ingest stamps on the Observation."""
        d = self._dev(device_id)
        if d["retired"]:
            raise DeviceError(
                f"device {device_id} retired on day "
                f"{d['retired_day']} ({d['retired_reason']}) — "
                "new readings refused")
        cal_age = day - d["calibration_day"]
        verified = cal_age <= CALIBRATION_VALID_DAYS
        return {"device_id": device_id, "type": d["type"],
                "verified": verified,
                "calibration_age_days": cal_age,
                "flag": None if verified else "calibration-stale"}

    def note_reading(self, device_id, day):
        self.last_seen[device_id] = day

    def silent_devices(self, as_of, expected_daily=True):
        out = []
        for did, d in self.devices.items():
            if d["retired"]:
                continue
            last = self.last_seen.get(did)
            silent_for = (as_of - last) if last is not None else (
                as_of - d["registered_day"])
            if silent_for > SILENCE_DAYS:
                out.append({"device_id": did, "facility": d["facility"],
                            "type": d["type"], "days_silent": silent_for,
                            "never_reported": last is None})
        out.sort(key=lambda r: -r["days_silent"])
        return out

    def report(self, as_of):
        verified = stale = retired = 0
        for did, d in self.devices.items():
            if d["retired"]:
                retired += 1
            elif as_of - d["calibration_day"] <= CALIBRATION_VALID_DAYS:
                verified += 1
            else:
                stale += 1
        return {"as_of": as_of, "total": len(self.devices),
                "verified": verified, "calibration_stale": stale,
                "retired": retired,
                "silent": len(self.silent_devices(as_of)),
                "note": "stale calibration degrades readings to "
                        "unverified — flagged, never blocked"}

    def _dev(self, did):
        if did not in self.devices:
            raise DeviceError(f"unknown device: {did}")
        return self.devices[did]
