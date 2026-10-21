# ADR-0065: Clinical NLP (OpenMed) spike — de-identification, coding assist, model licence gate

## Status
Spike (reference paths proven offline; model-path adoption deferred to
live verify, same gate discipline as ADR-0064).

## Context
Three platform gaps are clinical-NLP problems:

1. **Free-text notes can never leave bronze** — ADR-0060's k-anonymity
   floor protects structured aggregates, but narrative notes (where PHC
   care actually lives) are unusable for analytics without de-id.
2. **Coding quality at PHC level** — most facilities have no trained
   coders; free text like "malaria fever, gave amodiaquine" never becomes
   ICD-10/NAFDAC codes, degrading DHIS2 indicators and registries.
3. **Model artefacts are a new supply-chain class** — OpenMed's SDK is
   Apache-2.0, but per-model licences vary; the SBOM gate covers code,
   not models.

OpenMed (maziyarpanahi/openmed) is a local-first clinical NLP toolkit:
Apache-2.0 SDK, air-gapped model loading with sha256 integrity
verification, PII de-identification, clinical NER, and — decisively for
this spike — a pure-Python `openmed.risk` k-anonymity engine that runs
with NO model download.

## Doctrine check
- Licence: SDK Apache-2.0 ✓; per-model licences vetted by the new gate ✓.
- Compose-don't-fork: versioned pip dependency + pinned model artifacts
  behind a REST sidecar at T0/T1; nothing vendored, nothing forked.
- Offline: models load from a local directory with integrity
  verification; `openmed.risk` needs no models at all.
- Placement: **T0/T1 sidecar only**. T2 edge / T3 devices untouched;
  rule-based triage in howUdeyChat stays rule-based.

## Spike results (packages/mednlp, 13/13 green)
1. **`ngpatterns.py`** — Nigerian PII pattern pack written to OpenMed's
   PIIPattern field spec (NIN with context gating, +234/0 phones, OHF
   facility ids, NHIA enrollee ids, email). Candidate upstream
   contribution — no Nigerian language pack exists in OpenMed today.
2. **`deid.py`** — note-level de-id: detection → threshold → mask/hash
   redaction (clinical content survives: "malaria" and "amodiaquine"
   remain after all identifiers are stripped). The gold-layer gate calls
   REAL `openmed.risk.analyze_k_anonymity` + minimal suppression —
   executed offline in CI today; openmed missing ⇒ RuntimeError,
   never a silent release.
3. **`coding_assist.py`** — free text → ICD-10/NAFDAC/NHIA-tariff mapping
   against the real terminology snapshots ("malaria" → B50.9). Unmapped
   mentions are reported unmapped — coding that lies is worse than none.
   The rule spotter is the honest seam where OpenMed model NER drops in.
4. **`model_gate.py`** — model licence vetting: every artifact needs an
   allowlisted licence AND a sha256 pin (the same hash OpenMed verifies
   at download, so vetted artefact = verified bytes). NC licences,
   unvetted licences, missing pins all block the release. Fail-closed.
5. **`run_openmed.sh`** — live path: install `openmed[hf]`, verified
   model download, REST sidecar contract; exits 2 honestly when the
   network is unavailable (it is, in this sandbox — HF is unreachable
   and OpenMed's own integrity check correctly refuses unverified bytes).

## Consequences
- If adopted: lakehouse gains notes-derived analytics; PHCs gain coding
  assist; the release train gains a model supply-chain gate.
- Deferred: live model verify (network-gated), Hausa/Yorùbá/Igbo NER
  fine-tuning, FHIR DocumentReference de-id pipeline, OpenMed REST
  sidecar in infra/compose (lands with live verify).
