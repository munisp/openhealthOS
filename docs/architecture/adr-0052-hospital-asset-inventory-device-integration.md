# ADR-0052: Hospital assets — signed QR/barcode identity, custody spine, modality worklist, device ingest, ERP cycle counts

Status: accepted (2026-10-20)

## Context

Hospital management asked four questions this ADR answers:

1. **"Where is the MRI?"** Assets moved between wards with no trail;
   equipment-service tracked status and tickets but not *location* or
   *custody*.
2. **"How do we trust a barcode?"** Printed QR/asset labels are trivially
   copiable — a thief relabels the stolen pump with a dead pump's tag.
3. **"How do devices integrate?"** Imaging had DICOMweb for studies but
   no *scheduled* worklist for modalities (the MRI should pull today's
   list, not have patient demographics typed at the console — the #1
   cause of orphaned studies), and point-of-care devices (vitals
   monitors, glucometers) had no ingest path into the record at all.
4. **"Does the shelf match the ledger?"** stockcheck (ADR-0018)
   reconciles *paper flows* for fraud; nobody reconciled *physical
   counts* against ERP book stock — the classic shrinkage gap.

## Decision

### 1. Signed asset tags — `packages/assettag` (stdlib Python) + Go mirror

Payload `OHAST1.<facility>.<asset>.<issued_unix>.<hmac8-hex>` — HMAC-
SHA256 over the body, truncated to 16 hex chars. Verified **offline** at
scan time (no network call needed on a ward with dead Wi-Fi), fail-
closed: without `OH_ASSET_SECRET` (≥16 chars) minting and scanning are
disabled with 503, never guessed. Tags older than 5 years verify but are
flagged `stale` (re-label programme trigger). The Go mirror
(`equipment-service/tags.go`) is byte-compatible — the same QR sticker
verifies in both languages.

### 2. Custody spine — equipment-service

`POST /equipment/{id}/tag` mints, `POST /scan` applies a verified
action (`locate | transfer | maintenance_flag | check_in`), `GET
/custody?asset=` reads the trail. Every scan appends an immutable
custody event naming **who** (actor required — the audit names who);
`maintenance_flag` flips a working asset to in-maintenance (scan-to-
report). Memory store and Postgres store both implement it; Postgres
persists to `custody_events` (migration 0017) with `equipment.location`
updated in the **same transaction** as the event insert — the location
can never drift from its evidence.

### 3. Modality worklist + device ingest — imaging-service

- **MWL** (`GET/POST /mwl`, `POST /mwl/{id}/status`): modalities pull
  today's scheduled procedures filtered by modality + station AE title —
  the DICOM MWL pattern over plain HTTP+JSON for edge devices, while
  real DICOM nodes still go through Orthanc (composed GPLv3, never
  linked). Lifecycle `scheduled → arrived → in-progress → completed`
  (+cancelled) with guarded transitions: a completed study can never
  silently revert.
- **Device ingest** (`POST /devices/readings`): point-of-care readings
  validated at the boundary (device, patient, code, unit required),
  rendered as minimal FHIR R4 Observations (LOINC code, UCUM unit), and
  forwarded to the SHR **fail-soft** — SHR down → 202 with
  `forwarded:false`, reading kept in a 500-deep local ring, stats
  exposed at `/devices/stats`.

### 4. ERP cycle counts — `packages/stocktake` (stdlib Python)

Barcode scan streams (JSONL) become counts; double-scan suppression
drops same-barcode re-triggers within a window; a barcode→item map
resolves GTIN/EAN scans to item codes. `reconcile` diffs counts vs book
stock (ERPNext Bin export) with verdicts MATCH/SHORT/OVER/UNCOUNTED/
UNKNOWN and explicit unit/percent tolerances — variances are questions,
not accusations (same doctrine as stockcheck). Closes into an ERPNext
**Stock Reconciliation draft** posted through the existing REST bridge
(idempotent via `openhealth_id`, offline-spooled) — ERPNext stays the
book of record, we stay composed, never embedded.

## Consequences

- Every asset tag is self-authenticating; a photocopied sticker fails
  signature verification and the scan is rejected (tested).
- The custody spine answers "who had it last" for every scan since
  deployment — theft investigation and maintenance compliance both read
  the same table.
- Modalities stop orphaning studies; device vitals reach the SHR within
  seconds of measurement, or queue honestly when the SHR is down.
- Cycle counts become routine (sweep mode: each scan = 1 unit) instead
  of annual clipboard events; ERP variance posting is one CLI call.
- Limitations (honest): MWL is in-memory this pass (Postgres persistence
  is the next iteration); device ingest assumes devices POST JSON —
  serial/HL7v2 devices need an edge adapter, out of scope here.
