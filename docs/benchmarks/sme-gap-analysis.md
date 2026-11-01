# SME Gap Analysis — OpenHealth OS vs best-in-class per specialty

Status: living document (2026-11-02)
Method: for each specialty SME, list the daily duties, name the
best-in-class reference platform(s), state what this platform has
(package names = shipped and tested), then the honest gaps.
Gap severities: **P0** = patient safety or the specialist literally
cannot do the job without it; **P1** = daily-workflow pain;
**P2** = differentiator / nice-to-have.

Honesty rule for this document: a package existing is not a workflow
existing. "Not wired" means the capability exists somewhere in the
repo but the specialist would never reach it in daily use.

---

## Cross-cutting gaps (hit every specialty)

These repeat below per-specialty; they are listed once here.

1. **No diagnostic viewer in the reading UI (P0).** The
   [radiology worklist](../../apps/pwa/radiology.html) manages queues,
   drafts and verification, but there is no image canvas. OHIF
   (Apache-2.0) is the clean-licence embed for DICOM; OpenSeadragon for
   slides. Until a reader can look at the pixels from the worklist,
   every visual specialty is a paper exercise.
2. **No structured reporting template library (P0 for most).**
   BI-RADS / LI-RADS / TI-RADS, CAP cancer synoptics, KDIGO referral
   letters, WHO semen analysis — specialists report against templates,
   not blank text areas.
3. **No signed report export (P0).** A verified report must leave the
   system as a tamper-evident, clinician-signed artifact (PDF + FHIR
   DiagnosticReport). Today verification is a state change, not a
   document.
4. **Closed-loop result delivery to the referrer (P1→P0 for critical
   findings).** `result_ready_line` + critical acks exist in teleread;
   wiring to notification-service / whatsapp-gateway with receipt
   confirmation is not built.
5. **No voice dictation / ambient drafting (P1).** Every benchmark
   platform now ships it (Rad AI, Nuance DAX). Our aigov shadow-mode
   posture is the right governance; the dictation seam itself is absent.
6. **Cross-package wiring (P1).** Engines exist in isolation:
   eGFR (nephrology) never reaches pharmacy dosing; a derm biopsy never
   reaches telepath; MDT decisions never pull case packs; coldchain
   telemetry never watches cryo tanks; scheduling-service never rosters
   dialysis chairs; rota never informs reading assignment.
7. **Per-clinician scorecards (P1).** Turnaround and discrepancy rates
   exist as engine calls; there is no clinician-facing dashboard, and
   peer review lacks RADPEER-style standardized scoring semantics.
8. **Serial comparison (P1).** Priors: no "current vs previous" view
   for imaging, ECG, or labs. Specialists read trends, not snapshots.
9. **Terminology binding (P1).** terminology-service exists; reports
   don't carry SNOMED/LOINC/ICD codes, which blocks analytics, FHIR
   export, and cross-facility comparability.
10. **No offline specialist mode (P2).** Reading requires connectivity;
    the platform's own doctrine (offline-first for the edge) hasn't
    reached the specialist side.

---

## 1. Radiology / teleradiology

**Daily duties:** worklist triage → open study with priors → hanging
protocols → dictate/structure report → critical-finding calls with
documented read-back → peer review → MDT prep → teaching files.

**Benchmarks:** Sectra IDS7 (Best in KLAS, 13 consecutive years),
Visage 7 (viewer speed reference), Philips HealthSuite, Intelerad
(workflow orchestration), Rad AI (GenAI report drafting).

**We have:** `pacs` (registry, integrity chain, spool, dose ledger,
QC rejects, tiers, AI-draft governance, dcm4chee QIDO pull-sync),
`radiology` (protocol governance, transfer hash checks), `teleread`
(order→assign→worklist→draft→verify→addendum→second opinion→critical
ack→turnaround→peer review), `reading-service` + gateway route + PWA
worklist, `aigov` (model registry, shadow mode).

**Gaps:**
- **P0** — Diagnostic viewer (cross-cutting #1); structured templates:
  BI-RADS/LI-RADS/TI-RADS/RADS family (cross-cutting #2).
- **P0** — Priors/fetching: worklist doesn't auto-pull comparison
  studies from the archive for the same patient.
- **P1** — Hanging protocols (per-modality layout presets); key-image
  / GSPS / DICOM SR pushback to the archive (annotations currently
  live only in our registry, not in the study).
- **P1** — Mammography program tracking (BI-RADS category → recall
  interval → outcome audit). Distinct from one-off reads.
- **P1** — RADPEER-standard peer-review scoring (we record
  discrepancy; we don't score 1-4 with clinical significance).
- **P1** — Sub-specialty + on-call-aware assignment: `prefer` and
  least-loaded exist; rota/credential-time awareness does not
  (rota package not wired).
- **P2** — Teaching files / anonymized case export for training
  (training-service not linked).

## 2. Nephrology / dialysis units

**Daily duties:** pre-round on today's chair list → review session
flags (water, weight, UF) → adjust prescriptions → anemia/iron rounds
→ adequacy review → access surveillance → serology/outbreak watch →
transplant workup progression.

**Benchmarks:** Fresenius Medical Care EMR (largest provider,
machine-integrated), DaVita's proprietary EMR, DSI (1,000+ independent
facilities); registry benchmark: UK Renal Registry / USRDS returns.

**We have:** `nephrology` (KDIGO staging fail-closed, water staleness
flags, HBV+ machine segregation enforced, dialyzer reuse cap, 90-day
serology + seroconversion events, missed sessions overdue-first,
spKt/V adequacy with denominators, catheter-days, sequential
transplant workup).

**Gaps:**
- **P0** — Per-session prescription: target UF, UF/dialysate sodium
  profiling, heparin dose, dialysate composition, blood flow rate.
  Today a session is start/end/weights/spKt/V — the machine settings
  are unrecorded, so intradialytic hypotension has no audit trail.
- **P0** — Anemia management: ESA dosing, iron indices (TSAT/ferritin)
  with protocol-driven adjustment. This is the single biggest
  recurring drug spend and mortality lever in dialysis.
- **P1** — CKD-MBD: Ca/PO4/PTH trends with binder/vitamin-D review.
- **P1** — Chair scheduling: scheduling-service not wired; a unit
  runs on its chair grid (3 shifts × chairs × days).
- **P1** — Machine integration: cmms exists but no link between
  machine fault → chair unavailability → session moved. Dialysis
  machine hours/alarms (fresenius-style integration) absent.
- **P1** — Peritoneal dialysis module: absent entirely (PD exchanges,
  PET adequacy, peritonitis episodes with organism/outcome). PD is
  the dominant home modality in cost-constrained systems.
- **P1** — Adequacy variants: URR, equilibrated Kt/V, weekly std-Kt/V
  for non-thrice-weekly schedules.
- **P1** — Vaccination series (HBV 3-dose with titre response, flu,
  pneumococcal) — serology is tracked; immunization wiring
  (immunization package) not done.
- **P2** — Registry returns (UKRR/USRDS-style), dietitian and
  social-work notes, vascular-access flow surveillance (Qa trends),
  per-session claims (nhia package not wired).

## 3. Fertility / IVF

**Daily duties:** cycle monitoring scans + estradiol → dose adjustment
→ trigger timing → retrieval lists → embryology grading and
fertilization checks → transfer decisions → cryo inventory rounds →
outcome and regulatory returns.

**Benchmarks:** MedITEX (registry-compliant European standard, SART/CDC
export), eIVF, BabySentry/IDEAS, ARTworks; lab-side: time-lapse
systems (EmbryoScope) with KIDScore.

**We have:** `fertility` (dual-partner consents with expiry, sequential
cycle stages, IVF/ICSI fertilization, two-witness embryo moves,
cryo inventory, donor screening, OHSS ack ladder, outcome stats with
denominators, refund-on-failure ledger, hash-checked transport).

**Gaps:**
- **P0** — Stimulation monitoring: serial follicle counts + estradiol
  curves with dose-adjustment decisions per day. A cycle without a
  monitoring record is unmanageable clinically; today stages advance
  but no observations attach.
- **P0** — Embryology grading: fertilization check (2PN), day-3 score,
  Gardner blastocyst grade per embryo with grader identity. Embryos
  exist as ledger entries without morphology.
- **P1** — Cryo tank telemetry: coldchain package exists (temperature
  excursions, alarm ladders) but is not watching tanks/canisters. A
  tank failure discovered by inspection is the IVF equivalent of a
  blood-bank failure.
- **P1** — Semen analysis (WHO 2021 parameters) and source tracking
  for the male side of the couple.
- **P1** — Cumulative live-birth rate per couple across cycles
  (Kaplan-Meier/conservative-optimal estimates) — per-cycle rates
  alone undersell/oversell prognosis.
- **P1** — Registry returns (SART/HFEA shape) — no statute in Nigeria,
  which is exactly why self-reporting is the differentiator.
- **P2** — PGT biopsy workflows, OR/theatre scheduling link (theatre
  package not wired), patient portal with injection calendars
  (EngagedMD-style), gestational-carrier legal pack.

## 4. Pathology / telepathology

**Daily duties:** accession → grossing → cassette/block tracking →
slide QC → sign-out with synoptic template → reflex IHC/molecular →
frozen sections → MDT presentation → adequacy review of cytology.

**Benchmarks:** Sectra Digital Pathology, Philips IntelliSite, Proscia,
3DHISTECH CaseViewer; reporting benchmark: CAP cancer protocols.

**We have:** `telepath` (specimen custody chain with stage sequencing,
fixation minimums, cytology routing to cytopathologists,
reject-inadequate with adequacy rate), teleread engine underneath,
`aigov` for slide-AI shadow mode; OpenSlide/QuPath doctrine is
compose-only (never embedded).

**Gaps:**
- **P0** — Synoptic cancer reporting (CAP-style templates). Free-text
  histology reports are a staging and audit dead end.
- **P0** — WSI viewer integration (cross-cutting #1, slide variant).
- **P1** — Block/slide inventory: paraffin blocks and cut slides are
  physical assets with locations; custody covers the specimen, not
  the derivatives.
- **P1** — Reflex testing: IHC/molecular ordering from the report
  with addendum linkage; today an addendum is manual.
- **P1** — Frozen section workflow: separate clock, separate TAT,
  intra-op phone-read documentation.
- **P1** — MDT case-pack assembly: mdt package not wired to pull
  imaging + pathology + labs into one presentation.
- **P2** — Grossing photo capture (media-service not linked);
  TAT vs RCPath targets dashboard; AI quality gates for
  scanner focus failures.

## 5. Cardiology ECG / tele-ECG

**Daily duties:** confirm device quality → measure intervals →
compare with prior tracings → structured interpretation → STEMI
activation → Holter/event monitor reviews → overread billing.

**Benchmarks:** GE MUSE (ECG management standard), Philips
IntelliSpace ECG; device-side AI: AliveCor Kardia (FDA-cleared
determinations).

**We have:** `teleecg` (device integrity flags via devicereg, STEMI
escalation, 12-lead artifact contract), teleread engine.

**Gaps:**
- **P0** — Measurements: PR/QRS/QTc/axis. An ECG read without
  intervals is not a read. (NeuroKit2/wfdb are licence-clean and
  already named in our AI recommendations; the measurement seam is
  not built.)
- **P0** — Serial comparison (cross-cutting #8) — "new since prior"
  is the clinical question.
- **P1** — Waveform display/calipers in UI (no viewer).
- **P1** — Holter and event-monitor workflows (long-duration records,
  burden calculations, episode annotation).
- **P2** — ECG management system semantics (MUSE-style edit-trail of
  the machine interpretation), overread billing export.

## 6. Tele-ICU

**Daily duties:** unit census review → risk-stratified rounding →
ventilator/sedation bundle checks → sepsis clocks → family updates →
handover documentation.

**Benchmarks:** Philips eICU (predictive analytics + visualization),
Hicuity Health, Advanced ICU Care.

**We have:** `teleicu` (trigger-referenced oversight, consult notes
with required actions, confirm-action must match recommendation,
execution-gap reporting).

**Gaps:**
- **P0** — Continuous vitals ingestion: vitals package exists, not
  wired. Without a live stream there is no "tele" in tele-ICU —
  only episodic consults.
- **P0** — Severity/early-warning trending: NEWS2, SOFA, qSOFA with
  trend surfaces. A single score is a snapshot; the trajectory is
  the finding.
- **P1** — Sepsis bundle clocks (1-hour/3-hour/6-hour elements with
  per-element completion).
- **P1** — Bed board / rounding lists: prioritized census with the
  day's must-dos (ventilator days, sedation interruption, line days
  — linkable to our catheter-days pattern).
- **P1** — APACHE-style acuity for benchmarking units (denominators
  again: mortality without acuity adjustment is a fiction).
- **P2** — Structured family-update log; inter-unit transfer notes.

## 7. Pharmacy clinical review

**Daily duties:** high-risk order queue → interaction/duplication/
allergy checks → renal/hepatic dose adjustment → antibiotic timeout
→ prescriber interventions with tracked responses → ADR reporting.

**Benchmarks:** Epic Willow + First Databank, BD HealthSight,
MedAware (ML-based outliers).

**We have:** `pharmreview` (high-risk-only queue, interventions with
recommendation+reason, override requires reason, acceptance stats),
`steward` (antimicrobial stewardship package exists).

**Gaps:**
- **P0** — Knowledge base: no interaction/dose database. Commercial
  ones (FDB, Lexicomp) are licensed content we cannot embed; the
  honest path is a curated open subset + local formulary rules —
  not built.
- **P0** — Renal dosing link: nephrology computes eGFR; pharmreview
  never sees it. A vancomycin order and a G3b patient must meet
  automatically.
- **P1** — Antibiotic timeout (48–72h review prompt) — steward
  package not wired into the review queue.
- **P1** — Medication reconciliation on admission/discharge (inpatient
  package not wired to pharmreview).
- **P2** — IV compatibility, TPN double-check workflow, ADR
  (Yellow-Card-shaped) export.

## 8. Diabetic retinopathy screening

**Daily duties:** session list → image quality check at capture →
grade or accept AI triage → refer with urgency → track referral
completion → treatment outcomes.

**Benchmarks:** autonomous AI is the reference posture here:
LumineticsCore (first FDA De Novo, 2018), EyeArt (96% sens / 88% spec
for mtmDR; only one cleared to flag vision-threatening DR separately),
AEYE-DS (only handheld-camera clearance), iPredict-DR (cleared July
2026). Program-side: EyePACS (two-grader + arbitration model).

**We have:** `drscreen` (screened→referred→attended→treated cascade
with attrition denominators, AI drafts only through aigov governance).

**Gaps:**
- **P0** — Camera-level image quality gate at capture (gradability
  feedback to the photographer in-session; the AI benchmarks report
  49–75% nonmydriatic gradability — an ungradable image is a failed
  screen, not a negative).
- **P0** — Two-grader arbitration (EyePACS model): AI+human or
  human+human disagreement routes to a senior arbiter. Today one
  reader decides.
- **P1** — Grade-driven surveillance intervals (ETDRS-aligned recall:
  annual vs 6-month vs urgent) — the cascade has stages but not
  interval assignment.
- **P1** — Pregnancy pathway (pregnant diabetics have their own
  screening clock).
- **P1** — Treatment tracking: laser/injection episodes close the
  loop (treated stage exists; anti-VEGF series counting does not).
- **P2** — Autonomous-mode governance: our aigov is built for
  shadow/assistive. A regulator-cleared autonomous result is a
  different legal posture (no human reads the image) and needs its
  own policy gate before deployment.

## 9. Teledermatology

**Daily duties:** triage incoming store-and-forward cases →
dermoscopy review → 2-week-wait cancer decisions → biopsy orders →
surveillance of high-risk patients.

**Benchmarks:** Skin Analytics DERM (NHS-deployed, autonomous-adjacent
triage), FotoFinder (dermoscopy + total body photography), DermEngine.

**We have:** `telederm` (structural consent kinds, NTD surveillance
flags, aggregate-only reporting).

**Gaps:**
- **P0** — Image quality and dermoscopy standards at capture
  (dermoscopic vs clinical view, scale marker, lighting) — a bad
  skin photo is an unreadable case.
- **P0** — Cancer-pathway clock: suspicious-lesion → specialist seen
  within the two-week-wait target, tracked. This is the metric the
  whole pathway exists for.
- **P1** — Biopsy → pathology loop: telepath exists; the derm case
  never becomes a specimen (not wired).
- **P1** — Lesion mapping / total-body photography for high-risk
  patients (melanoma surveillance).
- **P2** — Teledermoscopy AI triage (aigov shadow-mode ready; no
  model registered).

## 10. Cervical screening

**Daily duties:** screening register call/recall → sample/VIA →
same-visit treatment → colposcopy referral → histology confirmation →
treatment-device QA.

**Benchmarks:** MobileODT EVA (VIA imaging + VisualCheck AI),
program-side: national call/recall registries (e.g. UK NHSCSP).

**We have:** `cervixscreen` (VIA results, same-visit treatment metric,
treat-only-on-positive, refer-only-on-suspicious, open-loops
overdue-first, program stats with denominators).

**Gaps:**
- **P0** — HPV DNA primary screening pathway: WHO's preferred
  strategy is now HPV testing (with VIA/triage only for positives).
  The module is VIA-only.
- **P1** — Call/recall register: households/pophealth registries
  exist but no screening-eligible cohort list with invite/reminder
  ladders drives the program.
- **P1** — Colposcopy module + histology confirmation loop
  (telepath not wired; VIA-positive → treat vs biopsy decision).
- **P2** — Treatment device QA (thermal ablation unit checks,
  linkable to cmms/devicereg).

## 11. Obstetric ultrasound

**Daily duties:** dating scan → anomaly (20-week) protocol scan →
growth scans → Dopplers → biometry documentation → findings
escalation.

**Benchmarks:** GE Voluson (SonoLyst AI), Philips EPIQ; reference
standards: INTERGROWTH-21st / WHO growth curves; ISUOG protocol
checklists.

**We have:** `usguard` (hash-seeded deterministic QA sampling,
flagged-exam 100% review, sonographer concordance feedback).

**Gaps:**
- **P0** — Biometry with reference ranges: HC/AC/FL/BPD → gestational
  age and percentile (INTERGROWTH-21st). Without measurements there
  is no growth assessment, only image review.
- **P0** — Anomaly-scan protocol checklist (ISUOG 20+2 planes with
  per-plane adequacy). usguard samples images; it doesn't enforce
  protocol completeness.
- **P1** — Estimated fetal weight, Doppler indices (umbilical/middle
  cerebral artery, CPR), cervical length.
- **P1** — Serial growth charting (growthmon exists for pediatrics;
  fetal growth not linked).
- **P2** — AI plane-detection (SonoLyst-style) in aigov shadow mode.

## 12. MDT / oncology boards

**Daily duties:** case list prep → case packs (imaging + pathology +
labs + history) → quorum discussion → recorded decision with named
owner → execution tracking → outcome review.

**Benchmarks:** Epic Beacon MDT module, Roche Navify Tumor Board,
Flatiron (oncology data).

**We have:** `mdt` (chair-only convening, quorum 3, append-only
decisions with actions, execution status).

**Gaps:**
- **P0** — Case-pack auto-assembly: pacs, telepath, lis, and the
  record exist as packages; nothing pulls them into one per-patient
  board pack. Preparing packs by hand is half the MDT coordinator's
  week.
- **P1** — Decision → order wiring: "start chemo regimen X" should
  land in cpoe; today the action is text with an owner.
- **P1** — Staging capture (TNM edition-aware) in the record.
- **P2** — Clinical-trial matching, re-discussion tracking (patient
  returning to board after interval imaging).

---

## Priority order (what to build next)

Grouped by blast radius:

1. **Viewer integration (P0, all visual specialties)** — embed OHIF
   (DICOM) + OpenSeadragon (WSI) + waveform canvas (ECG) into
   reading-service sessions. Nothing else in this document unblocks
   as many SMEs at once.
2. **Structured reporting + signed export (P0, all)** — template
   engine (RADS, CAP synoptics, KDIGO, WHO), SNOMED/LOINC binding,
   signed PDF + FHIR DiagnosticReport.
3. **Cross-package wiring (P0/P1)** — eGFR→pharmacy dosing;
   biopsy→telepath; MDT case packs; coldchain→cryo tanks;
   scheduling→dialysis chairs; vitals→teleicu; rota→reading
   assignment. These are integration sprints, not new engines.
4. **Closed-loop result delivery (P0 for critical findings)** —
   notification-service/whatsapp-gateway with receipt confirmation
   from the referrer.
5. **Nephrology session prescription + anemia/CKD-MBD + PD module
   (P0)** — the deepest single-specialty deficit.
6. **Fertility stimulation monitoring + embryology grading (P0)** —
   the lab notebook is missing.
7. **Serial comparison + measurements (P0)** — ECG intervals, imaging
   priors, fetal biometry references.
8. **Two-grader arbitration + capture-time quality gates (P0,
   drscreen/telederm)** — program credibility hinges on these.
9. **Per-clinician scorecards + RADPEER semantics (P1)**.
10. **Pharmacy knowledge base + antibiotic timeout wiring (P0/P1)**.

Deliberately deferred: autonomous-AI deployment postures (regulatory
strategy, not engineering), commercial drug/interaction databases
(content licensing), native mobile apps for specialists (PWA suffices
until viewer performance demands otherwise).
