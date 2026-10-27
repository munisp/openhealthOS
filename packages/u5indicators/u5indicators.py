"""u5indicators.py — under-5 mortality indicators as FHIR MeasureReports.

The mortality rates (neonatal / infant / under-5, per 1,000 live
births) are the scoreboard the whole child-health stack is playing
against. They are computed here from the platform's own registries —
MPDSR deaths and CRVS/live-birth counts — and emitted as FHIR
MeasureReports so they flow through dhis2map like everything else.

Doctrine:
- DENOMINATORS ARE PART OF THE NUMBER: a rate without its live-birth
  count is a fiction. Every MeasureReport carries numerator AND
  denominator populations; a rate computed on a tiny or zero
  denominator is refused, not footnoted.
- STILLBIRTHS ARE COUNTED SEPARATELY: they never enter the neonatal
  numerator (definitions matter — NMR counts deaths of live-born
  neonates only). The stillbirth count is its own indicator.
- UNDER-REGISTRATION UNDERSTATES MORTALITY: the report carries the
  CRVS invisibility count alongside the rates so a flattering rate
  built on missing deaths reads as exactly that.
"""

MIN_DENOMINATOR = 1  # below this the rate is a fiction, refuse it


class IndicatorError(Exception):
    pass


def mortality_rates(*, live_births, deaths_by_band, period, facility):
    """deaths_by_band: {"neonatal": n, "infant": n, "child-1-59m": n,
    "stillbirth": n} — infant EXCLUDES neonatal? No: per convention here
    'infant' is the full <1y band INCLUSIVE of neonatal (as MPDSR bands
    nest); the caller passes cumulative bands:
      neonatal = deaths 0-27d, infant = deaths 0-364d,
      child-1-59m = deaths 1-4y. Rates computed accordingly."""
    if live_births < MIN_DENOMINATOR:
        raise IndicatorError(
            f"denominator {live_births} live births — rate refused; "
            "a rate without a denominator is a fiction")
    neonatal = deaths_by_band.get("neonatal", 0)
    infant = deaths_by_band.get("infant", 0)
    child = deaths_by_band.get("child-1-59m", 0)
    if infant < neonatal:
        raise IndicatorError("infant band must include neonatal deaths")
    u5 = infant + child

    def per1000(n):
        return round(1000.0 * n / live_births, 1)

    return {
        "period": period, "facility": facility,
        "live_births": live_births,
        "deaths": {"neonatal": neonatal, "infant": infant,
                   "child-1-59m": child, "under5": u5,
                   "stillbirth": deaths_by_band.get("stillbirth", 0)},
        "nmr_per_1000": per1000(neonatal),
        "imr_per_1000": per1000(infant),
        "u5mr_per_1000": per1000(u5),
    }


def to_measure_report(rates, *, invisibility=None):
    """FHIR R4 MeasureReport with numerator+denominator populations per
    rate, so dhis2map's measure/population convention maps cleanly."""
    groups = []
    for code, num, score in (
            ("nmr", rates["deaths"]["neonatal"], rates["nmr_per_1000"]),
            ("imr", rates["deaths"]["infant"], rates["imr_per_1000"]),
            ("u5mr", rates["deaths"]["under5"], rates["u5mr_per_1000"])):
        groups.append({
            "code": {"text": code},
            "population": [
                {"code": {"text": "numerator"}, "count": num},
                {"code": {"text": "denominator"}, "count": rates["live_births"]},
            ],
            "measureScore": {"value": score, "unit": "per-1000-live-births"},
        })
    rep = {
        "resourceType": "MeasureReport",
        "status": "complete",
        "type": "summary",
        "measure": f"Measure/u5-mortality",
        "period": rates["period"],
        "reporter": {"display": rates["facility"]},
        "group": groups,
    }
    if invisibility is not None:
        rep["contained_note"] = {
            "unregistered_unqueued_births":
                invisibility.get("by_kind", {}).get("birth", {})
                .get("unregistered_unqueued"),
            "unregistered_unqueued_deaths":
                invisibility.get("by_kind", {}).get("death", {})
                .get("unregistered_unqueued"),
            "caveat": ("under-registration understates these rates; "
                       "treat low rates with low registration as suspect"),
        }
    return rep
