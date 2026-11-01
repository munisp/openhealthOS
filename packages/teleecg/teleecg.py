"""teleecg.py — tele-cardiology: ECG capture-to-interpretation.

The ECG machine sits at the PHC; the cardiologist doesn't. Device
provenance matters twice here: a waveform from an unverified device is
flagged, never laundered (devicereg doctrine), and a STEMI is a
critical finding that escalates until acknowledged.

Engine: teleread. This module owns waveform ingest + domain rules.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

LEAD_KINDS = ("12-lead", "3-lead", "single-lead")
CONFIG = DomainConfig(
    "ecg",
    scopes=("cardiac-reader",),
    critical_findings=("stemi", "complete-heart-block", "vt",
                       "severe-bradycardia"),
    artifact_kinds=LEAD_KINDS,
    sla_hours={"stat": 1, "urgent": 12, "routine": 48})


class ECGService:
    def __init__(self, readers, device_registry=None):
        self.reading = ReadingService(CONFIG, readers)
        self.devices = device_registry
        self.tracings = {}

    def record_tracing(self, tracing_id, *, patient_id, kind, device_id,
                       waveform_ref, facility, referrer, day):
        if kind not in LEAD_KINDS:
            raise TeleReadError(f"unknown ECG kind: {kind}")
        if not waveform_ref:
            raise TeleReadError("no waveform — nothing to read")
        flags = []
        if self.devices is not None:
            status = self.devices.reading_status(device_id, day)
            if status != "verified":
                flags.append(f"device-{status}")
        if tracing_id in self.tracings:
            raise TeleReadError(f"duplicate tracing: {tracing_id}")
        self.tracings[tracing_id] = {
            "tracing_id": tracing_id, "patient_id": patient_id,
            "kind": kind, "device_id": device_id,
            "waveform_ref": waveform_ref, "flags": flags, "day": day,
            "facility": facility}
        return dict(self.tracings[tracing_id])

    def submit_for_reading(self, tracing_id, *, case_id, priority,
                           question, referrer, day):
        t = self._tracing(tracing_id)
        return self.reading.order(
            case_id, priority=priority, question=question,
            facility=t["facility"],
            referrer=referrer, day=day, artifact=t["kind"],
            meta={"tracing_id": tracing_id, "kind": t["kind"],
                  "device_flags": t["flags"]})

    def _tracing(self, tid):
        if tid not in self.tracings:
            raise TeleReadError(f"unknown tracing: {tid}")
        return self.tracings[tid]
