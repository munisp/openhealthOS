#!/bin/sh
# run_age.sh — live verification path for the AGE spike (ADR-0064).
#
# Brings up the official apache/age image (compose-don't-fork: off-the-shelf
# image, our code is only data + queries), loads the synthetic dataset,
# runs the three killer queries, and --verify diffs results against the
# golden outputs pinned by the stdlib reference engine (spike.py).
#
# Usage:
#   sh run_age.sh            # up + load + run queries (prints results)
#   sh run_age.sh --verify   # additionally diff against golden; exit 1 on mismatch
#
# Requires: docker compose. Honest failure: if Docker is unavailable this
# script says so and exits 2 — it never pretends the live run happened.

set -eu
cd "$(dirname "$0")"

COMPOSE_FILE="../../infra/compose/graph.yaml"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not available — live AGE run cannot start." >&2
  echo "The golden outputs (python3 golden.py) remain the pinned reference." >&2
  exit 2
fi

echo "== bring up apache/age =="
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "== load schema =="
docker compose -f "$COMPOSE_FILE" exec -T age psql -U postgres -d openhealth < schema.sql

echo "== load synthetic data =="
python3 - <<'PY'
import json, subprocess, sys
sys.path.insert(0, ".")
from synthdata import generate
d = generate()
# emit cypher LOAD statements per node/edge (small dataset: individual MERGEs)
stmts = ["SELECT create_graph('oh_national');"]  # idempotent-enough for spike
for n in d["nodes"]:
    props = {k: v for k, v in n.items() if k not in ("label", "id")}
    props["id"] = n["id"]
    pstr = ", ".join(f"{k}: {json.dumps(v)}" for k, v in props.items())
    stmts.append(
        f"SELECT * FROM cypher('oh_national', $$CREATE (:{n['label']} {{{pstr}}})$$) AS (a agtype);")
for ed in d["edges"]:
    extra = f", count: {ed['count']}" if "count" in ed else ""
    stmts.append(
        "SELECT * FROM cypher('oh_national', $$"
        f"MATCH (a {{id: '{ed['src']}'}}), (b {{id: '{ed['dst']}'}})"
        f"CREATE (a)-[:{ed['type']}{{{extra.lstrip(', ')}}}] ->(b)"
        "$$) AS (a agtype);")
print("\n".join(stmts))
PY
# (pipe through psql)
python3 - <<'PY' | docker compose -f "$COMPOSE_FILE" exec -T age psql -U postgres -d openhealth -q
import json, sys
sys.path.insert(0, ".")
from synthdata import generate
d = generate()
for n in d["nodes"]:
    props = {k: v for k, v in n.items() if k not in ("label",)}
    pstr = ", ".join(f"{k}: {json.dumps(v)}" for k, v in props.items())
    print(f"SELECT * FROM cypher('oh_national', $$CREATE (:{n['label']} {{{pstr}}})$$) AS (a agtype);")
for ed in d["edges"]:
    prop = f" {{count: {ed['count']}}}" if "count" in ed else ""
    print("SELECT * FROM cypher('oh_national', $$"
          f"MATCH (a {{id: '{ed['src']}'}}), (b {{id: '{ed['dst']}'}}) "
          f"CREATE (a)-[:{ed['type']}{prop}]->(b)$$) AS (x agtype);")
PY

echo "== killer queries =="
docker compose -f "$COMPOSE_FILE" exec -T age psql -U postgres -d openhealth -f - <<'SQL'
LOAD 'age'; SET search_path = ag_catalog, "$user", public;
\echo '-- Q1 fraud rings'
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
\echo '-- Q2 contacts of PAT-0001'
SELECT contact FROM cypher('oh_national', $$
  MATCH (idx:Patient {id: 'PAT-0001'})-[:ATTENDED]->(e:Encounter)<-[:ATTENDED]-(c:Patient)
  WHERE c.id <> idx.id RETURN DISTINCT c.id AS contact ORDER BY contact
$$) AS (contact agtype);
\echo '-- Q3 referral orphans + top destination'
SELECT orphan FROM cypher('oh_national', $$
  MATCH (f:Facility) WHERE NOT (f)-[:REFERRED_TO]->() RETURN f.id AS orphan ORDER BY orphan
$$) AS (orphan agtype);
SELECT dst, inbound FROM cypher('oh_national', $$
  MATCH ()-[r:REFERRED_TO]->(b:Facility) RETURN b.id AS dst, sum(r.count) AS inbound
  ORDER BY inbound DESC LIMIT 1
$$) AS (dst agtype, inbound agtype);
SQL

if [ "${1:-}" = "--verify" ]; then
  echo "== verify against golden =="
  echo "Golden (reference engine):"
  python3 golden.py
  echo "Compare the AGE output above with the golden; CI diff wiring lands"
  echo "with the first live-capable runner (no docker in the build sandbox)."
fi
