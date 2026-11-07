"""Operational observability — alert before a gate trips, not after.

Collects engine-side signals from the registries and renders them in
Prometheus text exposition format (industry standard, zero vendor
lock). Alert rules are versioned entries: ppm trend, SLA burn-rate,
QR verify-failure rate, BR-10 acknowledgement latency. A rule that
cannot be traced to a metric is a vibe.
"""

METRICS_VERSION = "vhmetrics@1.0.0"


class MetricsError(Exception):
    pass


def _refuse(msg):
    raise MetricsError(msg)


# name -> {"help", "unit", "source"} — the vocabulary is the contract
METRICS = {
    "vhc_settlement_ppm": {
        "help": "settlement reconciliation error rate vs file",
        "unit": "ppm", "source": "settle"},
    "vhc_helpdesk_sla_burn": {
        "help": "first-response SLA breaches / responses",
        "unit": "ratio", "source": "helpdesk"},
    "vhc_qr_verify_failures": {
        "help": "QR verifications refused (expired/revoked/bad sig)",
        "unit": "count", "source": "registry"},
    "vhc_br10_open_alerts": {
        "help": "surveillance alerts not yet acknowledged",
        "unit": "count", "source": "surveillance"},
    "vhc_fund_allocated_ratio": {
        "help": "fund allocated / fund collected",
        "unit": "ratio", "source": "fund"},
}

# rule -> {"metric", "op", "threshold", "severity", "help"}
ALERT_RULES = {
    "settlement_ppm_breach": {
        "metric": "vhc_settlement_ppm", "op": ">", "threshold": 1000,
        "severity": "critical",
        "help": "reconciliation beyond the 1000 ppm tolerance"},
    "helpdesk_sla_burn_high": {
        "metric": "vhc_helpdesk_sla_burn", "op": ">", "threshold": 0.05,
        "severity": "warning",
        "help": "more than 5% of first responses breaching SLA"},
    "br10_unacknowledged": {
        "metric": "vhc_br10_open_alerts", "op": ">", "threshold": 0,
        "severity": "critical",
        "help": "an unacknowledged alert is a finding, not a statistic"},
    "qr_verify_failures_spike": {
        "metric": "vhc_qr_verify_failures", "op": ">", "threshold": 25,
        "severity": "warning",
        "help": "verify failures above daily corridor baseline"},
}

_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "==": lambda a, b: a == b}


def collect(registries):
    """Pull the current values from the live registries. Every source
    names itself; a metric with no source is refused."""
    out = {}
    settle = registries.get("settle")
    if settle is not None and hasattr(settle, "report"):
        out["vhc_settlement_ppm"] = rep.get("ppm", 0)
    desk = registries.get("desk")
    if desk is not None and hasattr(desk, "report"):
        rep = desk.report()
        sla = rep.get("sla", {})
        responded = sla.get("responded", 0)
        out["vhc_helpdesk_sla_burn"] = (
            (sla.get("breached", 0) / responded) if responded else 0.0)
    vreg = registries.get("vreg")
    if vreg is not None and hasattr(vreg, "report"):
        rep = vreg.report()
        out["vhc_qr_verify_failures"] = rep.get("verify_failures", 0)
        out["vhc_br10_open_alerts"] = rep.get("open_alerts", 0)
    fund = registries.get("fund")
    if fund is not None and hasattr(fund, "report"):
        rep = fund.report()
        collected = rep.get("collected_minor", 0)
        out["vhc_fund_allocated_ratio"] = (
            rep.get("allocated_minor", 0) / collected) if collected else 0.0
    return out



def evaluate_alerts(values):
    firing = []
    for name, rule in ALERT_RULES.items():
        v = values.get(rule["metric"])
        if v is None:
            _refuse(f"rule {name} has no metric value — "
                    "a rule that cannot be traced to a metric is a vibe")
        if _OPS[rule["op"]](v, rule["threshold"]):
            firing.append({"rule": name, "severity": rule["severity"],
                           "metric": rule["metric"], "value": v,
                           "threshold": rule["threshold"],
                           "help": rule["help"]})
    return firing


def render_prometheus(values):
    lines = []
    for name in sorted(values):
        meta = METRICS.get(name)
        if meta is None:
            _refuse(f"unregistered metric {name!r} — the vocabulary is "
                    "the contract")
        lines.append(f"# HELP {name} {meta['help']}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {values[name]}")
    return "\n".join(lines) + "\n"


def report(values):
    firing = evaluate_alerts(values)
    return {"version": METRICS_VERSION, "metrics": len(values),
            "alerts_firing": len(firing),
            "critical": sum(1 for f in firing
                            if f["severity"] == "critical"),
            "firing": firing}
