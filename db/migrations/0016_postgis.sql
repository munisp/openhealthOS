-- 0016_postgis.sql — real spatial types for the registry (ADR-0051).
--
-- Until now: bounding-box index + haversine refinement in Go. Fine at
-- tens of thousands of facilities; the national registry target is
-- hundreds of thousands of facilities plus millions of member/household
-- points, where full-scan haversine stops being a plan.
--
-- PostGIS is the designated spatial engine (OSI-clean: GPL-2.0 licence
-- on the EXTENSION, which runs server-side inside Postgres — no code is
-- linked into our binaries; the network-protocol usage doctrine is the
-- same as PostGIS's own widespread use with permissive stacks. Our
-- services remain stdlib Go; the SQL is the interface.)
--
-- Doctrine: geography (not geometry) — metres/kilometres natively,
-- correct on the sphere, Nigeria needs no projection choice.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

-- One geography column per spatial table, maintained BY THE DATABASE
-- (trigger), so writers keep inserting plain lat/lng doubles — no
-- service code changes, no dual-write drift.
ALTER TABLE facilities
    ADD COLUMN IF NOT EXISTS geom geography(Point, 4326);

CREATE OR REPLACE FUNCTION facilities_sync_geom() RETURNS trigger AS $$
BEGIN
    NEW.geom := CASE
        WHEN NEW.latitude IS NULL OR NEW.longitude IS NULL THEN NULL
        ELSE ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography
    END;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS facilities_geom_sync ON facilities;
CREATE TRIGGER facilities_geom_sync
    BEFORE INSERT OR UPDATE OF latitude, longitude ON facilities
    FOR EACH ROW EXECUTE FUNCTION facilities_sync_geom();

-- Backfill existing rows through the same function.
UPDATE facilities SET latitude = latitude WHERE latitude IS NOT NULL;

-- GiST spatial index: ST_DWithin / KNN (<->) become index-assisted.
CREATE INDEX IF NOT EXISTS facilities_geom_gist
    ON facilities USING GIST (geom) WHERE geom IS NOT NULL;

COMMIT;
