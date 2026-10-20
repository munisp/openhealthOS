"""Lab closed loop (ADR-0058): analyzer auto-verification rules, delta checks
against prior results, critical-value alerting (fail-LOUD into notification
rails), and reflex testing rules. Rules are data; a rule never silently
discards a result — failures go to technologist review.
"""
from __future__ import annotations
import json, sys

# analyte -> {"lo":..,"hi":..,"crit_lo":..,"crit_hi":..,"delta_pct":..,"unit":..}
RANGES = {
    "K":    {"lo": 3.5, "hi": 5.0, "crit_lo": 2.5, "crit_hi": 6.5, "delta_pct": 20, "unit": "mmol/L"},
    "NA":   {"lo": 135, "hi": 145, "crit_lo": 120, "crit_hi": 160, "delta_pct": 5, "unit": "mmol/L"},
    "HB":   {"lo": 12.0, "hi": 16.0, "crit_lo": 7.0, "crit_hi": 20.0, "delta_pct": 25, "unit": "g/dL"},
    "WBC":  {"lo": 4.0, "hi": 11.0, "crit_lo": 2.0, "crit_hi": 30.0, "delta_pct": 50, "unit": "x10^9/L"},
    "PLT":  {"lo": 150, "hi": 400, "crit_lo": 50, "crit_hi": 1000, "delta_pct": 40, "unit": "x10^9/L"},
    "GLU":  {"lo": 3.9, "hi": 6.1, "crit_lo": 2.2, "crit_hi": 22.0, "delta_pct": 40, "unit": "mmol/L"},
    "CREA": {"lo": 62, "hi": 106, "crit_lo": None, "crit_hi": 500, "delta_pct": 50, "unit": "umol/L"},
}

# reflex: when analyte abnormal in direction, suggest follow-up test
REFLEX = {
    ("HB", "low"): "Blood film + ferritin",
    ("WBC", "high"): "Blood film + malaria RDT",
    ("GLU", "high"): "HbA1c",
    ("CREA", "high"): "Urinalysis + eGFR",
    ("PLT", "low"): "Repeat with citrate sample (rule out clumping)",
}

class Result:
    def __init__(self, analyte, value, patient_id, prior=None):
        self.analyte, self.value, self.patient_id, self.prior = analyte, float(value), patient_id, prior

def evaluate(res: Result) -> dict:
    a = res.analyte.upper()
    r = RANGES.get(a)
    if not r:
        return {"disposition": "review", "reason": f"no rule for {a} — technologist review"}
    if r.get("crit_lo") is not None and res.value < r["crit_lo"]:
        return {"disposition": "critical", "severity": "low",
                "alert": f"CRITICAL {a} {res.value}{r['unit']} (< {r['crit_lo']}) — notify clinician NOW"}
    if r.get("crit_hi") is not None and res.value > r["crit_hi"]:
        return {"disposition": "critical", "severity": "high",
                "alert": f"CRITICAL {a} {res.value}{r['unit']} (> {r['crit_hi']}) — notify clinician NOW"}
    out = {"disposition": "autoverified" if r["lo"] <= res.value <= r["hi"] else "flagged"}
    if res.prior is not None and r.get("delta_pct"):
        delta = abs(res.value - res.prior) / max(abs(res.prior), 1e-9) * 100
        if delta > r["delta_pct"]:
            out = {"disposition": "review", "reason":
                   f"delta check failed: {delta:.0f}% change vs prior {res.prior} (limit {r['delta_pct']}%)"}
    if out["disposition"] == "flagged":
        direction = "low" if res.value < r["lo"] else "high"
        if (a, direction) in REFLEX:
            out["reflex"] = REFLEX[(a, direction)]
    return out

def batch(results: list[Result]) -> dict:
    out = {"autoverified": 0, "flagged": 0, "review": 0, "critical": 0, "alerts": [], "reflexes": []}
    for res in results:
        e = evaluate(res)
        out[e["disposition"]] += 1
        if e["disposition"] == "critical":
            out["alerts"].append({"patient_id": res.patient_id, "alert": e["alert"]})
        if "reflex" in e:
            out["reflexes"].append({"patient_id": res.patient_id, "test": e["reflex"]})
    return out

def main(argv):
    doc = json.load(sys.stdin)
    rs = [Result(x["analyte"], x["value"], x["patient_id"], x.get("prior")) for x in doc.get("results", [])]
    out = batch(rs)
    print(json.dumps(out, indent=2))
    return 1 if out["critical"] else 0  # fail-LOUD for the alerting rail

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
