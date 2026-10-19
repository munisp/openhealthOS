# ADR-0028: Imaging, clinical video, and document intelligence

Status: accepted (implemented)
Deciders: platform architecture

## Context

Unstructured clinical data — MRI/CT/X-ray DICOM studies, ultrasound and
scope videos, scanned referral letters, lab-report PDFs, ID cards — is
where Nigerian facilities lose the most information: films walk away with
patients, scans sit in WhatsApp threads, and paper referrals are
unsearchable. The platform needs one rail for DICOM, one for video, and
one for document OCR/parsing, each obeying the licence doctrine and the
offline-first doctrine.

## Decision

**Compose, never fork, never embed copyleft.**

| Capability | Component | Licence | Boundary |
|---|---|---|---|
| PACS | Orthanc (standalone container) | GPLv3 | DICOMweb/REST network only |
| DICOMweb facade + worklist | `services/imaging-service` (Go) | ours | — |
| Object storage | MinIO via media-service | AGPLv3 | S3 wire protocol |
| Video transcode (default) | `workers/transcoder` → ffmpeg subprocess | LGPL | process boundary |
| Video transcode (deploy) | `workers/transcoder-ffmpeg` → rsmpeg | MIT bindings | dynamic link to libav*, deploy image only |
| OCR floor | Tesseract subprocess | Apache-2.0 | process boundary |
| OCR scans/IDs | PaddleOCR sidecar (`ml/docai`) | Apache-2.0 | HTTP |
| Layout/table parsing | Docling sidecar (`ml/docai`) | MIT | HTTP |
| Visual QA escalation | VLM in `ml/` fence (torch exception) | model-dependent | HTTP, bounded prompts |
| DICOM viewing | Cornerstone3D/OHIF in PWA | MIT | browser |

### Imaging rail

`imaging-service` fronts Orthanc with a DICOMweb facade (QIDO-RS query,
WADO-RS retrieve, STOW-RS store) and adds what Orthanc lacks:

- **Triage worklist** — STAT keyword rules (trauma, pneumothorax, stroke,
  bleed…), read SLAs (stat 1h / urgent 4h / routine 24h), priority
  ordering stat→urgent→routine, unread-first, FIFO within a band.
  Radiologist signature clears the study. **AI triages workflow, never
  diagnoses** — an autonomous diagnosis engine is a medical device
  (SaMD); this rail keeps the signature human.
- **De-identification export** — keep-list (never deny-list) of DICOM
  tags, mandatory pseudonyms, k-anonymity floor of 5, burned-in pixel
  risk flag for US/CR/DX/ES modalities (same doctrine as gisexport).
- **STOW header fallback** — when PACS webhooks are unavailable,
  `X-Modality` / `X-Description` headers register the study on the
  worklist directly.

### Video rail

Clinical video (ultrasound sweeps, scope captures, wound reviews) is too
large for the 25 MiB blob path, so media-service gained **multipart
upload** (SHA-256 per part, contiguous assembly, restart-safe sessions)
and **HTTP Range streaming**. The transcoder worker plans a rendition
ladder that never upscales (≤1080/720/480/360 by source height), emits
4s HLS segments, stores `master.m3u8 + <height>p/seg_NNNNN.ts` back into
media-service, and reports status to `imaging-service /videos/{id}/update`.

Two backends, one contract (`oh_transcoder::Backend`):

- `CliBackend` — ffmpeg subprocess; builds everywhere, zero deps.
- `RsmpegBackend` — rsmpeg (MIT) in-process; compiles **only** in
  `deploy/imaging/Dockerfile.transcoder` (FFmpeg dev headers + crates.io
  exist there; the sandbox has neither). Sandbox CI tests the pure logic
  and the CLI backend.

### Document intelligence

`packages/docintel` (stdlib-only Python) is the orchestrator:

1. **Classify** the document (lab-report / referral / id-card / form /
   scan) by filename + first text.
2. **Route** to a backend chain — structured docs start at Docling,
   degraded scans at PaddleOCR, Tesseract is always the floor.
3. **Fallback** on unavailability or error, recording every hop.
4. **Quality gate** — blocks under confidence 0.60 are flagged for human
   review; when more than a third of blocks are weak and a VLM is
   reachable, weak blocks are re-read by the VLM (bounded prompts:
   transcription, never diagnosis).
5. **Provenance** — every block carries engine, engine_version,
   confidence, page, bbox. Downstream: semsearch indexes, docclean
   SimHash-dedupes, the review queue gets the weak blocks.

The heavy engines live in `ml/docai` — a stdlib `http.server` sidecar
with lazy imports that answers `/healthz` with the engines it actually
loaded. Absent engines simply remove themselves from the chain.

## Consequences

- No GPL/AGPL code is linked into platform binaries; copyleft components
  run as containers over documented wire protocols.
- The sandbox builds and tests everything except the rsmpeg crate and
  the docai engine imports — both are contract-tested via the
  `Backend`/HTTP interfaces and built in CI/deploy.
- Video and DICOM are first-class platform citizens without weakening
  the offline doctrine: multipart resumes, Range streams, worklists are
  local-first.
