# ADR-0068 — dcm4chee-arc-light as the hub archive (edge/hub PACS topology)

Status: accepted · Date: 2026-11-01

## Context

ADR-0028 composed Orthanc (GPLv3) as the PACS appliance, network-only,
with `services/imaging-service` as the DICOMweb facade. That answer is
right for an edge facility: Orthanc is small, scriptable, and tolerant of
rough hardware. It is not the answer for a state or national hub:
multi-facility ingestion, IHE profiles, HL7 v2 ADT/ORM/ORU, MPPS, storage
commitment, multi-AE routing, and LDAP-centralised AE configuration are
what a hub archive is for.

[dcm4chee-arc-light 5.x](https://github.com/dcm4che/dcm4chee-arc-light)
is that archive: a Java EE (WildFly) DICOM application with full
DICOMweb (QIDO-RS / WADO-RS / STOW-RS), native DIMSE
(C-STORE/C-FIND/C-MOVE/C-GET, MPPS, storage commitment), HL7 v2, IHE
XDS-I, export/routing rules, and Keycloak-secured UI + REST in the
`-secure-ui` variant. The companion
[dcm4che](https://github.com/dcm4che/dcm4che) toolkit supplies utilities
(stowrs, wadors, dcmvalidate, deidentify) we invoke as external CLI at
most — never linked.

## Licence analysis

- **dcm4chee-arc-light**: Mozilla Public License 1.1 (per repo README).
- **dcm4che library/tools**: tri-licensed MPL 1.1 / GPL 2.0 / LGPL 2.1
  (per-file headers); we elect **MPL 1.1**.
- MPL 1.1 is weak, file-level copyleft. It is **not** GPL/AGPL and not
  BSL/SSPL/Elastic — it is outside every banned class in LICENSING.md.
- Regardless, the standing doctrine is stricter than the licence: we
  **compose, never embed**. We reference upstream Docker images, talk
  DICOMweb/DIMSE/HL7 over the network, vendor no source, fork nothing,
  patch nothing. If upstream fixes something, `docker compose pull` is
  the entire maintenance story. (This is also why the old MPL/GPL
  licence-pairing debate is irrelevant here: nothing links.)

## Decision — edge/hub topology

```
modality ──DIMSE──► Orthanc (edge, GPLv3) ──STOW-RS──► dcm4chee (hub, MPL)
                         ▲                                  ▲
                         └──── imaging-service facade ──────┘
                    (packages/pacs DICOMwebAdapter — vendor-neutral)
```

- **Edge**: Orthanc stays. Small footprint, restart-anywhere.
- **Hub**: dcm4chee-arc-light (`deploy/imaging/docker-compose.dcm4chee.yml`).
- **Governance stays ours**: study registry + dedupe, sha256 integrity
  chain, offline spool, dose ledger, QC repeat-rate, storage-tier honesty,
  AI draft-only governance (`packages/pacs`), and the radiology workflow —
  credential registry, assignment, reading-session audit, reports as
  append-only documents, critical-finding escalation, TAT stats, peer
  review, cross-facility transfer gating (`packages/radiology`). The
  archive stores and serves; the platform decides what is *true*.
- **Reconciliation runs both ways**: `orphans_and_gaps()` against QIDO —
  an appliance study without a registry record is an orphan, a registry
  study the appliance lost is a disaster; both are findings.

## Security posture

- Production deploys use `:5.35.2-secure-ui` + `dcm4che/keycloak`; REST
  is OIDC-protected (realm `dcm4che`). `DICOMwebAdapter.access_token()`
  takes a client-credentials token and **refuses to send unsigned
  requests** to a secured archive.
- WildFly admin (9990) and the UI are never exposed beyond the hub
  network; the platform's imaging-service remains the only facade.
- DICOM TLS (11112→2762) between facilities and hub at national scale.

## Consequences

- One new compose profile; zero new code dependencies; zero licence risk
  beyond what composition already carries.
- dcm4che toolkit utilities may be used as CLI in ops scripts only, with
  the same compose-don't-embed rule.
- If a future national deployment prefers Orthanc-only or another
  DICOMweb archive, nothing in packages/pacs or packages/radiology
  changes — the adapter contract is the seam.
