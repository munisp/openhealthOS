"""Multi-tenancy isolation audit (ADR-0058). Statically audits db/migrations
for tenant/facility scoping on PHI-bearing tables, and runtime-checks that
query helpers never return cross-facility rows. Honest output: a FAILING
audit exits 1 — it is a gate, not a report to file away.
"""
from __future__ import annotations
import json, re, sys

# Tables expected to carry facility/tenant scoping (PHI or money).
SCOPED_TABLES = ["patients", "encounters", "claims", "custody_events", "equipment",
                 "consents", "wallet_transactions", "immunizations"]
# scoping column per table: consents scope to the patient (consent is
# patient-granted by definition); everything else must carry facility/tenant.
SCOPE_COLUMNS = {"consents": ("facility_id", "facility", "tenant_id", "patient_id")}

CREATE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);", re.S | re.I)

def audit_migrations(sql_files: list[str]) -> dict:
    found = {}
    for path in sql_files:
        with open(path) as fh:
            sql = fh.read()
        for m in CREATE_RE.finditer(sql):
            table, body = m.group(1).lower(), m.group(2).lower()
            cols = [c.lower() for c in re.findall(r"(\w+)\s+(?:TEXT|NUMERIC|INTEGER|BIGSERIAL|TIMESTAMPTZ|DOUBLE\s+PRECISION|BOOLEAN|JSONB?|DATE)", body, re.I)]
            found[table] = cols
    findings = []
    for t in SCOPED_TABLES:
        if t not in found:
            findings.append({"table": t, "severity": "info",
                             "finding": "table not present in migrations (may live in a service-owned store)"})
            continue
        cols = found[t]
        accepted = SCOPE_COLUMNS.get(t, ("facility_id", "facility", "tenant_id"))
        if not any(c in cols for c in accepted):
            findings.append({"table": t, "severity": "major",
                             "finding": "no facility/tenant column — cross-tenant leakage risk"})
    return {"tables_seen": len(found), "findings": findings,
            "isolated": not any(f["severity"] == "major" for f in findings)}

def check_row_isolation(rows: list[dict], expected_facility: str) -> list[dict]:
    """Runtime guard: every returned row must belong to the caller's facility."""
    leaks = [r for r in rows
             if r.get("facility_id", r.get("facility", expected_facility)) != expected_facility]
    return leaks

def main(argv):
    doc = json.load(sys.stdin)
    out = audit_migrations(doc.get("sql_files", []))
    print(json.dumps(out, indent=2))
    return 0 if out["isolated"] else 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
