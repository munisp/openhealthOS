-- 0018: canonical facility registry (ADR-0057)
CREATE TABLE IF NOT EXISTS facilities (
    facility_id   TEXT PRIMARY KEY,          -- OHF-<state>-<lga>-<seq>
    name          TEXT NOT NULL,
    state         TEXT NOT NULL,
    lga           TEXT NOT NULL,
    type          TEXT NOT NULL,
    dhis2_uid     TEXT UNIQUE,
    hmis_code     TEXT,
    nhia_provider_code TEXT,
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS facility_duplicate_flags (
    id            BIGSERIAL PRIMARY KEY,
    candidate_name TEXT NOT NULL,
    existing_id   TEXT NOT NULL REFERENCES facilities(facility_id),
    flagged_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_facilities_state_lga ON facilities(state, lga);
