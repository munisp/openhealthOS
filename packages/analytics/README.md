# Analytics (Python) — pilot gates, measured not asserted

Computes the §6.6 scale gates from the domain event stream (the same events
[`sync-ingest`](../../services/sync-ingest) lands):

| Gate | Function | Threshold |
|---|---|---|
| Offline clinical continuity | `offline_continuity` | ≥90% encounters captured with zero connectivity |
| Sync reliability | `sync_reliability` | ≥98% reconciled <15 min after connectivity |
| Claims turnaround | `claims_turnaround` | <14 days median (paper baseline: 6–24 months) |
| Stock-out visibility | `stockout_visibility` | ≥80% facility-days reporting |
| Staff weekly active use | `staff_weekly_active` | ≥70% of trained staff |

Every output states its window and pass/fail — dashboards quote reproducible
numbers, and the sequencing rule (no new state until <14-day turnaround holds
two consecutive quarters) is computed, not asserted.

```bash
python3 -m unittest discover -s packages/analytics
```
