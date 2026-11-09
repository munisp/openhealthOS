"""outbreaksim.py — deterministic tabletop outbreak simulator.

Outbreak response is learned during outbreaks — the most expensive
classroom there is. This simulator generates seeded, reproducible
epidemic scenarios (index case, branching generations, detection
probability) so teams can drill escalation timing and ring-vaccination
strategy against the SAME outbreak twice.

Doctrine:
- SEEDED AND REPRODUCIBLE: same seed, same outbreak, byte-for-byte
  checksum. A drill you cannot replay is a story, not training.
- DETECTION IS A PLAYER: cases surface only through the declared
  detection probability — the scenario honestly models that outbreaks
  are found late.
- INTERVENTIONS CHANGE THE FUTURE ONLY: ring vaccination cuts the
  effective R from its start generation forward; history is append-only.
"""

VERSION = "outbreaksim@1.0.0"

import hashlib
import json
import math
import random


class SimError(Exception):
    pass


def _poisson(rng, lam):
    """Knuth's algorithm — exact, and deterministic under a seeded rng."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def simulate(*, seed, generations, r0, population, detection=0.5,
             vaccinate_from_gen=None, vaccine_efficacy=0.8):
    if not (0 <= detection <= 1) or not (0 <= vaccine_efficacy <= 1):
        raise SimError("probabilities must be in [0, 1]")
    if population < 10:
        raise SimError("population too small to simulate honestly")
    if generations <= 0 or r0 < 0:
        raise SimError("implausible generations or r0")
    rng = random.Random(seed)
    log = []
    active, total, detected = 1, 1, 0
    for gen in range(generations):
        r_eff = r0
        if vaccinate_from_gen is not None and gen >= vaccinate_from_gen:
            r_eff = r0 * (1 - vaccine_efficacy)
        new_cases = 0
        for _ in range(active):
            offspring = _poisson(rng, r_eff)
            new_cases += min(offspring, max(0, population - total
                                            - new_cases))
        for _ in range(new_cases):
            if rng.random() < detection:
                detected += 1
        total += new_cases
        log.append({"generation": gen, "infectious": active,
                    "new_cases": new_cases, "total_cases": total,
                    "detected_cum": detected, "r_effective": r_eff})
        active = new_cases
        if active == 0 or total >= population:
            break
    result = {"seed": seed, "generations_run": len(log), "log": log,
              "total_cases": total, "detected": detected,
              "detection_rate": detection}
    result["checksum"] = hashlib.sha256(json.dumps(
        {k: v for k, v in result.items() if k != "checksum"},
        sort_keys=True).encode()).hexdigest()
    return result


def compare_strategies(**kw):
    """Same seed, with and without ring vaccination — the drill's core
    comparison. Returns both runs plus the honest delta."""
    base = simulate(**kw)
    vacc = simulate(**{**kw, "vaccinate_from_gen":
                       kw.get("vaccinate_from_gen", 3)})
    return {"baseline": base, "ring_vaccination": vacc,
            "cases_averted": base["total_cases"] - vacc["total_cases"],
            "note": "identical seed — the delta is the intervention, "
                    "not the dice"}
