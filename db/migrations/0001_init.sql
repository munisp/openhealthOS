-- OpenHealth OS state-plane schema — migration 0001.
--
-- One schema per state deployment (ADR-0002: state data plane). Covers the
-- four services whose seams gain a Postgres implementation in this round:
-- registry (facilities/providers/patients), sync-ingest (event dedup),
-- consent (artefacts + audit), SHR (FHIR resources + access audit).
--
-- Idempotent by construction (IF NOT EXISTS) — safe to replay at boot.
-- Money never lives here: the ledger is the Rust double-entry service.

BEGIN;

-- ---------------------------------------------------------- registry
CREATE TABLE IF NOT EXISTS facilities (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    state      TEXT NOT NULL,
    lga        TEXT NOT NULL,
    ownership  TEXT NOT NULL DEFAULT 'public',
    level      TEXT NOT NULL DEFAULT 'PHC',
    services   JSONB NOT NULL DEFAULT '[]',
    bhcpf      BOOLEAN NOT NULL DEFAULT FALSE,
    power      TEXT NOT NULL DEFAULT 'none',
    water      TEXT NOT NULL DEFAULT 'none',
    latitude   DOUBLE PRECISION,
    longitude  DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS providers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    license    TEXT NOT NULL DEFAULT '',
    cadre      TEXT NOT NULL,
    facility   TEXT NOT NULL,
    verified   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS patients (
    id          TEXT PRIMARY KEY,              -- OH-<facility>-<seq>
    nin         TEXT,                          -- NIMC NIN, when linked
    name        TEXT NOT NULL,
    dob         TEXT NOT NULL DEFAULT '',      -- YYYY-MM-DD
    phone       TEXT NOT NULL DEFAULT '',
    facility_id TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- NIN uniqueness only when present (progressive enrolment: most records
-- start NIN-less at the edge and bind later).
CREATE UNIQUE INDEX IF NOT EXISTS patients_nin_unique
    ON patients (nin) WHERE nin IS NOT NULL;

-- Per-facility offline-id sequences: atomic increments give every edge
-- device a unique, locally-resolvable patient id with zero coordination.
CREATE TABLE IF NOT EXISTS facility_seq (
    facility_id TEXT PRIMARY KEY,
    next        BIGINT NOT NULL DEFAULT 0
);

-- -------------------------------------------------------- sync-ingest
-- Exactly-once ingest across restarts: the primary key IS the dedup.
CREATE TABLE IF NOT EXISTS sync_events (
    id          TEXT PRIMARY KEY,              -- edge-assigned event id
    facility    TEXT NOT NULL,
    type        TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    payload     JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sync_events_facility_ts ON sync_events (facility, ts);

-- ------------------------------------------------------------- consent
CREATE TABLE IF NOT EXISTS consents (
    id         TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    grantee    TEXT NOT NULL,
    scope      TEXT NOT NULL,
    purpose    TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,                  -- active | revoked
    issued_at  TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    signature  TEXT NOT NULL                   -- HMAC-SHA256 over canonical form
);
-- Artefact ids: CNS-%06d from a durable sequence — survives restarts.
CREATE SEQUENCE IF NOT EXISTS consent_id_seq;

-- One active consent per (patient, grantee, scope) — reissue replaces.
CREATE UNIQUE INDEX IF NOT EXISTS consents_active_unique
    ON consents (patient_id, grantee, scope) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS consent_audit (
    seq        BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    event      TEXT NOT NULL,                  -- ISSUE | CHECK | REVOKE
    consent_id TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------- shr
CREATE TABLE IF NOT EXISTS fhir_resources (
    resource_type   TEXT NOT NULL,
    id              TEXT NOT NULL,
    patient_id      TEXT NOT NULL,
    source_facility TEXT NOT NULL,
    body            JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (resource_type, id)
);
CREATE INDEX IF NOT EXISTS fhir_resources_patient ON fhir_resources (patient_id);

-- The access log is append-only: every read decision, with its basis.
-- A record whose access cannot be attributed is a breach; this table is
-- how attribution survives restarts.
CREATE TABLE IF NOT EXISTS shr_audit (
    seq        BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor      TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    resource   TEXT NOT NULL,
    decision   TEXT NOT NULL,                  -- allowed | denied | break-glass
    basis      TEXT NOT NULL                   -- same-facility | consent | emergency | no-consent
);

COMMIT;
