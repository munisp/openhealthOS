-- 0012_passkeys — patient-portal passkey credentials (ADR-0017): one
-- row per WebAuthn credential bound to a patient. Public key material
-- only — private keys never leave the authenticator by design.

CREATE TABLE IF NOT EXISTS portal_passkeys (
  cred_id     TEXT PRIMARY KEY,        -- base64url credential id
  patient_id  TEXT NOT NULL,
  pub_x       TEXT NOT NULL,           -- base64url P-256 X coordinate
  pub_y       TEXT NOT NULL,
  sign_count  INT NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS portal_passkeys_patient
  ON portal_passkeys (patient_id);
