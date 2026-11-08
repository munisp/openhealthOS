"""observatory.py — population-health observatory (the WEBSIG pattern).

A national observatory turns facility events into public indicators:
incidence by region, trends by period, coverage by programme. Its
entire credibility rests on two rules: an indicator is a versioned
definition, and a cell too small to publish is suppressed — never
rounded into plausibility.

Doctrine:
- INDICATORS ARE RATIFIED ENTRIES: numerator, denominator, and
  stratifiers are a versioned definition, ratified four-eyes. The
  observatory computes; it does not invent measures.
- THE K-FLOOR IS A WALL: any cell below the anonymity floor is
  suppressed with the suppression stated. Small numbers are identities.
- EVERY FIGURE CARRIES ITS DEFINITION: the published value travels
  with indicator id, version, period, region, and the checksum of the
  events it was computed from.
- GEOGRAPHY IS A REGISTRY: regions and facilities are entries with
  parent links; a rollup is derived, not re-keyed.
"""

import hashlib
import json

VERSION = "observatory@1.0.0"

K_FLOOR = 5  # cells smaller than this are suppressed, with suppression stated

# Events the observatory knows how to count. Extensions are registry
# loads, not edits.
EVENT_KINDS = ("case", "death", "immunization", "screening",
               "birth", "admission")


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class IndicatorRegistry:
    """Versioned indicator definitions, ratified four-eyes."""

    def __init__(self):
        self.definitions = {}

    def propose(self, indicator_id, definition, *, by, day):
        if indicator_id in self.definitions:
            raise Error(f"indicator {indicator_id} exists; version up, "
                        "never edit in place")
        required = {"event_kind", "numerator", "title"}
        missing = required - set(definition)
        if missing:
            raise Error(f"indicator definition missing "
                        f"{sorted(missing)}")
        if definition["event_kind"] not in EVENT_KINDS:
            raise Error(f"unknown event kind "
                        f"{definition['event_kind']!r}; kinds are "
                        f"{EVENT_KINDS}")
        self.definitions[indicator_id] = {
            **definition, "status": "proposed", "proposed_by": by}

    def ratify(self, indicator_id, *, by, day):
        d = self.definitions.get(indicator_id)
        if d is None or d["status"] != "proposed":
            raise Error(f"nothing to ratify for {indicator_id!r}")
        if by == d["proposed_by"]:
            raise Error("the hand that proposes never approves")
        d["status"] = "live"
        d["ratified_by"] = by

    def live(self, indicator_id):
        d = self.definitions.get(indicator_id)
        if d is None or d.get("status") != "live":
            raise Error(f"no live indicator {indicator_id!r}")
        return d


class Observatory:
    """Counts events into indicators, suppresses small cells, and
    publishes checksummed series."""

    def __init__(self, registry=None):
        self.registry = registry or IndicatorRegistry()
        self.regions = {}
        self.events = []

    # --- geography -----------------------------------------------------
    def register_region(self, region_id, name, parent=None):
        if region_id in self.regions:
            raise Error(f"region {region_id} already registered")
        if parent is not None and parent not in self.regions:
            raise Error(f"parent region {parent!r} unknown")
        self.regions[region_id] = {"name": name, "parent": parent}

    def _rollup(self, region_id):
        """The region and every descendant — a parent's figure includes
        its children."""
        out = {region_id}
        changed = True
        while changed:
            changed = False
            for rid, r in self.regions.items():
                if r["parent"] in out and rid not in out:
                    out.add(rid)
                    changed = True
        return out

    # --- ingest ----------------------------------------------------------
    def record_event(self, event_kind, *, region, day, condition,
                     count=1, source):
        if event_kind not in EVENT_KINDS:
            raise Error(f"unknown event kind {event_kind!r}")
        if region not in self.regions:
            raise Error(f"unknown region {region!r}; geography is a "
                        "registry")
        if not source:
            raise Error("an event names its source")
        if not (isinstance(count, int) and count > 0):
            raise Error("count is a positive integer")
        self.events.append({"kind": event_kind, "region": region,
                            "day": day, "condition": condition,
                            "count": count, "source": source})

    # --- compute ---------------------------------------------------------
    def compute(self, indicator_id, *, region, period_start, period_end):
        """One indicator, one region (rollup included), one period.
        Cells under the k-floor are suppressed with the suppression
        stated — never zeroed, never rounded."""
        definition = self.registry.live(indicator_id)
        if period_end < period_start:
            raise Error("period end precedes period start")
        chain = self._rollup(region)
        matched = [e for e in self.events
                   if e["kind"] == definition["event_kind"]
                   and e["region"] in chain
                   and period_start <= e["day"] <= period_end
                   and e["condition"] in definition["numerator"]]
        total = sum(e["count"] for e in matched)
        base = {
            "indicator": indicator_id,
            "indicator_version": definition.get("version", "unversioned"),
            "region": region, "period": [period_start, period_end],
            "events_sha256": _sha(matched), "version": VERSION,
        }
        if 0 < total < K_FLOOR:
            return {**base, "value": None, "suppressed": True,
                    "suppression": f"count below k-floor {K_FLOOR} — "
                                   "suppressed, not rounded"}
        if total == 0:
            return {**base, "value": 0, "suppressed": False,
                    "suppression": None,
                    "note": "zero events matched — stated as zero"}
        return {**base, "value": total, "suppressed": False,
                "suppression": None}

    def series(self, indicator_id, *, region, period_start, period_end,
               step):
        """Period-over-period series; each point carries the same
        provenance as a single compute."""
        if step < 1:
            raise Error("step is at least one day")
        points = []
        start = period_start
        while start <= period_end:
            end = min(start + step - 1, period_end)
            c = self.compute(indicator_id, region=region,
                             period_start=start, period_end=end)
            points.append(c)
            start = end + 1
        return points

    def publish(self, indicator_id, *, region, period_start, period_end,
                by, day):
        """A publication is a signed, checksummed artifact: the figure,
        its definition, its period, and the hash of the events behind
        it. Republishing the same inputs yields the same hash."""
        if not by:
            raise Error("a publication names its publisher")
        point = self.compute(indicator_id, region=region,
                             period_start=period_start,
                             period_end=period_end)
        return {**point, "published_by": by, "published_day": day,
                "publication_sha256": _sha({**point, "by": by})}
