-- 0013_ipd — the inpatient department (ADR-0017): wards, beds with a
-- state machine (free|occupied|cleaning), admissions with embedded
-- transfer moves, durable id counters.

CREATE TABLE IF NOT EXISTS ipd_wards (
  id          TEXT PRIMARY KEY,
  facility_id TEXT NOT NULL,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'medical'
);
CREATE INDEX IF NOT EXISTS ipd_wards_facility ON ipd_wards (facility_id);

CREATE TABLE IF NOT EXISTS ipd_beds (
  id      TEXT PRIMARY KEY,
  ward_id TEXT NOT NULL REFERENCES ipd_wards(id),
  label   TEXT NOT NULL DEFAULT '',
  state   TEXT NOT NULL DEFAULT 'free'
          CHECK (state IN ('free','occupied','cleaning'))
);
CREATE INDEX IF NOT EXISTS ipd_beds_ward ON ipd_beds (ward_id);

CREATE TABLE IF NOT EXISTS ipd_admissions (
  id            TEXT PRIMARY KEY,
  patient_id    TEXT NOT NULL,
  facility_id   TEXT NOT NULL,
  ward_id       TEXT NOT NULL REFERENCES ipd_wards(id),
  bed_id        TEXT NOT NULL REFERENCES ipd_beds(id),
  reason        TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'admitted'
                CHECK (status IN ('admitted','discharged')),
  admitted_at   TIMESTAMPTZ NOT NULL,
  discharged_at TIMESTAMPTZ,
  summary       TEXT NOT NULL DEFAULT '',
  moves         JSONB NOT NULL DEFAULT '[]'
);
-- The one-open-admission invariant as a database guarantee.
CREATE UNIQUE INDEX IF NOT EXISTS ipd_one_open_admission
  ON ipd_admissions (patient_id) WHERE status = 'admitted';
CREATE INDEX IF NOT EXISTS ipd_admissions_board
  ON ipd_admissions (facility_id, status, admitted_at DESC);

CREATE TABLE IF NOT EXISTS ipd_seq (name TEXT PRIMARY KEY, value BIGINT NOT NULL);
