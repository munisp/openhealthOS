# TigerBeetle — ledger profile
- It IS the tuning: in-memory accounting, batched VSR consensus, no locks.
- Client: batch create_transfers up to 8189 ops per request; pipeline
  batches without waiting (the official client does this automatically).
- Data file on dedicated NVMe, direct IO, replication factor 3.
- Capacity headroom: keep storage < 50% so compaction never gates ingest.
- Observability: stats -> sidecar exporter (exporter/) -> collector.
