# Business specification

*Condensed from Specification v1.0 §6–9. Companion financial model: OpenHealth_OS_Financial_Model.xlsx (verified, 271 live formulas).*

## Revenue architecture

The core OS is free and open source forever. Revenue attaches to the economic activity the OS enables — claims, payments, procurement — plus paid convenience and intelligence on top.

| Stream | Pricing (Base) | Year-5 revenue |
|---|---|---|
| SaaS — PHCs | $35/facility/mo | $716k |
| SaaS — hospitals/clinics | $250/facility/mo | $3,171k |
| Premium modules (telehealth, AI triage) | $60/facility/mo · 35% attach | $696k |
| Claims processing fees | 2.5% of claim value | $842k |
| Payments take rate | 1.0% of digital collections | $331k |
| Marketplace commission | 3.0% of procurement | $2,195k |
| State analytics licenses | $40k/state/yr | $320k |
| Training & implementation | $150/new facility | $97k |
| **Total** | | **$8,368k = 0.053% of Nigeria's total health spend** |

## Go-to-market

- **Beachhead**: the 8,309 BHCPF-supported PHCs — the payer (state) is already contracted to them. Layer in faith-based/private clinics for cross-subsidising SaaS revenue.
- **Rollout**: cluster-by-cluster (one LGA at a time) behind a 90-day go-live playbook — see [runbooks/pilot-90-day-go-live.md](runbooks/pilot-90-day-go-live.md).
- **Candidate pilot states**: Kaduna, Ekiti, Ondo, Lagos (final selection with NPHCDA and the SHIA).
- **Partners**: NPHCDA/FMOH, NHIA/state SHIAs, NIMC, Nigerian PSPs, the open-source commons (OpenMRS, OpenELIS, DHIS2, OpenHIE), one teaching hospital as clinical governance anchor.

## Packaging

| Tier | Contents | Price |
|---|---|---|
| Community | Full core OS, self-hosted, AGPL | Free, forever |
| Professional | Managed hosting, SLAs, premium modules, reporting pack | $35 (PHC) / $250 (hospital) /mo |
| State | Claims rails, eligibility, fraud flags, analytics, DHIS2 bridge, dedicated data plane | $40k/yr + claims fees |
| Donor kit | Edge node + solar + deployment + training, branded and monitored | $1,200 + $350 per PHC (co-financed) |

## Pilot design & success gates (measured, not asserted)

Phase 1: 50 facilities for six months (30 BHCPF PHCs, 10 faith-based/private, 10 hospitals), then 200 in the same state.

| Gate | Threshold |
|---|---|
| Clinical continuity on offline days | ≥90% of encounters recorded with zero connectivity |
| Sync reliability | ≥98% of facility-days reconciled <15 min after connectivity |
| Claims turnaround | <14 days median (from 6–24 months paper baseline) |
| Stock-out visibility | ≥80% of tracer SKUs reported daily |
| Staff weekly active use | ≥70% of trained staff |
| Patient experience | NPS ≥ +30 |

**Sequencing rule**: no state onboards until the previous state's claims turnaround is <14 days for two consecutive quarters. The money rails are the product; facility software is the Trojan horse.

## Financial sustainability (Base case, verified model)

| $000 | 2027 | 2028 | 2029 | 2030 | 2031 |
|---|---|---|---|---|---|
| Revenue | 983 | 2,196 | 4,130 | 6,175 | 8,368 |
| EBITDA | (305) | 329 | 1,267 | 2,578 | 4,004 |
| Facilities live | 381 | 829 | 1,473 | 2,117 | 2,761 |

EBITDA breaks even in 2028; Bear case (0.55× adoption, 0.85× price) still clears costs at +$1.2M in Year 5. Total catalytic capital $10.5M ($4.5M seed to break-even + $6M Series A 2029). Unit economics (Year-5): $3,031 blended revenue/facility/yr, ~$740 CAC, ~3.7-month payback, ~9.6× LTV/CAC (design ceiling, not forecast).

## Impact framework (end-2031, Base)

2,761 facilities live · 8 states · 60.5M encounters digitalized · 5.0M members on claims rails · $33.7M claims value processed in 2031 · 16,566 health staff trained · 100% of installed base infrastructure-monitored. Aligned to SDG 3.8 (UHC), the AU digital health strategy, and Nigeria's 44M-insured-by-2030 target.
