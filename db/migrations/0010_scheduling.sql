-- 0010_scheduling — the appointment engine (ADR-0017): weekly schedule
-- templates, materialised slots with atomic booking counters,
-- appointments, FIFO waitlists, durable ID counters.

CREATE TABLE IF NOT EXISTS sched_schedules (
  id            TEXT PRIMARY KEY,
  facility_id   TEXT NOT NULL,
  resource_id   TEXT NOT NULL,
  resource_name TEXT NOT NULL,
  resource_type TEXT NOT NULL CHECK (resource_type IN ('provider','room','equipment')),
  service       TEXT NOT NULL,
  weekday       INT  NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_min     INT  NOT NULL,
  end_min       INT  NOT NULL,
  slot_minutes  INT  NOT NULL CHECK (slot_minutes > 0),
  capacity      INT  NOT NULL CHECK (capacity > 0),
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK (start_min < end_min)
);
CREATE INDEX IF NOT EXISTS sched_schedules_facility ON sched_schedules (facility_id) WHERE active;

-- Slot IDs are deterministic (schedule:date:start_min) so generation is
-- idempotent across edge retries.
CREATE TABLE IF NOT EXISTS sched_slots (
  id            TEXT PRIMARY KEY,
  schedule_id   TEXT NOT NULL REFERENCES sched_schedules(id),
  facility_id   TEXT NOT NULL,
  service       TEXT NOT NULL,
  resource_name TEXT NOT NULL,
  start_ts      TIMESTAMPTZ NOT NULL,
  end_ts        TIMESTAMPTZ NOT NULL,
  capacity      INT NOT NULL,
  booked        INT NOT NULL DEFAULT 0 CHECK (booked >= 0)
);
CREATE INDEX IF NOT EXISTS sched_slots_browse ON sched_slots (facility_id, service, start_ts);

CREATE TABLE IF NOT EXISTS sched_appointments (
  id            TEXT PRIMARY KEY,
  slot_id       TEXT NOT NULL REFERENCES sched_slots(id),
  patient_id    TEXT NOT NULL,
  facility_id   TEXT NOT NULL,
  service       TEXT NOT NULL,
  resource_name TEXT NOT NULL,
  start_ts      TIMESTAMPTZ NOT NULL,
  end_ts        TIMESTAMPTZ NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('booked','checked-in','completed','cancelled','no-show')),
  reason        TEXT NOT NULL DEFAULT '',
  coverage      JSONB,                 -- advisory snapshot, never a gate
  created_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sched_appts_patient  ON sched_appointments (patient_id, start_ts);
CREATE INDEX IF NOT EXISTS sched_appts_dayboard ON sched_appointments (facility_id, start_ts) WHERE status <> 'cancelled';

-- One queue entry per (patient, facility, service) — re-joining is a
-- no-op, promotion is FIFO by created_at.
CREATE TABLE IF NOT EXISTS sched_waitlist (
  id          TEXT PRIMARY KEY,
  patient_id  TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  service     TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL,
  UNIQUE (patient_id, facility_id, service)
);
CREATE INDEX IF NOT EXISTS sched_waitlist_queue ON sched_waitlist (facility_id, service, created_at);

CREATE TABLE IF NOT EXISTS sched_seq (
  name  TEXT PRIMARY KEY,
  value INT NOT NULL
);
