"""poe_screen.py — points-of-entry screening.

The index case in an imported-outbreak scenario arrives through an
airport or a land border. PoE screening must be fast, offline-capable,
and versioned: the list of countries under alert changes as the WHO
situation evolves, and a border post screening against last week's list
is the leak the whole system exists to close.

Doctrine:
  - the alert-country list is VERSIONED (like casedef): screening with a
    superseded list refuses, naming the current version;
  - dispositions are exactly three: pass, observe (temp/symptom flags
    without exposure), isolate-and-notify (exposure + any flag) — the
    last CREATES the contact-trace seed at the border, not after;
  - every screen records the screener and the list version: audit is
    free, reconstruction is impossible.
"""
from __future__ import annotations

FEVER_C = 38.0


class PoEError(Exception):
    pass


class AlertList:
    def __init__(self):
        self.versions = []  # [{version, countries, effective_day}]

    def publish(self, version: str, countries: list[str],
                effective_day: str):
        if not countries:
            raise PoEError("an empty alert list screens nobody — refusing")
        if any(v["version"] == version for v in self.versions):
            raise PoEError(f"{version} already published — lists never "
                           "mutate, publish a new one")
        self.versions.append({"version": version,
                              "countries": sorted(c.lower() for c in countries),
                              "effective_day": effective_day})

    def current(self):
        if not self.versions:
            raise PoEError("no alert list published")
        return self.versions[-1]


class BorderPost:
    def __init__(self, post_id: str, alert_list: AlertList):
        self.post = post_id
        self.alerts = alert_list
        self.records = []

    def screen(self, traveller_id: str, list_version: str,
               travel_history_21d: list[str], temp_c: float | None,
               symptoms: list[str], screener: str, day: str) -> dict:
        cur = self.alerts.current()
        if list_version != cur["version"]:
            raise PoEError(
                f"alert list {list_version} is stale — current is "
                f"{cur['version']} ({', '.join(cur['countries'])}); sync "
                "before screening")
        if not screener.strip():
            raise PoEError("screener identity required — audit is free, "
                           "reconstruction is impossible")
        exposure = [c for c in travel_history_21d
                    if c.lower() in cur["countries"]]
        febrile = temp_c is not None and temp_c >= FEVER_C
        symptomatic = bool(symptoms)
        if exposure and (febrile or symptomatic):
            disposition = "isolate-and-notify"
        elif febrile or symptomatic:
            disposition = "observe"
        else:
            disposition = "pass"
        rec = {"post": self.post, "traveller": traveller_id,
               "list_version": list_version, "exposure": exposure,
               "temp_c": temp_c, "symptoms": symptoms,
               "screener": screener, "day": day,
               "disposition": disposition}
        self.records.append(rec)
        if disposition == "isolate-and-notify":
            rec["trace_seed"] = {
                "exposure_day": day,
                "note": "border-detected suspect — register as case/"
                        "suspect and start the ring; the trace begins at "
                        "the border, not after admission"}
        return rec

    def stats(self) -> dict:
        out = {"post": self.post, "screened": len(self.records)}
        for d in ("pass", "observe", "isolate-and-notify"):
            out[d] = sum(1 for r in self.records if r["disposition"] == d)
        return out
