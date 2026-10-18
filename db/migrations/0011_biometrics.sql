-- 0011_biometrics — biometric identity assurance (ADR-0017): enrolled
-- finger templates, sealed at rest (sealx token), one row per
-- (patient, finger). Templates never leave this table unsealed; the
-- matcher opens them only inside the registry process.

CREATE TABLE IF NOT EXISTS registry_biometrics (
  patient_id TEXT NOT NULL,
  finger     TEXT NOT NULL,          -- e.g. 'right-index'
  sealed     TEXT NOT NULL,          -- sealx token (or PLAIN: dev sentinel)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (patient_id, finger)
);
CREATE INDEX IF NOT EXISTS registry_biometrics_patient
  ON registry_biometrics (patient_id);
