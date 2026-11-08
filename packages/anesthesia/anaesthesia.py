"""anaesthesia.py — the intra-operative anaesthesia record.

Theatre tracks the case; the WHO checklist guards the pauses; but the
anaesthetist's own record — induction, agents, vitals at intervals,
airway, emergence — is its own legal and clinical document. An operation
without an anaesthesia record is an operation nobody can defend.

Doctrine:
- PREOP GATE FAIL-CLOSED: no record starts without an ASA grade, an
  airway assessment (Mallampati), and confirmed fasting status for
  elective cases. "Assumed fasted" is not a fasting status.
- THE GRID IS TIMESTAMPED: every observation and agent carries its
  minute relative to induction. The record is a timeline, not a form
  filled in afterwards.
- PLAUSIBILITY REFUSES, NOT CLAMPS: an SpO2 of 140 is a transcription
  error; it refuses with the bound named.
- HIGH-ALERT AGENTS NAME A SECOND CHECKER: induction doses of agents on
  the high-alert registry carry a witness.
- EMERGENCE CLOSES THE LOOP: the record cannot close without a recovery
  disposition (PACU / ward / ICU) and a named handover recipient.
"""

import hashlib
import json

VERSION = "anaesthesia@1.0.0"

ASA_GRADES = ("I", "II", "III", "IV", "V", "VI")
MALLAMPATI = ("I", "II", "III", "IV")
ANAESTHESIA_TYPES = ("general", "spinal", "epidural", "regional-block",
                     "local", "sedation")
DISPOSITIONS = ("pacu", "ward", "icu")

# agent -> (class, high_alert)
AGENTS = {
    "propofol": ("induction", True),
    "thiopentone": ("induction", True),
    "ketamine": ("induction", True),
    "suxamethonium": ("muscle-relaxant", True),
    "atracurium": ("muscle-relaxant", False),
    "fentanyl": ("opioid", True),
    "morphine": ("opioid", True),
    "halothane": ("volatile", False),
    "isoflurane": ("volatile", False),
    "sevoflurane": ("volatile", False),
    "bupivacaine": ("local-anaesthetic", False),
    "lidocaine": ("local-anaesthetic", False),
    "ondansetron": ("antiemetic", False),
    "ephedrine": ("vasopressor", True),
    "adrenaline": ("vasopressor", True),
    "oxytocin": ("uterotonic", True),
    "midazolam": ("sedative", True),
    "neostigmine": ("reversal", False),
    "paracetamol-iv": ("analgesic", False),
}

# observation -> (plausible_low, plausible_high) — survival bounds
OBSERVATIONS = {
    "heart-rate": (20, 250),
    "systolic-bp": (40, 300),
    "diastolic-bp": (20, 200),
    "spo2": (40, 100),
    "etco2": (5, 120),
    "temperature": (25.0, 44.0),
    "airway-pressure": (0, 80),
}


class Error(Exception):
    pass


def register_agent(name, agent_class, high_alert=False):
    """Agents are registry entries, not branches."""
    if not name or not agent_class:
        raise Error("an agent entry names itself and its class")
    AGENTS[name] = (agent_class, high_alert)


class AnaesthesiaRecord:
    """One patient's anaesthesia for one theatre case."""

    def __init__(self, record_id, case_ref, patient_ref, *, anaesthetist,
                 day):
        for name, val in (("record", record_id), ("case", case_ref),
                          ("patient", patient_ref),
                          ("anaesthetist", anaesthetist)):
            if not val:
                raise Error(f"an anaesthesia record names its {name}")
        self.record_id = record_id
        self.case_ref = case_ref
        self.patient_ref = patient_ref
        self.anaesthetist = anaesthetist
        self.day = day
        self.preop = None
        self.timeline = []   # append-only events
        self.closed = None
        self._started = False

    # ---- preoperative gate ----
    def assess_preop(self, *, asa, mallampati, fasting_hours,
                     elective, airway_plan, by):
        if asa not in ASA_GRADES:
            raise Error(f"unknown ASA grade: {asa}")
        if mallampati not in MALLAMPATI:
            raise Error(f"unknown Mallampati grade: {mallampati}")
        if by != self.anaesthetist:
            raise Error("the anaesthetist signs her own preop assessment")
        if elective and fasting_hours < 6:
            raise Error(
                f"elective case fasted {fasting_hours}h (<6h) — "
                "postpone or reclassify; 'assumed fasted' is not a status")
        if not (airway_plan and airway_plan.strip()):
            raise Error("the airway plan is stated, not assumed")
        self.preop = {"asa": asa, "mallampati": mallampati,
                      "fasting_hours": fasting_hours, "elective": elective,
                      "airway_plan": airway_plan, "by": by}
        return self.preop

    # ---- intraoperative ----
    def induce(self, *, minute, anaesthesia_type):
        if self.preop is None:
            raise Error("no induction without a signed preop assessment")
        if self._started:
            raise Error("already induced — the timeline never restarts")
        if anaesthesia_type not in ANAESTHESIA_TYPES:
            raise Error(f"unknown anaesthesia type: {anaesthesia_type}")
        self._started = True
        self.anaesthesia_type = anaesthesia_type
        self.timeline.append({"minute": minute, "kind": "induction",
                              "detail": anaesthesia_type})

    def _require_started(self):
        if not self._started:
            raise Error("the record has not started — induce first")

    def record_observation(self, kind, value, *, minute):
        self._require_started()
        if self.closed:
            raise Error("the record is closed")
        if kind not in OBSERVATIONS:
            raise Error(f"unknown observation: {kind}")
        low, high = OBSERVATIONS[kind]
        if not (low <= value <= high):
            raise Error(f"{kind} {value} outside plausible {low}–{high} — "
                        "refused, not clamped")
        obs = {"minute": minute, "kind": "observation", "obs": kind,
               "value": value}
        self.timeline.append(obs)
        return obs

    def administer_agent(self, agent, dose, *, minute, by, witness=None):
        self._require_started()
        if self.closed:
            raise Error("the record is closed")
        if agent not in AGENTS:
            raise Error(f"unknown agent: {agent} — register it first")
        if not (dose and str(dose).strip()):
            raise Error("an administered agent names its dose")
        if by != self.anaesthetist:
            raise Error("only the named anaesthetist records agents")
        agent_class, high_alert = AGENTS[agent]
        if high_alert:
            if not witness or witness == by:
                raise Error(
                    f"{agent} is high-alert — a second named person "
                    "checks the dose")
        ev = {"minute": minute, "kind": "agent", "agent": agent,
              "class": agent_class, "dose": dose, "by": by,
              "witness": witness if high_alert else None}
        self.timeline.append(ev)
        return ev

    def record_event(self, note, *, minute):
        """Intubation, knife-to-skin, blood loss — free-text milestones."""
        self._require_started()
        if not (note and note.strip()):
            raise Error("a milestone says what happened")
        ev = {"minute": minute, "kind": "milestone", "note": note.strip()}
        self.timeline.append(ev)
        return ev

    # ---- emergence ----
    def close(self, *, minute, disposition, handed_over_to, by):
        self._require_started()
        if self.closed:
            raise Error("the record is already closed")
        if by != self.anaesthetist:
            raise Error("the anaesthetist closes her own record")
        if disposition not in DISPOSITIONS:
            raise Error(f"unknown disposition: {disposition}")
        if not handed_over_to:
            raise Error("emergence names the recovery recipient")
        self.closed = {"minute": minute, "disposition": disposition,
                       "handed_over_to": handed_over_to, "by": by}
        self.timeline.append({"minute": minute, "kind": "emergence",
                              "detail": disposition})
        return self.closed

    def as_dict(self):
        body = {"record_id": self.record_id, "case_ref": self.case_ref,
                "patient_ref": self.patient_ref,
                "anaesthetist": self.anaesthetist, "day": self.day,
                "preop": self.preop,
                "anaesthesia_type": getattr(self, "anaesthesia_type", None),
                "timeline": self.timeline, "closed": self.closed,
                "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
