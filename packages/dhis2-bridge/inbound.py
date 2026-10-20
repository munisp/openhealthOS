"""DHIS2 inbound sync (ADR-0057): org-unit pull → canonical facility registry.
Outbound aggregates were one-way; this closes the loop so the DHIS2/HMIS
org-unit registry is the upstream master list and OHF ids stay mapped via
dhis2_uid. Network is injected (fetcher callable) — offline edges replay the
last good payload from disk.
"""
from __future__ import annotations
import json, sys
from facilityreg import FacilityRegistry

def map_orgunit(ou: dict) -> dict:
    """DHIS2 orgUnit -> facilityreg import shape. Parent chain supplies
    state/lga when attributes are absent."""
    parent = ou.get("parent") or {}
    return {"id": ou.get("id"), "name": ou.get("name", ""),
            "state": ou.get("state") or parent.get("state") or parent.get("name", "unknown"),
            "lga": ou.get("lga") or parent.get("name", "unknown"),
            "type": ou.get("type", "unknown")}

def sync_orgunits(fetcher, registry: FacilityRegistry, snapshot_path: str | None = None) -> dict:
    """fetcher() -> {"organisationUnits": [...]}; raises when unreachable."""
    try:
        payload = fetcher()
        units = payload.get("organisationUnits", [])
        if snapshot_path:
            with open(snapshot_path, "w") as fh:
                json.dump(payload, fh)
        source = "live"
    except Exception as e:
        if not snapshot_path:
            raise
        with open(snapshot_path) as fh:
            units = json.load(fh).get("organisationUnits", [])
        source = f"snapshot (upstream unreachable: {e})"
    result = registry.import_dhis2_orgunits([map_orgunit(u) for u in units])
    return {**result, "source": source, "units_seen": len(units)}

def main(argv):
    print(json.dumps({"error": "inbound sync runs via library; see sync_orgunits()"}))
    return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
