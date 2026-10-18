-- 0015_dispatch — emergency dispatch with integrated case management
-- and CRM (ADR-0019): incidents, contacts, interactions, cases, tasks,
-- durable id counters.

CREATE TABLE IF NOT EXISTS dispatch_incidents (
  id           TEXT PRIMARY KEY,
  channel      TEXT NOT NULL,
  caller_phone TEXT NOT NULL DEFAULT '',
  patient_id   TEXT NOT NULL DEFAULT '',
  lat          DOUBLE PRECISION NOT NULL DEFAULT 0,
  lng          DOUBLE PRECISION NOT NULL DEFAULT 0,
  loc_source   TEXT NOT NULL DEFAULT '',
  complaint    TEXT NOT NULL,
  triage       TEXT NOT NULL CHECK (triage IN ('red','orange','green')),
  triage_reason TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL,
  facility_id  TEXT NOT NULL DEFAULT '',
  facility_name TEXT NOT NULL DEFAULT '',
  facility_km  DOUBLE PRECISION NOT NULL DEFAULT 0,
  timeline     JSONB NOT NULL DEFAULT '[]',
  created_at   TIMESTAMPTZ NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS dispatch_incidents_status ON dispatch_incidents (status);
CREATE INDEX IF NOT EXISTS dispatch_incidents_caller ON dispatch_incidents (caller_phone, created_at);

CREATE TABLE IF NOT EXISTS dispatch_contacts (
  phone             TEXT PRIMARY KEY,
  name              TEXT NOT NULL DEFAULT '',
  patient_id        TEXT NOT NULL DEFAULT '',
  language          TEXT NOT NULL DEFAULT '',
  preferred_channel TEXT NOT NULL DEFAULT '',
  consent_follow_up BOOLEAN NOT NULL DEFAULT TRUE,
  tags              JSONB NOT NULL DEFAULT '[]',
  interactions      INT NOT NULL DEFAULT 0,
  first_contact_at  TIMESTAMPTZ NOT NULL,
  last_contact_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_interactions (
  id          TEXT PRIMARY KEY,
  phone       TEXT NOT NULL REFERENCES dispatch_contacts(phone),
  channel     TEXT NOT NULL DEFAULT '',
  direction   TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  kind        TEXT NOT NULL,
  summary     TEXT NOT NULL DEFAULT '',
  incident_id TEXT NOT NULL DEFAULT '',
  case_id     TEXT NOT NULL DEFAULT '',
  actor       TEXT NOT NULL DEFAULT '',
  ts          TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS dispatch_interactions_phone ON dispatch_interactions (phone, ts);

CREATE TABLE IF NOT EXISTS dispatch_cases (
  id            TEXT PRIMARY KEY,
  contact_phone TEXT NOT NULL REFERENCES dispatch_contacts(phone),
  patient_id    TEXT NOT NULL DEFAULT '',
  incident_id   TEXT NOT NULL DEFAULT '',
  title         TEXT NOT NULL,
  priority      TEXT NOT NULL CHECK (priority IN ('red','orange','green')),
  status        TEXT NOT NULL,
  owner         TEXT NOT NULL,
  facility_id   TEXT NOT NULL DEFAULT '',
  outcome       TEXT NOT NULL DEFAULT '',
  opened_at     TIMESTAMPTZ NOT NULL,
  updated_at    TIMESTAMPTZ NOT NULL,
  closed_at     TIMESTAMPTZ,
  timeline      JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS dispatch_cases_status ON dispatch_cases (status);
CREATE INDEX IF NOT EXISTS dispatch_cases_owner ON dispatch_cases (owner);
CREATE INDEX IF NOT EXISTS dispatch_cases_contact ON dispatch_cases (contact_phone);

CREATE TABLE IF NOT EXISTS dispatch_tasks (
  id       TEXT PRIMARY KEY,
  case_id  TEXT NOT NULL REFERENCES dispatch_cases(id),
  kind     TEXT NOT NULL,
  due_at   TIMESTAMPTZ NOT NULL,
  done_at  TIMESTAMPTZ,
  status   TEXT NOT NULL CHECK (status IN ('open','done','cancelled')),
  assignee TEXT NOT NULL DEFAULT '',
  note     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS dispatch_tasks_case ON dispatch_tasks (case_id);
CREATE INDEX IF NOT EXISTS dispatch_tasks_open_due ON dispatch_tasks (due_at) WHERE status = 'open';

-- durable counters for INC / CASE / TASK / INT ids
CREATE TABLE IF NOT EXISTS dispatch_seq (
  name  TEXT PRIMARY KEY,
  value INT NOT NULL
);
