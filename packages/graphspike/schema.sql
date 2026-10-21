-- schema.sql — AGE spike: graph DDL + the three killer queries.
-- Runs against stock PostgreSQL 15+ with the Apache AGE extension.
-- These are the REAL queries adoption depends on; spike.py pins the
-- expected outputs as goldens, and run_age.sh --verify diffs them.

CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT create_graph('oh_national');

-- nodes ---------------------------------------------------------------------
SELECT create_vlabel('oh_national', 'Facility');
SELECT create_vlabel('oh_national', 'Prescriber');
SELECT create_vlabel('oh_national', 'Patient');
SELECT create_vlabel('oh_national', 'Encounter');
SELECT create_vlabel('oh_national', 'Claim');
SELECT create_vlabel('oh_national', 'Account');

-- edges ---------------------------------------------------------------------
SELECT create_elabel('oh_national', 'WORKS_AT');
SELECT create_elabel('oh_national', 'REFERRED_TO');
SELECT create_elabel('oh_national', 'ATTENDED');
SELECT create_elabel('oh_national', 'HELD_AT');
SELECT create_elabel('oh_national', 'FILED_BY');
SELECT create_elabel('oh_national', 'SIGNED_BY');
SELECT create_elabel('oh_national', 'PAID_TO');

-- ===========================================================================
-- KILLER QUERY 1 — fraud-ring signature.
-- Facilities with MANY claims, at most ONE signing prescriber, paying into
-- at most TWO accounts. Returns the facility, its prescribers, its accounts.
-- (Thresholds mirror spike.py RING_* constants.)
-- ===========================================================================
SELECT facility, signers, accounts FROM cypher('oh_national', $$
  MATCH (f:Facility)<-[:FILED_BY]-(c:Claim)
  WITH f, count(c) AS nclaims
  WHERE nclaims >= 15
  MATCH (f)<-[:FILED_BY]-(c2:Claim)-[:SIGNED_BY]->(p:Prescriber)
  WITH f, nclaims, collect(DISTINCT p.id) AS signers
  MATCH (f)<-[:FILED_BY]-(c3:Claim)-[:PAID_TO]->(a:Account)
  WITH f, nclaims, signers, collect(DISTINCT a.id) AS accounts
  WHERE size(signers) <= 1 AND size(accounts) <= 2
  RETURN f.id AS facility, signers, accounts
$$) AS (facility agtype, signers agtype, accounts agtype);

-- ===========================================================================
-- KILLER QUERY 2 — contact tracing, 2 hops from an index case.
-- Every patient who attended any encounter the index case attended.
-- ===========================================================================
SELECT contact FROM cypher('oh_national', $$
  MATCH (idx:Patient {id: 'PAT-0001'})-[:ATTENDED]->(e:Encounter)<-[:ATTENDED]-(c:Patient)
  WHERE c.id <> idx.id
  RETURN DISTINCT c.id AS contact
  ORDER BY contact
$$) AS (contact agtype);

-- ===========================================================================
-- KILLER QUERY 3 — referral flow.
-- (a) aggregated flows PHC -> general -> tertiary; (b) orphan facilities
-- (no outgoing referrals); (c) top referral destination by volume.
-- ===========================================================================
SELECT src, dst, total FROM cypher('oh_national', $$
  MATCH (a:Facility)-[r:REFERRED_TO]->(b:Facility)
  RETURN a.id AS src, b.id AS dst, sum(r.count) AS total
  ORDER BY src, dst
$$) AS (src agtype, dst agtype, total agtype);

SELECT orphan FROM cypher('oh_national', $$
  MATCH (f:Facility)
  WHERE NOT (f)-[:REFERRED_TO]->() AND f.kind <> 'tertiary'
  RETURN f.id AS orphan
  ORDER BY orphan
$$) AS (orphan agtype);

SELECT dst, inbound FROM cypher('oh_national', $$
  MATCH ()-[r:REFERRED_TO]->(b:Facility)
  RETURN b.id AS dst, sum(r.count) AS inbound
  ORDER BY inbound DESC
  LIMIT 1
$$) AS (dst agtype, inbound agtype);
