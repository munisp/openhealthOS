-- OpenHealth OS state-plane schema — migration 0002.
-- Telehealth (consults, referrals, prescriptions) and equipment
-- (assets, maintenance tickets, BMET technicians). Idempotent.

BEGIN;

-- --------------------------------------------------------- telehealth
CREATE SEQUENCE IF NOT EXISTS consult_id_seq;
CREATE SEQUENCE IF NOT EXISTS referral_id_seq;
CREATE SEQUENCE IF NOT EXISTS prescription_id_seq;

CREATE TABLE IF NOT EXISTS consults (
    id          TEXT PRIMARY KEY,              -- CONS-%04d from sequence
    patient_id  TEXT NOT NULL,
    facility_id TEXT NOT NULL,
    nurse_id    TEXT NOT NULL,
    complaint   TEXT NOT NULL,
    protocol    TEXT NOT NULL DEFAULT '',
    assessment  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,                 -- open | assessed | escalated | closed
    outcome     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS referrals (
    id            TEXT PRIMARY KEY,            -- REF-%04d
    consult_id    TEXT NOT NULL REFERENCES consults(id),
    from_facility TEXT NOT NULL,
    to_facility   TEXT NOT NULL,
    urgency       TEXT NOT NULL,               -- routine | urgent | emergency
    reason        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,               -- open | accepted | completed
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS referrals_consult ON referrals (consult_id);

CREATE TABLE IF NOT EXISTS prescriptions (
    id          TEXT PRIMARY KEY,              -- RX-%04d
    consult_id  TEXT NOT NULL REFERENCES consults(id),
    prescriber  TEXT NOT NULL,
    medication  TEXT NOT NULL,
    dose        TEXT NOT NULL DEFAULT '',
    quantity    INT NOT NULL CHECK (quantity > 0),
    pharmacy_id TEXT NOT NULL,
    status      TEXT NOT NULL                  -- routed | dispensed
);
CREATE INDEX IF NOT EXISTS prescriptions_consult ON prescriptions (consult_id);

-- --------------------------------------------------------- equipment
CREATE SEQUENCE IF NOT EXISTS equipment_id_seq;

CREATE TABLE IF NOT EXISTS equipment (
    id        TEXT PRIMARY KEY,
    facility  TEXT NOT NULL,
    type      TEXT NOT NULL,
    model     TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL,                   -- working | down | in-maintenance | condemned
    donated   BOOLEAN NOT NULL DEFAULT FALSE,
    intake_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS technicians (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    vetted  BOOLEAN NOT NULL DEFAULT FALSE,    -- marketplace gate
    regions JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    issue        TEXT NOT NULL,
    priority     TEXT NOT NULL,                -- P1 | P2 | P3
    status       TEXT NOT NULL,                -- open | assigned | resolved
    technician   TEXT NOT NULL DEFAULT '',
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tickets_equipment ON tickets (equipment_id);

COMMIT;
