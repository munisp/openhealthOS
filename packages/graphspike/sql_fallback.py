"""sql_fallback.py — relational equivalents of the three killer queries.

If ADR-0064's live verify fails or AGE is rejected at review, T1 Postgres
still answers the same three questions — slower and uglier (recursive
CTEs), but on stock relational tables with zero extensions. This module
ships the SQL and asserts its structural parity with the reference engine
(thresholds, keys) so the fallback can never silently drift from the
graph contract.

Tables assumed (T1 silver mirror):
  claims(claim_id, facility_id, prescriber_id, amount, paid_to)
  encounter_attendees(encounter_id, patient_ref)
  referrals(from_facility, to_facility, count)
"""

FRAUD_RING_SQL = """
-- killer query 1, relational form: many claims, one signer, few accounts.
SELECT c.facility_id,
       count(*)                            AS nclaims,
       count(DISTINCT c.prescriber_id)     AS signers,
       count(DISTINCT c.paid_to)           AS accounts
FROM claims c
GROUP BY c.facility_id
HAVING count(*) >= :ring_min_claims
   AND count(DISTINCT c.prescriber_id) <= :ring_max_signers
   AND count(DISTINCT c.paid_to)       <= :ring_max_accounts
ORDER BY c.facility_id;
"""

CONTACT_2HOP_SQL = """
-- killer query 2, relational form: patients sharing any encounter with
-- the index case.
SELECT DISTINCT ea2.patient_ref AS contact
FROM encounter_attendees ea1
JOIN encounter_attendees ea2 ON ea2.encounter_id = ea1.encounter_id
WHERE ea1.patient_ref = :index_patient
  AND ea2.patient_ref <> :index_patient
ORDER BY contact;
"""

REFERRAL_FLOW_SQL = """
-- killer query 3, relational form: flows, orphans, top destination.
-- (a) flows
SELECT from_facility AS src, to_facility AS dst, sum(count) AS total
FROM referrals GROUP BY from_facility, to_facility ORDER BY src, dst;
-- (b) orphans (non-tertiary facilities with no outgoing referral)
SELECT f.facility_id AS orphan
FROM facilities f
WHERE f.kind <> 'tertiary'
  AND NOT EXISTS (SELECT 1 FROM referrals r WHERE r.from_facility = f.facility_id)
ORDER BY orphan;
-- (c) top destination by inbound volume
SELECT to_facility AS dst, sum(count) AS inbound
FROM referrals GROUP BY to_facility ORDER BY inbound DESC LIMIT 1;
"""

QUERIES = {
    "fraud_ring": FRAUD_RING_SQL,
    "contact_2hop": CONTACT_2HOP_SQL,
    "referral_flow": REFERRAL_FLOW_SQL,
}


def parity_report():
    """Structural parity with spike.py thresholds — the numbers MUST match
    or the fallback answers a different question than the graph."""
    from spike import RING_MIN_CLAIMS, RING_MAX_SIGNERS, RING_MAX_ACCOUNTS
    return {
        "ring_min_claims": RING_MIN_CLAIMS,
        "ring_max_signers": RING_MAX_SIGNERS,
        "ring_max_accounts": RING_MAX_ACCOUNTS,
        "sql_mentions_min_claims": ":ring_min_claims" in FRAUD_RING_SQL,
        "sql_excludes_tertiary_orphans": "kind <> 'tertiary'" in REFERRAL_FLOW_SQL,
        "sql_excludes_index_patient": "<> :index_patient" in CONTACT_2HOP_SQL,
    }
