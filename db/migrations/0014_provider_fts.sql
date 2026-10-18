-- 0014_provider_fts — practitioner full-text search (ADR-0017):
-- "find me a midwife near Zaria" is a directory question; the same
-- stored-tsvector + GIN answer as facilities/patients (0008).

BEGIN;

ALTER TABLE providers
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(name, '')     || ' ' ||
            coalesce(cadre, '')    || ' ' ||
            coalesce(license, '')  || ' ' ||
            coalesce(facility, '') || ' ' ||
            coalesce(id, ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS providers_fts_idx ON providers USING GIN (fts);

COMMIT;
