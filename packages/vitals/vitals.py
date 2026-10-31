"""vitals.py — FHIR Observation ingest for device readings.

The pipe from cuff/oximeter/thermometer to the platform. Every reading
becomes a FHIR R4 Observation with LOINC coding, stamped with the
device's verification status from devicereg. Implausible values are
refused — a heart rate of 400 is a device fault or a typo, and either
way it must never reach a trigger rule.

Doctrine:
- PLAUSIBILITY IS PER-VITAL, FAIL-CLOSED: out-of-range readings raise,
  naming the range. Ranges here are wide physiological survival
  bounds, not clinical thresholds (thresholds live in triggers).
- PROVENANCE ON EVERY READING: device verification state
  (verified / calibration-stale) rides on the Observation; trigger
  rules can weight stale-calibration readings differently.
- MANUAL ENTRY IS NOT A DEVICE: readings entered by hand are marked
  performer-type human, never laundered as device data.
- LOINC CODES: canonical public codes per vital.
"""

# vital -> (loinc, unit, plausible_low, plausible_high)
VITALS = {
    "systolic-bp": ("8480-6", "mmHg", 40, 300),
    "diastolic-bp": ("8462-4", "mmHg", 20, 200),
    "heart-rate": ("8867-4", "/min", 20, 250),
    "spo2": ("59408-5", "%", 40, 100),
    "temperature": ("8310-5", "C", 25.0, 44.0),
    "respiratory-rate": ("9279-1", "/min", 2, 80),
    "blood-glucose": ("2339-0", "mg/dL", 10, 1000),
    "weight": ("29463-7", "kg", 0.5, 400),
}


class VitalsError(Exception):
    pass


class VitalsIngest:
    def __init__(self, device_registry=None):
        self.devices = device_registry
        self.observations = []   # append-only
        self.by_patient = {}

    def record(self, patient_id, vital, value, *, day, ts,
               device_id=None, performer="human"):
        """One reading in. Returns the FHIR Observation."""
        if vital not in VITALS:
            raise VitalsError(f"unknown vital: {vital}")
        loinc, unit, lo, hi = VITALS[vital]
        if not isinstance(value, (int, float)):
            raise VitalsError(f"{vital} value must be numeric")
        if not (lo <= value <= hi):
            raise VitalsError(
                f"implausible {vital}: {value}{unit} "
                f"(plausible {lo}-{hi}) — refused, not clamped")
        device_status = None
        if device_id is not None:
            if self.devices is None:
                raise VitalsError(
                    "device readings require a device registry")
            device_status = self.devices.reading_status(device_id, day)
            self.devices.note_reading(device_id, day)
        elif performer == "device":
            raise VitalsError(
                "device performer without device_id is a lie — refused")
        obs = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org",
                                 "code": loinc, "display": vital}]},
            "subject": {"reference": f"Patient/{patient_id}"},
            "effectiveDateTime": ts,
            "day": day,
            "valueQuantity": {"value": value, "unit": unit},
            "performer_type": performer if device_id is None else "device",
        }
        if device_status:
            obs["device"] = device_status
            if not device_status["verified"]:
                obs["flag"] = device_status["flag"]
        self.observations.append(obs)
        self.by_patient.setdefault(patient_id, []).append(obs)
        return dict(obs)

    def series(self, patient_id, vital, *, since_day=None):
        """Chronological series for trend rules."""
        obs = [o for o in self.by_patient.get(patient_id, [])
               if o["code"]["coding"][0]["display"] == vital]
        if since_day is not None:
            obs = [o for o in obs if o["day"] >= since_day]
        return sorted(obs, key=lambda o: (o["day"], o["effectiveDateTime"]))

    def latest(self, patient_id, vital):
        s = self.series(patient_id, vital)
        return dict(s[-1]) if s else None
