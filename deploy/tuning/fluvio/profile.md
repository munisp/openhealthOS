# Fluvio — streaming profile
- Partition hot topics by tenant/shard key; consumer SPU affinity per partition.
- SmartModules do filtering in the stream, not in the consumer.
- Producer: linger 5ms, compression lz4, batch 64KB.
- Observability: boundary instrumentation (docs/observability/coverage-matrix.md).
