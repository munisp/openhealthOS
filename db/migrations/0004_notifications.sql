-- Migration 0004 — notification rail (subscriptions + outbound messages).
-- Idempotent; applied by the k8s migration job and CI before integration tests.

-- Patient contact consent: which number, which language. Captured at
-- registration; the service never guesses numbers.
CREATE TABLE IF NOT EXISTS notification_subscriptions (
    patient_id  TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'en',
    facility_id TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES notification_subscriptions(patient_id),
    phone       TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- appointment | lab_ready | pickup | referral | immunization
    text        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','sent','failed')),
    attempts    INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);

CREATE INDEX IF NOT EXISTS notifications_patient_idx ON notifications (patient_id, created_at DESC);
-- the drain loop's working set
CREATE INDEX IF NOT EXISTS notifications_pending_idx ON notifications (created_at) WHERE state = 'queued';
