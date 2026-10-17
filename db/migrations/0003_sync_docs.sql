-- 0003_sync_docs.sql — CRDT-replicated entity documents (state plane).
-- One row per replicated doc; body is the full CRDT doc (fields carry
-- stamps + tombstones), merged field-wise on arrival. Idempotent.

CREATE TABLE IF NOT EXISTS sync_docs (
    id      TEXT PRIMARY KEY,             -- entity id from the edge
    type    TEXT NOT NULL,                -- Patient | Encounter | Task | ...
    body    JSONB NOT NULL,               -- crdt.Doc JSON (fields + stamps)
    updated TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sync_docs_type_idx ON sync_docs (type);
