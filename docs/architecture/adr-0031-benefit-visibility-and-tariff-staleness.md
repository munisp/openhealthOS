# ADR-0031: Benefit packages as data — "what does my cover include?"

Status: accepted (implemented)
Deciders: platform architecture

## Context

In Nigeria the benefit package exists on paper: NHIA publishes the
package, treatment guidelines, a drug list, and a fee-for-service price
list — but an enrollee at a PHC counter cannot see it. The result is
the most common enrollee grievance: "your insurance doesn't cover that"
(or an improper charge) discovered only at the point of care, with
redress depending on NHIA staff physically visiting facilities.

Meanwhile cost-of-care is administered pricing: capitation per enrollee
(₦1,450/yr after the April-2025 actuarial review, covering 90% with a
10% co-pay) plus a published FFS tariff schedule (raised 373–378% in
the same review). Tariffs had gone unrevised for a decade — a policy
failure that was invisible until providers started refusing enrollees.

## Decision

**The benefit package is data, served from the same seam that enforces
it.** `packages/benefits` (stdlib Python) is the canonical
scheme → service-code catalogue: covered/excluded, co-pay %, referral
requirement, tariff in kobo, and the tariff's vintage year. A test
enforces that the adjudicator's coverage fixture and the patient-facing
catalogue never disagree — *what the patient sees is what gets paid.*

Three query shapes, one CLI contract (JSON in/out, mirroring
adjudicate.py):

- `package` / `explain` — the full catalogue and a plain-language
  summary ("14 covered services; co-pay 10% at point of care"),
  grouped by category, with the referral rule stated.
- `check` — the moment-of-care question: is THIS service covered under
  THIS scheme, what is the tariff, what does the patient pay.
- `staleness` — recompute any tariff year against a CPI path and report
  the funding gap per service and on capitation. A frozen schedule
  becomes a computable alarm, not a decade of quiet under-funding.

## Surfaces

- **claims-orchestrator** `GET /benefits/{scheme}` (+ `?code=`,
  `?staleness_year=&cpi_path=`) via a subprocess seam identical in
  doctrine to the adjudicator.
- **USSD** menu option 5 — member ID → scheme resolved through the
  eligibility seam → plain-language summary on a feature phone. Hidden
  when unbound; a dead benefits service reads "unavailable", never a
  wrong answer.
- **PWA** reads the same endpoint (the "my benefits" page is the
  explain payload rendered).
- **Wallets** (ADR-0030) make coverage tangible: the spend allow-list
  *is* the benefit package enforced at the wallet.

## Seed data and honesty

The shipped catalogue is a pilot subset of the NHIA package priced at
the April-2025 FFS schedule (e.g. hernia repair ₦35,000, caesarean
₦75,000, FBC ₦2,600, chest X-ray ₦1,800), `tariff_year = 2025`, with
capitation ₦1,450. States upload their full negotiated schedules as
versioned bundle JSON (same shape as ADR-0015 tariff bundles); every
answer stamps its vintage so a stale schedule is always visible.

## Consequences

- Coverage questions have one answer across USSD, PWA, clerk screen,
  and the claims engine — divergence is a test failure, not a patient
  complaint.
- The next actuarial review gets utilization evidence (the claims rail)
  and a staleness report instead of anecdotes.
- Known limit: the catalogue is in-file pilot data until state bundles
  ship; the loader contract already exists.
