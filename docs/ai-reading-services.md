# AI / LLM / CV for the reading services and radiology

Recommendations for `packages/teleread` domains + `packages/radiology`.
Governing rule (enforced in code by `packages/aigov`): **licence class
decides where a model may run** — embed-clean (Apache/MIT/BSD) may ship;
compose-only (GPL/LGPL/AGPL) runs as an external process/service;
research-only (CC-BY-NC et al.) is **shadow mode only, never shown to a
clinician** (the Meta MMS precedent). Every output lands as a
versioned DRAFT a human confirms or overrules, and override rates are
monitored per model version. A nudge never executes.

## Licence tiers (verified where marked)

| Library | Use | Licence | Class |
|---|---|---|---|
| nnU-Net | segmentation training framework | Apache-2.0 ✓ | embed-clean |
| MONAI | medical-imaging DL framework | Apache-2.0 ✓ | embed-clean |
| pydicom / highdicom | DICOM read/write, SR/TID-1500 structured results | MIT ✓ | embed-clean |
| SimpleITK | registration, resampling, QA | Apache-2.0 ✓ | embed-clean |
| OHIF / Cornerstone3D | web viewer for radiology | MIT ✓ | embed-clean |
| OpenSeadragon | whole-slide viewer for tele-pathology | BSD ✓ | embed-clean |
| NeuroKit2 / wfdb | ECG signal processing, waveform reading | MIT ✓ | embed-clean |
| OpenSlide | WSI decoding | LGPL-2.1 ✓ | compose-only (external process) |
| QuPath | WSI annotation/QA workbench | GPL-3.0 ✓ | compose-only (external, never linked) |
| CLAM | WSI weakly-supervised models | GPL-3.0 ✓ | compose-only |
| llama.cpp / Ollama | local LLM runtime | MIT ✓ (Ollama wired already, off by default) | embed-clean runtime; model weights classed individually |
| MedSAM / TotalSegmentator | segmentation models | **verify weights licence before use** (code Apache, weights vary) | pending verification |
| Meta MMS and other CC-BY-NC models | — | CC-BY-NC | **research-only: rejected for clinical surfacing** |

✓ = verified against the upstream repo. "Verify before use" is itself
the doctrine: an unchecked licence is a research-only licence.

## Per service

**Radiology (PACS)**
- *Triage pre-read* (MONAI/nnU-Net models): draft urgency ordering — the
  STAT-first worklist gets an AI-proposed order a radiologist may
  reshuffle; the proposal is logged and its override rate monitored.
- *Structured reporting*: LLM drafts findings/impression from the
  radiologist's dictated notes into the versioned template
  (llama.cpp/Ollama at the hub, small model, draft-only).
- *DICOM SR*: model outputs serialized with highdicom as SR objects,
  never burned into pixels.
- *Critical-finding detection* (pneumothorax etc.): feeds the existing
  escalation ladder as a *suggestion* — ack still belongs to a human.

**Tele-pathology**
- OpenSeadragon for viewing; OpenSlide/QuPath as external services.
- CLAM-class weakly-supervised models (compose-only) for tumour-region
  highlighting: the pathologist sees a heatmap draft, not a diagnosis.
- Adequacy pre-check: blur/tissue-detection model flags inadequate
  slides at scan time, before they burn a reading slot.

**Tele-ECG**
- NeuroKit2/wfdb for signal QA (lead-off, noise) at capture — a bad
  tracing is rejected at the PHC, not at the reader's desk.
- Arrhythmia pre-classification as draft; STEMI suggestions escalate
  through the ladder with model + version stamped.

**Obstetric-US over-read (usguard)**
- Image-quality scoring drives *sampling*: low-confidence scans raise
  their over-read priority (the QA sample becomes risk-weighted instead
  of flat-random; the random floor stays so the denominator stays
  honest).

**DR screening**
- Fundus gradability check at capture (blur/field) — retake immediately
  while the patient is still in the chair.
- DR pre-grade as draft (already wired through aigov in drscreen.py);
  referable-DR suggestions jump the queue but still need a grader.

**Tele-derm / cervix**
- Image-quality gate at capture; lesion/malignancy suspicion as draft
  flags that set priority — never a diagnosis shown to the patient.

**Pharmacy review**
- LLM interaction/dosing explainer: drafts the *reason* text for the
  pharmacist's review (drug, dose, renal function) — pharmacist edits
  and signs; the draft is never sent directly to the prescriber.

**Tele-ICU**
- Deterioration-risk scoring (NEWS2-style composite plus a learned
  model) feeds the trigger engine as candidate rules — versioned,
  cooldown-governed, draft escalations confirmed by humans.

**MDT**
- LLM case summariser: composes the radiology + pathology verified
  reports into a one-page board brief, with citations back to the
  source reports; the brief is a draft reviewed by the submitter.

## LLM posture (all services)

- Small local models at the hub via Ollama (already in the voice stack,
  off by default) — no PHI leaves the deployment; no hosted-LLM calls
  for clinical content.
- Every LLM output is a draft attached to a named human's decision;
  prompts and template versions are logged so a bad brief is auditable
  to the exact model + prompt that produced it.
- Nigerian-language drafting (en/pcm/ha/yo/ig) for patient-facing
  explanations of results, reviewed before sending — same gate as
  everything else.
