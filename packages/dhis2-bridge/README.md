# DHIS2 bridge (Python)

Facility-level events roll up to Nigeria's national HMIS so reporting
continues unbroken and facilities never dual-enter (ADR-0002).

```
domain events (JSON lines, stdin) → aggregate per facility/period → DHIS2 dataValueSet (stdout)
```

- Indicator mapping (`DATA_ELEMENTS`) binds domain event types to DHIS2 data
  elements; production binds the state's DHIS2 metadata.
- Periods are DHIS2 monthly (`YYYYMM`).
- Unknown event types pass through silently — the bridge never invents data.

## Test

```bash
python3 -m unittest discover -s packages/dhis2-bridge
```
