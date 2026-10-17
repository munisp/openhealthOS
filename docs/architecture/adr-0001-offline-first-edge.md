# ADR-0001: Offline-first edge architecture

- Status: Accepted
- Date: 2026-10-17
- Spec reference: §5.3

## Context

Nigerian PHCs face ~140 national grid collapses per decade, patchy 2G/3G coverage, expensive data, and facilities averaging ~6.6h/day of grid power. Field literature identifies power and network failure as the top technological barrier that kills digital-health deployments. Any architecture that treats connectivity as a given dies in the field.

## Decision

Every clinical, enrolment and claims workflow is fully functional with zero connectivity:

1. **Edge node per PHC cluster** — mini-PC (4-core, 16GB, 1TB NVMe, ~15W avg) hosting the facility data plane: FHIR store replica, terminology cache, sync agent, local Wi-Fi/LAN. Capex ≤$1,200.
2. **Solar + 300Wh LFP battery**, 48h autonomy; charge-controller telemetry is first-class platform data.
3. **Local-first clients** — PWAs on any Android with CRDT-based sync: last-writer-wins with semantic merge for clinical facts, plus a clinician conflict UI.
4. **Store-and-forward sync** over HTTPS when connectivity appears; payloads compressed to <1MB per facility-day; SMS/USSD fallback for enrolment, eligibility and alerts.
5. **Resilience target**: 30+ days fully offline; edge self-heals and re-replicates after outages.

## Consequences

- Sync is an optimisation, never a dependency. Clinical workflow latency is a local property (<200ms).
- The sync agent (`services/sync-agent`) is a small, heavily-tested component with an ACK-cursor contract — it must never lose or duplicate a facility-day.
- Claims and eligibility degrade to offline queues; adjudication catches up on reconnect (see ADR-0003).
- Complexity cost: CRDT merge semantics and conflict UX are permanent engineering surface. Accepted — the alternative is product death in the field.
