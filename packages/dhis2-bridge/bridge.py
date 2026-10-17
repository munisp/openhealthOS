"""OpenHealth OS — DHIS2 aggregate bridge.

Facility-level events roll up to Nigeria's existing national HMIS (DHIS2) so
national reporting continues unbroken and facilities never dual-enter
(ADR-0002). Reads encounter/dispensing events (the same JSON-line domain
events the sync agent ships), aggregates per facility per period, and emits
a DHIS2-shaped dataValueSet payload.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime

# Indicator mapping: domain event type → DHIS2 data element id.
# (Ids are illustrative; production binds the state's DHIS2 metadata.)
DATA_ELEMENTS = {
    "encounter": "DE_OPD_VISITS",
    "anc_visit": "DE_ANC_VISITS",
    "immunisation": "DE_IMMUNISATIONS",
    "dispensing": "DE_DISPENSING",
    "claim_submitted": "DE_CLAIMS_SUBMITTED",
    "stockout_day": "DE_STOCKOUT_DAYS",
}


def period_of(ts: str) -> str:
    """DHIS2 monthly period (YYYYMM) from an ISO timestamp."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y%m")


def aggregate(events: list[dict]) -> list[dict]:
    """Group events into (facility, period, dataElement) counts."""
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for ev in events:
        de = DATA_ELEMENTS.get(ev.get("type", ""))
        if not de or "facility" not in ev or "ts" not in ev:
            continue  # unknown event types pass through silently
        counts[(ev["facility"], period_of(ev["ts"]), de)] += 1
    return [
        {"orgUnit": fac, "period": per, "dataElement": de, "value": n}
        for (fac, per, de), n in sorted(counts.items())
    ]


def to_datavalueset(values: list[dict], dataset: str = "OH-PHC-MONTHLY") -> dict:
    """Render aggregates as a DHIS2 dataValueSet payload."""
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for v in values:
        by_key[(v["orgUnit"], v["period"])].append(
            {"dataElement": v["dataElement"], "value": v["value"]})
    sets = []
    for (org, period), dvs in sorted(by_key.items()):
        sets.append({
            "dataSet": dataset,
            "orgUnit": org,
            "period": period,
            "completeDate": datetime.now().date().isoformat(),
            "dataValues": dvs,
        })
    return {"dataValueSets": sets}


if __name__ == "__main__":
    events = [json.loads(line) for line in sys.stdin if line.strip()]
    print(json.dumps(to_datavalueset(aggregate(events)), indent=2))
