# ADR-0056: Clinical trust plane — CPOE, CDS Hooks, proxy consent, USSD self-service

Date: 2026-10-20 · Status: accepted

## Context

The national-platform audit (docs/national-platform-audit.md) found the
foundation premiere-grade but clinical depth thin: no order-entry safety, no
standard alert bus, no caregiver access, and shallow unstructured-channel
self-service. A hospital will not run on a platform whose prescriptions are
unchecked and whose alerts each UI must wire by hand.

## Decision

1. **CPOE (`packages/cpoe`)** — open formulary (WHO EML core + NAFDAC-style
   registry entries as DATA, never an embedded licensed compendium;
   deployers layer licensed data via `Formulary.load_extra`). Order sets for
   the highest-volume pathways (malaria, URI, hypertension, diabetes, ANC,
   PPH, asthma). Status machine draft→proposed→placed→verified→dispensed→
   completed. **Allergy = fail-CLOSED (raises AllergyConflict); interactions
   and dose-range = warn-and-override with mandatory recorded reason.**
   Prescriber must pass `hrcred.check_prescriber` before an order places.
2. **CDS Hooks (`services/cds-service`)** — HL7 CDS Hooks discovery +
   invocation; `order-select` (CPOE screen) and `patient-view` (immunization
   defaulter) providers run as subprocess calls (Python off hot path, 2s
   timeout). **Fail-soft: provider failure yields empty cards + honest
   warning; allergy findings surface as `critical` indicator cards so UIs can
   fail-closed.** All future rules engines become hook providers, so every
   UI gets every alert through one endpoint.
3. **Proxy consent (consent-service)** — signed, scoped, expiring, revocable
   caregiver grants (`ProxyGrant`), same audit discipline as Consent.
   `consent:manage` is refused — **delegation is one level**. Expiry and
   tampering fail closed. `ProxyService` interface leaves the Postgres seam.
4. **USSD self-service depth** — appointment booking, refill reminders,
   results-ready checks as injectable readers; hidden from the menu until a
   state activates them; every failure answers with an honest END message.

## Consequences

- Positive: prescriptions carry machine-checkable safety; alerts are
  centrally governed; children/elderly get lawful access paths; feature-phone
  citizens get real self-service.
- Limitations (honest): the interaction table is a curated subset, not a
  full compendium — deployments with a licensed database should layer it via
  `load_extra`. Proxy grants are in-memory until the Postgres seam lands.
  IVR voice menus reuse the same handlers but voice prompts in ha/yo/ig/pcm
  remain to be recorded.
