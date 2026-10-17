-- Migration 0003 — pharmacy module (medicines, stock, dispensing, claim drafts).
-- Idempotent; applied by the k8s migration job and CI before integration tests.

CREATE SEQUENCE IF NOT EXISTS pharmacy_seq;

-- Formulary lives in code (services/pharmacy-service Formulary()) so the
-- edge and the state plane share one catalogue; this table is the audit
-- copy a state may extend locally.
CREATE TABLE IF NOT EXISTS formulary (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL,
    unit_price_ngn NUMERIC(12,2) NOT NULL CHECK (unit_price_ngn >= 0),
    essential     BOOLEAN NOT NULL DEFAULT TRUE,
    allergy_class TEXT NOT NULL DEFAULT '',
    therapy_class TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pharmacy_stock (
    facility_id   TEXT NOT NULL,
    drug_code     TEXT NOT NULL,
    quantity      INT NOT NULL CHECK (quantity >= 0),
    reorder_level INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, drug_code)
);

CREATE TABLE IF NOT EXISTS dispense_requests (
    id              TEXT PRIMARY KEY,
    prescription_id TEXT NOT NULL,
    patient_id      TEXT NOT NULL,
    facility_id     TEXT NOT NULL,
    drug_code       TEXT NOT NULL,
    dose            TEXT NOT NULL DEFAULT '',
    quantity        INT NOT NULL CHECK (quantity > 0),
    prescriber      TEXT NOT NULL DEFAULT '',
    allergies       TEXT[] ,
    status          TEXT NOT NULL CHECK (status IN ('pending','dispensed','rejected')),
    reject_reason   TEXT,
    created_at      TEXT NOT NULL,
    dispensed_at    TEXT,
    dispensed_by    TEXT
);
-- One dispense request per routed prescription, ever.
CREATE UNIQUE INDEX IF NOT EXISTS dispense_requests_rx_unique
    ON dispense_requests (prescription_id);
CREATE INDEX IF NOT EXISTS dispense_requests_facility_idx
    ON dispense_requests (facility_id, status);

CREATE TABLE IF NOT EXISTS claim_drafts (
    id            TEXT PRIMARY KEY,
    dispense_id   TEXT NOT NULL REFERENCES dispense_requests (id),
    facility_id   TEXT NOT NULL,
    patient_id    TEXT NOT NULL,
    item_code     TEXT NOT NULL,
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price_ngn NUMERIC(12,2) NOT NULL,
    total_ngn     NUMERIC(12,2) NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('drafted','submitted')),
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claim_drafts_facility_idx
    ON claim_drafts (facility_id, status);
