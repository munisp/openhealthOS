"""Provider economics rail (ADR-0032): margin-per-code cost accounting,
capitation panel P&L per risk band, denial-reason analytics, and tariff
simulation — the analytics half of "make capitation + FFS sustainable".

Doctrine:
  * stdlib-only; consumes ledger/IPD/theatre exports and the packages/
    benefits tariff schedule (single source of truth for prices).
  * AI optimizes workflow and pricing, never care: undercoding recovery
    is legitimate revenue integrity; upcoding is fraud and belongs to
    fraudml, never here. No output of this package may suppress care.
  * Every figure is reproducible: reports carry their inputs' counts and
    the tariff vintage year.
"""

from collections import defaultdict

try:  # tariff schedule is the benefits package's seed
    from benefits import SERVICES, TARIFF_YEAR
except ImportError:  # standalone test path
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "benefits"))
    from benefits import SERVICES, TARIFF_YEAR


class EconomicsError(Exception):
    pass


# ------------------------------------------------------------- margin per code

def cost_per_encounter(enc):
    """True cost of one encounter from its captured inputs (kobo):
    staff minutes x per-minute cost, consumables, allocated overhead
    (theatre/ward/power apportioned per day), and length-of-stay."""
    staff = sum(m["minutes"] * m["kobo_per_minute"] for m in enc.get("staff", []))
    consumables = sum(c["kobo"] for c in enc.get("consumables", []))
    los_days = enc.get("los_days", 0)
    overhead = los_days * enc.get("overhead_kobo_per_day", 0)
    return staff + consumables + overhead


def margin_report(encounters, overhead_kobo_per_day=0):
    """Margin per service code across a period. encounters: dicts with
    code + cost inputs. Tariffs come from the benefits schedule; a code
    missing from the schedule is reported with tariff 0 (flagged)."""
    agg = defaultdict(lambda: {"count": 0, "revenue": 0, "cost": 0})
    unknown = set()
    for enc in encounters:
        code = enc.get("code")
        if not code:
            raise EconomicsError("encounter missing service code")
        e = dict(enc)
        e.setdefault("overhead_kobo_per_day", overhead_kobo_per_day)
        name_cat = SERVICES.get(code)
        tariff = name_cat[2] if name_cat else 0
        if not name_cat:
            unknown.add(code)
        a = agg[code]
        a["count"] += 1
        a["revenue"] += tariff
        a["cost"] += cost_per_encounter(e)
    lines = []
    for code, a in agg.items():
        margin = a["revenue"] - a["cost"]
        lines.append({
            "code": code,
            "name": SERVICES.get(code, (code, "?", 0))[0],
            "count": a["count"],
            "tariff_kobo": SERVICES.get(code, (code, "?", 0))[2],
            "revenue_kobo": a["revenue"],
            "cost_kobo": a["cost"],
            "margin_kobo": margin,
            "margin_pct": round(margin * 100 / a["revenue"], 1) if a["revenue"] else None,
            "in_tariff_schedule": code in SERVICES,
        })
    lines.sort(key=lambda l: l["margin_kobo"])  # worst margin first
    return {
        "tariff_year": TARIFF_YEAR,
        "encounters": len(encounters),
        "lines": lines,
        "unknown_codes": sorted(unknown),
        "totals": {
            "revenue_kobo": sum(l["revenue_kobo"] for l in lines),
            "cost_kobo": sum(l["cost_kobo"] for l in lines),
            "margin_kobo": sum(l["margin_kobo"] for l in lines),
        },
    }


# ------------------------------------------------------------- capitation panel P&L

def risk_band(enrollee):
    """Age/condition banding — the actuarial minimum. A flat capitation
    rate hides this; the panel P&L exposes it."""
    age = enrollee.get("age", 30)
    if enrollee.get("pregnant"):
        return "maternal"
    if enrollee.get("chronic"):
        return "chronic"
    if age < 5:
        return "under5"
    if age >= 65:
        return "elderly"
    if age >= 45:
        return "older-adult"
    return "adult"


def panel_pnl(enrollees, encounters, capitation_kobo,
              overhead_kobo_per_day=0):
    """Capitation revenue vs cost of care delivered, per risk band.
    enrollees: [{id, age, pregnant?, chronic?}]; encounters carry
    enrollee_id + cost inputs. Unattributed encounters land in
    'walk-in' — care consumed without capitation attribution."""
    by_band = defaultdict(lambda: {"enrollees": 0, "capitation": 0, "cost": 0, "encounters": 0})
    band_of = {}
    for e in enrollees:
        b = risk_band(e)
        band_of[e["id"]] = b
        by_band[b]["enrollees"] += 1
        by_band[b]["capitation"] += capitation_kobo
    for enc in encounters:
        b = band_of.get(enc.get("enrollee_id"), "walk-in")
        e = dict(enc)
        e.setdefault("overhead_kobo_per_day", overhead_kobo_per_day)
        by_band[b]["cost"] += cost_per_encounter(e)
        by_band[b]["encounters"] += 1
    bands = []
    for b, a in sorted(by_band.items()):
        margin = a["capitation"] - a["cost"]
        per_enrollee = round(margin / a["enrollees"]) if a["enrollees"] else None
        bands.append({"band": b, "enrollees": a["enrollees"],
                      "capitation_kobo": a["capitation"],
                      "care_cost_kobo": a["cost"],
                      "margin_kobo": margin,
                      "margin_per_enrollee_kobo": per_enrollee,
                      "encounters": a["encounters"]})
    return {
        "capitation_kobo": capitation_kobo, "tariff_year": TARIFF_YEAR,
        "panel_size": len(enrollees), "encounters": len(encounters),
        "bands": bands,
        "total_margin_kobo": sum(b["margin_kobo"] for b in bands),
        "walk_in_cost_kobo": next((b["care_cost_kobo"] for b in bands if b["band"] == "walk-in"), 0),
    }


# ------------------------------------------------------------- denial analytics

DENIAL_CLASSES = {
    "missing-referral": "preventable",
    "wrong-tariff-vintage": "preventable",
    "documentation-gap": "preventable",
    "not-covered": "package",
    "duplicate-claim": "fraud-screen",
    "panel-mismatch": "preventable",
}


def denial_report(claims):
    """Denials by reason class and by HMO. claims: [{hmo, status,
    denial_reason?, amount_kobo}]. The preventable fraction is the
    revenue-cycle target; 'not-covered' denials point at benefit
    literacy, not process."""
    by_reason = defaultdict(lambda: {"count": 0, "amount_kobo": 0})
    by_hmo = defaultdict(lambda: {"submitted": 0, "denied": 0, "denied_kobo": 0})
    for c in claims:
        h = by_hmo[c.get("hmo", "unknown")]
        h["submitted"] += 1
        if c.get("status") != "denied":
            continue
        reason = c.get("denial_reason", "unknown")
        by_reason[reason]["count"] += 1
        by_reason[reason]["amount_kobo"] += c.get("amount_kobo", 0)
        h["denied"] += 1
        h["denied_kobo"] += c.get("amount_kobo", 0)
    preventable_kobo = sum(a["amount_kobo"] for r, a in by_reason.items()
                           if DENIAL_CLASSES.get(r) == "preventable")
    denied_kobo = sum(a["amount_kobo"] for a in by_reason.values())
    return {
        "claims": len(claims),
        "denied": sum(a["count"] for a in by_reason.values()),
        "by_reason": {r: dict(a, klass=DENIAL_CLASSES.get(r, "unknown"))
                      for r, a in sorted(by_reason.items())},
        "by_hmo": {h: dict(a, denial_rate_pct=round(a["denied"] * 100 / a["submitted"], 1))
                   for h, a in sorted(by_hmo.items()) if a["submitted"]},
        "preventable_denied_kobo": preventable_kobo,
        "preventable_share_pct": round(preventable_kobo * 100 / denied_kobo, 1) if denied_kobo else 0,
    }


# ------------------------------------------------------------- tariff simulation

def simulate_tariff_change(utilization, cpi_path, target_year, uplift_pct=0.0):
    """Before NHIA publishes a new schedule: what does it do to provider
    margin, pool outflow, and enrollee co-pay? utilization: [{code,
    volume}] for a reference period. Uplift applies on top of the
    CPI-indexed expected tariff (from packages/benefits staleness math).
    """
    if target_year <= TARIFF_YEAR:
        raise EconomicsError("target year must be after the tariff year")
    factor = 1.0
    for y in range(TARIFF_YEAR + 1, target_year + 1):
        factor *= 1 + cpi_path.get(y, cpi_path.get("default", 0.15))
    factor *= 1 + uplift_pct / 100
    lines = []
    tot_current = tot_expected = tot_copay_delta = 0
    for u in utilization:
        code = u["code"]
        vol = u["volume"]
        svc = SERVICES.get(code)
        if not svc:
            continue
        current = svc[2]
        expected = round(current * factor)
        lines.append({"code": code, "name": svc[0], "volume": vol,
                      "current_tariff_kobo": current,
                      "expected_tariff_kobo": expected,
                      "delta_kobo": expected - current})
        tot_current += current * vol
        tot_expected += expected * vol
        tot_copay_delta += (expected - current) * vol * 10 // 100  # 10% co-pay
    return {
        "tariff_year": TARIFF_YEAR, "target_year": target_year,
        "cpi_factor": round(factor, 4), "uplift_pct": uplift_pct,
        "lines": lines,
        "pool_outflow_delta_kobo": tot_expected - tot_current,
        "pool_outflow_delta_pct": round((tot_expected - tot_current) * 100 / tot_current, 1) if tot_current else 0,
        "enrollee_copay_delta_kobo": tot_copay_delta,
    }


# ------------------------------------------------------------- CLI

if __name__ == "__main__":
    import json
    import sys

    op = json.load(sys.stdin)
    kind = op.get("op")
    try:
        if kind == "margins":
            out = margin_report(op["encounters"], op.get("overhead_kobo_per_day", 0))
        elif kind == "panel":
            out = panel_pnl(op["enrollees"], op["encounters"],
                            int(op["capitation_kobo"]), op.get("overhead_kobo_per_day", 0))
        elif kind == "denials":
            out = denial_report(op["claims"])
        elif kind == "tariff-sim":
            out = simulate_tariff_change(op["utilization"], op.get("cpi_path", {}),
                                         int(op["target_year"]), op.get("uplift_pct", 0.0))
        else:
            raise EconomicsError("unknown op: %r" % kind)
    except (EconomicsError, KeyError) as e:
        json.dump({"error": str(e)}, sys.stdout)
        sys.exit(1)
    json.dump(out, sys.stdout)
