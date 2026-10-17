-- 0005_registry_kyc.sql — identity assurance ladder (ADR-0012)
--
-- Adds KYC level tracking to patients and the phone-binding OTP challenge
-- table. Idempotent by construction — safe to replay at boot.

BEGIN;

ALTER TABLE patients ADD COLUMN IF NOT EXISTS kyc_level TEXT NOT NULL DEFAULT 'self';
ALTER TABLE patients ADD COLUMN IF NOT EXISTS kyc_detail TEXT NOT NULL DEFAULT '';

DO $$
BEGIN
    ALTER TABLE patients
        ADD CONSTRAINT patients_kyc_level_check
        CHECK (kyc_level IN ('self', 'phone', 'nin-format', 'nin-verified'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Phone-binding challenges: one outstanding OTP per patient, hash-only
-- (the plain code never rests), attempt counter persisted so a crash
-- cannot reset the lockout.
CREATE TABLE IF NOT EXISTS patient_otp (
    patient_id TEXT PRIMARY KEY REFERENCES patients (id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    attempts   INT NOT NULL DEFAULT 0
);

COMMIT;
