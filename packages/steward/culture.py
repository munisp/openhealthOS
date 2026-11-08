"""culture.py — culture-guided de-escalation for stewardship.

steward.py counts AWaRe patterns; this module closes the loop with
the microbiology bench: a culture result arrives with susceptibilities,
and the current therapy is judged AGAINST it. Continuing a Watch or
Reserve antibiotic when an Access drug covers the organism is not a
style choice — it breeds resistance, and the engine says so with the
evidence attached.

Doctrine:
- SUSCEPTIBILITY IS A LAB FACT: S/I/R per drug from the bench; a
  therapy judgement without a culture refuses — empiric therapy is a
  different module's honest admission, not this one's guess.
- DE-ESCALATION CARRIES ITS EVIDENCE: every recommendation names the
  organism, the culture day, and the susceptibility row it rests on.
- NARROWEST EFFECTIVE ACCESS FIRST: among susceptible drugs the
  engine prefers AWaRe Access over Watch over Reserve; ties break to
  the narrowest spectrum declared in the drug entry.
- THE MICROBIOLOGIST OVERRIDES, STATED: a human may reject the
  recommendation with a reason; the rejection is recorded, not
  hidden.
"""

CULTURE_VERSION = "culturegram@1.0.0"


class Error(Exception):
    pass


# AWaRe rank — lower is preferred when susceptible
_AWARE_RANK = {"Access": 0, "Watch": 1, "Reserve": 2}


class DrugSpectrumRegistry:
    """Drug entries: AWaRe class + declared spectrum width (1 narrowest
    .. 5 broadest). Entries are ratified four-eyes like everything
    else that touches prescribing."""

    def __init__(self, aware_map):
        self.aware = aware_map          # shared with steward.AWARE
        self.spectrum = {}              # drug_code -> 1..5
        self.status = {}

    def register(self, drug_code, *, spectrum, by, day):
        if drug_code not in self.aware:
            raise Error(f"{drug_code} has no AWaRe entry — register "
                        "the classification before the spectrum")
        if not (1 <= spectrum <= 5):
            raise Error("spectrum width is declared 1 (narrowest) "
                        ".. 5 (broadest)")
        self.spectrum[drug_code] = spectrum
        self.status[drug_code] = {"by": by, "day": day}

    def entry(self, drug_code):
        if drug_code not in self.spectrum:
            raise Error(f"no spectrum entry for {drug_code} — the "
                        "engine does not guess widths")
        return {"aware": self.aware[drug_code],
                "spectrum": self.spectrum[drug_code]}


class CultureBoard:
    """Culture results and the de-escalation judgements they drive."""

    def __init__(self, registry):
        self.registry = registry
        self.cultures = {}        # culture_id -> result
        self.judgements = []

    def post_culture(self, culture_id, *, patient_ref, organism,
                     susceptibilities, day, by):
        """susceptibilities: {drug_code: "S"|"I"|"R"} from the bench."""
        if not organism:
            raise Error("a culture names its organism")
        if not susceptibilities:
            raise Error("a culture with no susceptibilities guides "
                        "nothing — refused")
        for drug, sir in susceptibilities.items():
            if sir not in ("S", "I", "R"):
                raise Error(f"susceptibility for {drug} is S/I/R, "
                            f"not {sir!r}")
        if culture_id in self.cultures:
            raise Error(f"culture {culture_id} exists — an amended "
                        "report is a new entry, not an overwrite")
        self.cultures[culture_id] = {
            "id": culture_id, "patient_ref": patient_ref,
            "organism": organism,
            "susceptibilities": dict(susceptibilities),
            "day": day, "posted_by": by}
        return self.cultures[culture_id]

    def judge(self, culture_id, *, current_therapy, day, by):
        """Judge current therapy against the culture. Returns a
        verdict: continue / de-escalate (with candidate) / escalate
        (organism not covered) — each with its evidence row."""
        culture = self.cultures.get(culture_id)
        if not culture:
            raise Error(f"unknown culture {culture_id} — judgement "
                        "without a bench result refuses")
        susc = culture["susceptibilities"]
        current_sir = susc.get(current_therapy)
        evidence = {"culture_id": culture_id,
                    "organism": culture["organism"],
                    "culture_day": culture["day"],
                    "current_therapy": current_therapy,
                    "current_susceptibility": current_sir}

        if current_sir is None:
            verdict = {"verdict": "untested",
                       "reason": f"{current_therapy} was not on the "
                                 "panel — coverage is unknown, stated"}
        elif current_sir == "R":
            verdict = {"verdict": "escalate",
                       "reason": f"{culture['organism']} is RESISTANT "
                                 f"to {current_therapy} — continuing "
                                 "it is documented harm"}
        else:
            candidates = [d for d, s in susc.items()
                          if s == "S" and d != current_therapy]
            ranked = sorted(
                candidates,
                key=lambda d: (
                    _AWARE_RANK.get(
                        self.registry.entry(d)["aware"], 9),
                    self.registry.entry(d)["spectrum"]))
            current_entry = self.registry.entry(current_therapy)
            if ranked:
                best = ranked[0]
                best_entry = self.registry.entry(best)
                better = (_AWARE_RANK[best_entry["aware"]],
                          best_entry["spectrum"]) < (
                    _AWARE_RANK[current_entry["aware"]],
                    current_entry["spectrum"])
                if better and current_sir == "S" or \
                        (current_sir == "I" and best_entry):
                    verdict = {
                        "verdict": "de-escalate",
                        "candidate": best,
                        "reason": f"{best} ({best_entry['aware']}, "
                                  f"spectrum {best_entry['spectrum']})"
                                  " covers the organism and is "
                                  "narrower/lighter than "
                                  f"{current_therapy} "
                                  f"({current_entry['aware']})"}
                else:
                    verdict = {"verdict": "continue",
                               "reason": f"{current_therapy} is "
                                         "already the narrowest "
                                         "effective option"}
            else:
                verdict = {"verdict": "continue",
                           "reason": "no susceptible alternative on "
                                     "the panel — stated"}

        judgement = {**evidence, **verdict, "judged_by": by,
                     "day": day, "overrides": []}
        self.judgements.append(judgement)
        return judgement

    def override(self, judgement_idx, *, decision, reason, by, day):
        """The microbiologist's word is final — and recorded."""
        j = self.judgements[judgement_idx]
        if not reason:
            raise Error("an override without a reason is stealth — "
                        "state it")
        j["overrides"].append({"decision": decision, "reason": reason,
                               "by": by, "day": day})
        return j
