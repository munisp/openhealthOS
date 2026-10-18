-- 0008_fts.sql — full-text search (ADR-0015). The OpenSearch row answer:
-- instead of embedding an Elastic-licensed search cluster for two lookup
-- endpoints, Postgres does the searching it is already excellent at.
-- 'simple' config: no English stemming mangling Nigerian names and
-- transliterations; prefix tokens (term:*) give type-ahead behaviour.

BEGIN;

ALTER TABLE facilities
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(name, '')  || ' ' ||
            coalesce(state, '') || ' ' ||
            coalesce(lga, '')   || ' ' ||
            coalesce(id, ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS facilities_fts_idx ON facilities USING GIN (fts);

ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(name, '')  || ' ' ||
            coalesce(phone, '') || ' ' ||
            coalesce(nin, '')   || ' ' ||
            coalesce(id, ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS patients_fts_idx ON patients USING GIN (fts);

COMMIT;
