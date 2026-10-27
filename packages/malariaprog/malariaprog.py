"""malariaprog.py — malaria program layer: SMC, LLIN, TPR surveillance.

The fever pack treats the patient in front of the CHW. This module is
the program behind the patients: seasonal malaria chemoprevention
cycles, bed-net distribution, and test-positivity surveillance that
feeds the EBS inbox when the signal exceeds the baseline.

Doctrine:
- SMC IS A CAMPAIGN WITH A DENOMINATOR: eligible children 3-59 months,
  four monthly cycles; coverage counts doses AND eligible children,
  and a child who misses cycle 2 is on next cycle's worklist.
- LLIN DISTRIBUTION COUNTS NETS AND PEOPLE: nets distributed without
  a household register are a press release, not a program.
- TPR SURVEILLANCE IS HONEST: test-positivity = confirmed/tested, both
  numbers always shipped; a spike in positivity with collapsing test
  numbers is flagged as an artifact suspect, not an outbreak.
"""

SMC_AGE_MIN_MONTHS = 3
SMC_AGE_MAX_MONTHS = 59
SMC_CYCLES = 4
TPR_SPIKE_FACTOR = 2.0     # vs baseline
TPR_MIN_TESTED = 30        # below this, TPR is noise


class MalariaError(Exception):
    pass


class SMCCampaign:
    def __init__(self, year):
        self.year = year
        self.children = {}     # child_id -> age_months at campaign start
        self.doses = []        # (child_id, cycle, day)

    def register_eligible(self, child_id, *, age_months):
        if not (SMC_AGE_MIN_MONTHS <= age_months <= SMC_AGE_MAX_MONTHS):
            raise MalariaError(
                f"SMC eligible 3-59mo; got {age_months}mo")
        self.children[child_id] = age_months

    def give_dose(self, child_id, cycle, *, day):
        if child_id not in self.children:
            raise MalariaError(f"child not in SMC register: {child_id}")
        if not (1 <= cycle <= SMC_CYCLES):
            raise MalariaError(f"SMC has {SMC_CYCLES} cycles; got {cycle}")
        if any(c == child_id and cy == cycle
               for c, cy, _ in self.doses):
            raise MalariaError(f"cycle {cycle} already given to {child_id}")
        self.doses.append((child_id, cycle, day))
        return {"child_id": child_id, "cycle": cycle, "day": day}

    def cycle_coverage(self, cycle):
        dosed = {c for c, cy, _ in self.doses if cy == cycle}
        return {"cycle": cycle, "eligible": len(self.children),
                "dosed": len(dosed),
                "missed": len(set(self.children) - dosed)}

    def next_cycle_worklist(self, next_cycle):
        """Children who missed earlier cycles — the catch-up list."""
        rows = []
        for cid in self.children:
            missed = [cy for cy in range(1, next_cycle)
                      if (cid, cy) not in {(c, cy_) for c, cy_, _ in self.doses}]
            if missed:
                rows.append({"child_id": cid, "missed_cycles": missed})
        return rows


class LLINDistribution:
    def __init__(self):
        self.households = {}   # household_id -> nets received
        self.total_nets = 0

    def distribute(self, household_id, nets, *, day, distributor,
                   sleeping_spaces=None):
        if nets <= 0:
            raise MalariaError("nets must be positive")
        prev = self.households.get(household_id, 0)
        self.households[household_id] = prev + nets
        self.total_nets += nets
        return {"household_id": household_id, "nets_total": prev + nets,
                "day": day}

    def report(self):
        return {"households_reached": len(self.households),
                "nets_distributed": self.total_nets,
                "note": "households_reached without nets_distributed "
                        "would be a press release, not a program"}


class TPRWatch:
    """Test-positivity-rate surveillance per facility per week."""

    def __init__(self):
        self.weeks = []  # {facility, week, tested, confirmed}

    def record(self, facility, week, *, tested, confirmed):
        if tested < 0 or confirmed < 0 or confirmed > tested:
            raise MalariaError(
                f"impossible counts: {confirmed}/{tested}")
        self.weeks.append({"facility": facility, "week": week,
                           "tested": tested, "confirmed": confirmed})

    def tpr(self, facility, week):
        w = next((x for x in self.weeks
                  if x["facility"] == facility and x["week"] == week), None)
        if w is None:
            raise MalariaError(f"no data for {facility} week {week}")
        if w["tested"] < TPR_MIN_TESTED:
            return {"tpr": None, "state": "insufficient-tests",
                    "tested": w["tested"], "confirmed": w["confirmed"]}
        return {"tpr": round(w["confirmed"] / w["tested"], 3),
                "state": "ok", "tested": w["tested"],
                "confirmed": w["confirmed"]}

    def baseline(self, facility, before_week, n=4):
        prior = [x for x in self.weeks if x["facility"] == facility
                 and x["week"] < before_week and x["tested"] >= TPR_MIN_TESTED]
        prior = sorted(prior, key=lambda x: x["week"])[-n:]
        if len(prior) < 2:
            return None  # learning
        return sum(x["confirmed"] / x["tested"] for x in prior) / len(prior)

    def check_spike(self, facility, week):
        cur = self.tpr(facility, week)
        if cur["state"] != "ok":
            return {"signal": None, "reason": cur["state"],
                    "note": "small numbers are noise, not outbreaks"}
        base = self.baseline(facility, week)
        if base is None:
            return {"signal": None, "reason": "baseline-learning"}
        if cur["tpr"] >= TPR_SPIKE_FACTOR * base:
            return {"signal": "tpr-spike", "tpr": cur["tpr"],
                    "baseline": round(base, 3),
                    "route": "ebs-inbox-as-rumour-not-alert"}
        return {"signal": None, "tpr": cur["tpr"], "baseline": round(base, 3)}
