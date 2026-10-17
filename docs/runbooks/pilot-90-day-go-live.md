# Runbook: 90-day facility go-live playbook

Rollout runs cluster-by-cluster — one LGA at a time. This playbook is the per-cluster sequence. Paper-parallel is time-boxed to four weeks with an explicit exit; workflows must be strictly faster than paper before paper is retired.

## Phase 0 — Cluster preparation (days 1–14)

1. **Facility registry clean-up**: reconcile the LGA's facility list against the facility registry (`services/registry-service`); verify ownership, services, GPS, power/water/equipment profile.
2. **Site survey**: solar exposure, roof/ground mount, water storage, cold-chain units, connectivity survey (2G/3G/4G signal; Starlink/VSAT only for high-value sites).
3. **Staff roster & training plan**: identify nurses/CHO/CHWs per facility; schedule 2-day training.

## Phase 1 — Install (days 15–30)

1. **Edge install + solar check**: edge node ($1,200 kit), 300Wh LFP battery, charge controller; verify 48h autonomy target; register node in fleet management with signed image.
2. **Telemetry hookup**: power state, water-tank level, cold-chain temperature probes streaming to `services/telemetry-collector`; verify alerts fire (test a cold-chain excursion).
3. **Client setup**: PWAs on staff Android devices; offline mode verified with radios off.
4. **Connectivity**: sync agent pointed at the state plane; confirm store-and-forward queue drains within 15 minutes of connectivity.

## Phase 2 — Paper-parallel (days 31–60, max 4 weeks)

1. All encounters recorded in the EMR **and** on paper; daily reconciliation count.
2. Staff-time measurement: task flows must be strictly faster than paper before proceeding (failure-mode counter-measure 3).
3. Weekly stock report via the app; tracer-SKU reporting ≥80% target tracked from day 1.
4. Eligibility checks via USSD validated against the state scheme.

## Phase 3 — Claims live (days 61–90)

1. Paper retired (exit ceremony is deliberate — morale matters).
2. First claims submitted through `services/claims-orchestrator`; adjudication via `packages/adjudicator`; settlement on the ledger seam.
3. Median claims turnaround tracked against the <14-day gate.
4. Go/no-go review against pilot gates (§6.6 of the business spec): offline continuity ≥90%, sync ≥98% <15min, turnaround <14 days, stock-out visibility ≥80%, staff WAU ≥70%, NPS ≥+30.

## Failure handling

- **Edge node down**: facility falls back to device-local capture; sync agent re-replicates on recovery. No data loss is acceptable — the ACK cursor contract in `services/sync-agent` is the guarantee.
- **Cold-chain excursion alert**: SMS to facility manager + state dashboard; quarantine stock per cold-chain SOP; the telemetry event is the auditable record for funders.
- **Claims backlog**: adjudication queue drains in arrival order on reconnect; duplicates are flagged, never silently dropped.
