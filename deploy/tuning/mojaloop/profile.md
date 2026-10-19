# Mojaloop — database + throughput profile

## Database answer (verified 2026-10)
- Current Mojaloop (v17+): central-ledger is Postgres-native — "Postgres is
  currently the only supported database" (mojaloop/central-ledger README).
  Deploy current releases; nothing to port.
- Legacy (<= v16): MySQL for central-ledger/quoting/account-lookup. Do NOT
  fork to swap engines — upgrade. If pinned on legacy short-term: InnoDB
  buffer pool 70% RAM, innodb_flush_log_at_trx_commit=2 off the money path,
  binlog row format, connection pooling at the service (knex pool ~50).
- Direction of travel: the community is moving the ledger to TigerBeetle
  (row-locking in the RDBMS is the throughput ceiling). TigerBeetle is
  already in our stack — evaluate the Mojaloop TigerBeetle ledger track.

## Throughput
- Position-account cache in Redis; prepare/fulfil handlers horizontally
  scaled on Kafka partitions keyed by transfer id.
- ALS oracle behind APISIX with keepalive upstream pools.
