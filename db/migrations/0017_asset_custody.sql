BEGIN;

-- --------------------------------------------------------- asset custody
-- ADR-0052: signed tag scans persist here — every barcode/QR scan of a
-- hospital asset appends an immutable custody event. equipment.location
-- mirrors the latest scan for fast lookup; custody_events is the spine.

ALTER TABLE equipment ADD COLUMN IF NOT EXISTS location TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS custody_events (
    id         BIGSERIAL PRIMARY KEY,
    asset_id   TEXT NOT NULL REFERENCES equipment(id),
    facility   TEXT NOT NULL,
    action     TEXT NOT NULL,                -- locate | transfer | maintenance_flag | check_in
    actor      TEXT NOT NULL,
    location   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    stale_tag  BOOLEAN NOT NULL DEFAULT FALSE,
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS custody_asset ON custody_events (asset_id, scanned_at);

COMMIT;
