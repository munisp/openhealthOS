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

## Integration (shipped; live-verify still pending)
The parts that do not depend on model downloads are now wired into the
platform, all fail-closed:
1. **Silver free-text gate** (`packages/lakehouse/lakehouse.py`) —
   `silver()`/`build()` take a `scrub` callable. Any event declaring
   `free_text: [payload keys]` is REFUSED (FreeTextGateError) if no
   scrubber is provided; otherwise declared keys are de-identified on
   entry. Raw narrative can never reach the analytical plane silently.
2. **Scrubber hook** (`packages/mednlp/silver_hook.py`) —
   `make_scrubber()` wires the spike's de-id into the lakefeed today;
   when the sidecar is live the same seam swaps to model-grade NER with
   the pattern pack as high-precision fallback.
3. **Release model gate** (`tools/release/sbom.py::model_gate`) — every
   `**/models-manifest.json` in the repo is vetted at release time:
   licence allowlist + 64-hex sha256 pins. The current manifest carries
   `PENDING-LIVE-VERIFY` and the release gate correctly refuses it —
   the integration test pins this refusal and flips to clean only after
   live verification lands real hashes.
4. **Sidecar** (`infra/compose/mednlp.yaml` +
   `packages/mednlp/sidecar_server.py`) — T0/T1 loopback-only OpenMed
   REST sidecar. Starts only where gated models are provisioned;
   otherwise exits 2 loudly (verified in this sandbox) while the
   silver gate keeps protecting the pipeline with the pattern pack.
5. **Overlap resolution** (`ngpatterns.py`) — found by the integration
   test: an 11-digit phone number also matches the bare-NIN pattern, and
   double redaction at the same coordinates ate clinical text. Overlaps
   now resolve to the highest-scoring span before redaction.

## Consequences
- Adopted in part: the lakehouse silver plane enforces de-id at entry;
  the release train vets model artifacts; PHCs gain coding assist.
  Notes-derived analytics light up fully once models are live-verified.
- Deferred: live model verify (network-gated), Hausa/Yorùbá/Igbo NER
  fine-tuning, FHIR DocumentReference de-id pipeline.
