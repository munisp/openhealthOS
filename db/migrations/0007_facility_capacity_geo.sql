-- 0007_facility_capacity_geo.sql — facility capacity profile + geospatial
-- readiness (ADR-0014). Beds, clinical staffing and amenities are what the
-- map renders and what referral decisions route on.

BEGIN;

ALTER TABLE facilities ADD COLUMN IF NOT EXISTS beds      INT NOT NULL DEFAULT 0;
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS doctors   INT NOT NULL DEFAULT 0;
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS nurses    INT NOT NULL DEFAULT 0;
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS amenities JSONB NOT NULL DEFAULT '[]';
-- e.g. ["pharmacy","lab","maternity","xray","ambulance","oxygen","cold-chain"]
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS phone     TEXT NOT NULL DEFAULT '';
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS hours     TEXT NOT NULL DEFAULT '';

-- Geospatial lookup: bounding-box prefilter before haversine refinement.
CREATE INDEX IF NOT EXISTS facilities_geo_idx
    ON facilities (latitude, longitude) WHERE latitude IS NOT NULL;

COMMIT;
