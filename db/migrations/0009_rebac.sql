-- 0009_rebac.sql — relationship-based authorization tuples (ADR-0015)
--
-- The Permify row: facility↔staff↔patient grants as first-class data.
-- One tuple table, idempotent by construction — safe to replay at boot.
-- Semantics live in shared/rebac; this is only the durable shape.
--
-- Relations in use today:
--   user → facility : staff | admin
--   user → patient  : guardian | care-team | referral
-- Derived rule (shared/rebac.Checker): staff/admin of a patient's
-- registering facility can access that patient; direct patient tuples
-- extend access beyond the facility (guardians, cross-facility referral).

BEGIN;

CREATE TABLE IF NOT EXISTS rebac_tuples (
    subject_type TEXT NOT NULL,          -- user | facility | service
    subject_id   TEXT NOT NULL,
    relation     TEXT NOT NULL,          -- staff | admin | guardian | care-team | referral
    object_type  TEXT NOT NULL,          -- facility | patient
    object_id    TEXT NOT NULL,
    granted_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,            -- NULL = standing grant
    PRIMARY KEY (subject_type, subject_id, relation, object_type, object_id)
);

-- Reverse lookup: who can touch this object?
CREATE INDEX IF NOT EXISTS rebac_tuples_object_idx
    ON rebac_tuples (object_type, object_id, relation);

COMMIT;
