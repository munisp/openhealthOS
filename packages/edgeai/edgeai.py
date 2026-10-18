#!/usr/bin/env python3
"""On-device AI rails — approval-gated decision support at the edge
(ADR-0013, innovation).

Runs on the facility tablet/NUC with no uplink. Today the executable model
is a transparent rule set over structured symptoms + vitals; tomorrow a
tflite/onnx model registers through the same ModelManifest seam WITHOUT
changing the safety contract:

  * output is always a DRAFT (status="DRAFT", approval_required=True)
  * every draft carries provenance (model id/version, rule/inputs hash)
  * no code path here writes to the record — commit happens only through
    triage.ApprovalGate (Proposal → Decision → Commit → Audit, ADR-0004)

Rule-based drafter covers nurse/CHW red flags where connectivity is worst:
respiratory distress, sepsis risk, obstetric danger, dehydration,
hypoglycaemia, hypertensive crisis. It never diagnoses; it drafts an
assessment + rationale for a human to approve, edit, or reject.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field


# ---------------------------------------------------------------- red flags
# Each rule: id, any-of symptom sets OR vital predicate, priority, note.
SYMPTOM_RULES = [
    {"id": "RESP-DISTRESS", "priority": "P1",
     "any_of": [{"fast_breathing", "chest_indrawing"}, {"stridor"}, {"cyanosis"}],
     "note": "respiratory distress — oxygen, refer urgently"},
    {"id": "SEPSIS-RISK", "priority": "P1",
     "any_of": [{"fever", "lethargy"}, {"fever", "confusion"}, {"fever", "cold_extremities"}],
     "note": "sepsis screen positive — fluids/antibiotics per protocol, refer"},
    {"id": "OBSTETRIC-DANGER", "priority": "P0",
     "any_of": [{"pregnant", "vaginal_bleeding"}, {"pregnant", "convulsions"},
                {"pregnant", "severe_headache", "blurred_vision"}],
     "note": "obstetric emergency — immediate referral"},
    {"id": "DEHYDRATION-SEVERE", "priority": "P1",
     "any_of": [{"diarrhoea", "sunken_eyes", "unable_to_drink"}, {"diarrhoea", "lethargy"}],
     "note": "severe dehydration — ORS plan C / IV fluids en route"},
    {"id": "HYPOGLYCAEMIA", "priority": "P1",
     "any_of": [{"diabetic", "confusion"}, {"diabetic", "sweating", "tremor"}],
     "note": "suspected hypoglycaemia — glucose now, recheck in 15 min"},
]

# Vital thresholds: (field, comparator, value, priority, note)
VITAL_RULES = [
    ("spo2", "<", 92, "P1", "SpO2 <92% — oxygen, refer urgently"),
    ("resp_rate", ">", 40, "P1", "tachypnoea — count for a full minute, assess for pneumonia"),
    ("heart_rate", ">", 130, "P2", "tachycardia — recheck, assess for sepsis/dehydration"),
    ("temp_c", ">=", 39.0, "P2", "high fever — RDT, paracetamol, danger-sign screen"),
    ("systolic_bp", ">=", 180, "P1", "possible hypertensive crisis — quiet rest, urgent review"),
    ("systolic_bp", "<", 90, "P1", "hypotension — shock screen, fluids, urgent referral"),
    ("glucose_mmol", "<", 3.0, "P1", "hypoglycaemia — treat immediately"),
]

_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b}
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


@dataclass(frozen=True)
class ModelManifest:
    """Registration seam for any on-device model. Only kind="rules" is
    executable today; ML kinds (tflite/onnx) register here and are held as
    manifests until a sandboxed runner is approved — the draft contract is
    identical either way."""
    model_id: str
    version: str
    kind: str                    # rules | tflite | onnx
    entrypoint: str = ""         # model artifact path for ML kinds
    sha256: str = ""             # artifact digest for ML kinds

    def executable(self) -> bool:
        return self.kind == "rules"


RULES_MANIFEST = ModelManifest("openhealth-edgeai-rules", "1.0.0", "rules")


@dataclass
class Draft:
    draft_id: str
    patient_id: str
    status: str                  # always "DRAFT"
    approval_required: bool      # always True
    model: dict                  # manifest provenance
    assessment: dict
    rationale: list[str]
    inputs_digest: str
    created_ts: float = field(default_factory=time.time)

    def to_proposal_content(self) -> dict:
        """Shape compatible with triage.ApprovalGate.propose()."""
        return {"assessment": self.assessment, "rationale": self.rationale,
                "ai_draft": {"model": self.model, "inputs_digest": self.inputs_digest,
                             "draft_id": self.draft_id}}


class ModelRegistry:
    """Holds manifests; refuses to execute anything that is not an approved
    local ruleset. This is where future ML plugs in — gated, versioned,
    auditable."""

    def __init__(self):
        self._manifests: dict[str, ModelManifest] = {RULES_MANIFEST.model_id: RULES_MANIFEST}

    def register(self, m: ModelManifest) -> None:
        if not m.model_id or not m.version:
            raise ValueError("manifest requires model_id and version")
        if m.kind != "rules" and (not m.entrypoint or not m.sha256):
            raise ValueError("ML manifests require entrypoint and sha256")
        self._manifests[m.model_id] = m

    def get(self, model_id: str) -> ModelManifest:
        try:
            return self._manifests[model_id]
        except KeyError:
            raise KeyError(f"unregistered model {model_id}") from None

    def executable(self, model_id: str) -> bool:
        return self.get(model_id).executable()


class EdgeAIDrafter:
    """Drafts assessments from structured intake. Never writes records."""

    def __init__(self, registry: ModelRegistry | None = None,
                 model_id: str = RULES_MANIFEST.model_id):
        self.registry = registry or ModelRegistry()
        self.manifest = self.registry.get(model_id)
        if not self.manifest.executable():
            raise ValueError(f"model {model_id} is registered but not executable on this build")

    def draft(self, patient_id: str, symptoms: list[str] | set[str],
              vitals: dict | None = None, context: dict | None = None) -> Draft:
        symptoms = set(symptoms or [])
        vitals = vitals or {}
        context = context or {}
        if context.get("pregnant"):
            symptoms.add("pregnant")  # context feeds obstetric rules honestly
        fired: list[tuple[str, str, str]] = []  # (priority, id, note)

        for rule in SYMPTOM_RULES:
            if any(req <= symptoms for req in rule["any_of"]):
                fired.append((rule["priority"], rule["id"], rule["note"]))
        for fname, op, thresh, prio, note in VITAL_RULES:
            val = vitals.get(fname)
            if isinstance(val, (int, float)) and _CMP[op](val, thresh):
                fired.append((prio, f"VITAL-{fname.upper()}", note))
        if not fired:
            assessment = {"disposition": "ROUTINE", "priority": "P4",
                          "summary": "no red flags — routine pathway"}
            rationale = ["no symptom rule fired", "no vital threshold breached"]
        else:
            fired.sort(key=lambda f: _PRIORITY_ORDER.get(f[0], 9))
            top = fired[0]
            assessment = {"disposition": "REFER-URGENT" if top[0] in ("P0", "P1") else "REVIEW-TODAY",
                          "priority": top[0], "summary": top[2],
                          "also_flagged": [f[1] for f in fired[1:]]}
            rationale = [f"{rid}: {note}" for _, rid, note in fired]

        digest = hashlib.sha256(json.dumps(
            {"symptoms": sorted(symptoms), "vitals": vitals, "context": context},
            sort_keys=True).encode()).hexdigest()
        return Draft(
            draft_id=f"draft-{uuid.uuid4().hex[:12]}",
            patient_id=patient_id, status="DRAFT", approval_required=True,
            model={"id": self.manifest.model_id, "version": self.manifest.version,
                   "kind": self.manifest.kind},
            assessment=assessment, rationale=rationale, inputs_digest=digest,
        )
