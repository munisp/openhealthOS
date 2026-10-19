# ADR-0032: Provider economics rail — optimizing capitation and FFS with analytics

Status: accepted (implemented)
Deciders: platform architecture

## Context

NHIA's two payment mechanisms (ADR-0031) fail providers in predictable
ways: under capitation, facilities cannot see their cost per risk band;
under fee-for-service, they cannot say what a procedure actually costs
them, and revenue dies in preventable denials. Payers, meanwhile, price
blind — capitation is flat because the utilization data for risk
adjustment has never existed. The platform's rails (ledger, encounters,
IPD, theatre, claims) already capture the inputs; this ADR adds the
analytics layer.

## Decision

`packages/economics` (stdlib-only Python), surfaced through
claims-orchestrator as `POST /economics/{op}` via the subprocess seam
(same doctrine as the adjudicator and benefits seams). Four ops:

### margins — true cost per service code vs the FFS tariff

Encounter inputs (staff minutes × per-minute cost, consumables,
length-of-stay × daily overhead allocation) are summed into activity-
based cost per encounter, then aggregated per service code against the
benefits package's tariff schedule. Output: a margin-per-code table,
worst margin first, with codes missing from the tariff schedule
explicitly flagged. Every report carries `tariff_year` — a margin
computed against a stale schedule says so on its face.

### panel — capitation P&L per risk band

Enrollees are risk-banded (under5 / adult / older-adult / elderly /
maternal / chronic); capitation revenue per band is compared against the
cost of care actually delivered to that band. Encounters attributed to
nobody on the panel land in **walk-in** — care consumed without
capitation, the panel-attribution leak made visible as a naira figure.

### denials — denial-reason and per-HMO analytics

Denials are classified (missing-referral, wrong-tariff-vintage,
documentation-gap, panel-mismatch → *preventable*; not-covered →
*package*; duplicate-claim → *fraud-screen*) and rated per HMO. The
preventable share is the revenue-cycle target; the fix lives upstream —
docintel (ADR-0028) validates claim documentation before submission.

### tariff-sim — simulate before publishing

Extends the ADR-0031 staleness math: given a utilization mix and a CPI
path, compute the expected tariff per code, then the effect of any
uplift on provider revenue, pool outflow, and enrollee co-pay — before a
schedule is published, not a decade after it goes stale.

## Boundaries (doctrine)

- **AI optimizes workflow and pricing, never care.** Undercoding
  recovery is legitimate revenue integrity; upcoding belongs to fraudml
  (which screens against it). No output of this rail may suppress care:
  utilization models are constrained by SOP-versioned clinical pathways.
- Money figures are kobo; every report is reproducible (input counts +
  tariff vintage included).
- The rail reads the same tariff schedule the adjudicator and the
  patient-facing catalogue use — one price list, three audiences, zero
  divergence.

## Consequences

- A facility can finally answer "which service lines subsidize which"
  and "which denials are self-inflicted".
- The payer gains the raw material for risk-adjusted capitation and
  evidence-based tariff revisions.
- Known limit: overhead allocation is a per-day rate in this pilot;
  theatre-minute costing arrives when the theatre rail exports its
  resource log in this shape.
