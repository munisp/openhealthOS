# Lakehouse — batch profile (packages/lakehouse)
- Bronze->silver->gold as partitioned Parquet on object storage (MinIO);
  compaction jobs coalesce small files nightly (small files are the #1
  lakehouse throughput killer).
- Query side: predicate pushdown + projection pruning; gold KPI plane
  pre-aggregated per tenant (tenant_id partition column).
- Geospatial: Sedona spatial partitions (KDB-tree) sized ~1-4MB per cell;
  GeoLibre evaluated as the serving layer (licence doctrine applies).
- Observability: every pipeline job emits otelx spans with the tenant label.
