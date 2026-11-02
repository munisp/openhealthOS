"""staging.py — TNM cancer staging capture, edition-aware.

Doctrine:
- THE EDITION IS PART OF THE STAGE. "T2N1M0" means different things
  under AJCC 7th and 8th; a stage without its edition is not a stage.
- Categories are validated per site against a shaped subset — an
  impossible category (T4 colon in a T1-only list) refuses loudly.
  The tables are versioned deployment data, re-typed from published
  AJCC/UICC categories; extend them deliberately, not casually.
- Basis matters: clinical (cTNM) and pathological (pTNM) are
  different measurements; the prefix is recorded, never merged.
- The stage history is append-only: restaging after progression or
  neoadjuvant therapy is a NEW entry; the first stage stays on the
  record — the lab notebook does not erase.
"""

STAGING_VERSION = "tnm-staging@1.0.0"

# site -> {"editions": {...}, "t": (...), "n": (...), "m": (...)}
# Shaped subsets of AJCC/UICC 8th-edition categories. Absence of a
# site is not clearance to stage it freehand — it is a refusal until
# the deployment adds the site table deliberately.
TNM_SITES = {
    "colon": {
        "t": ("tis", "1", "2", "3", "4a", "4b"),
        "n": ("0", "1a", "1b", "1c", "2a", "2b"),
        "m": ("0", "1a", "1b", "1c"),
    },
    "breast": {
        "t": ("tis", "1", "1mi", "2", "3", "4a", "4b", "4c", "4d"),
        "n": ("0", "0i+", "1mi", "1", "1a", "1b", "1c", "2", "2a",
              "2b", "3", "3a", "3b", "3c"),
        "m": ("0", "0i+", "1"),
    },
    "cervix": {
        "t": ("1", "1a", "1a1", "1a2", "1b", "1b1", "1b2", "1b3",
              "2", "2a", "2a1", "2a2", "2b", "3", "3a", "3b", "3c",
              "4", "4a", "4b"),
        "n": ("0", "0i+", "1", "1mi", "2"),
        "m": ("0", "1"),
    },
    "lung": {
        "t": ("tis", "1", "1a", "1b", "1c", "2", "2a", "2b", "3",
              "4"),
        "n": ("0", "1", "2", "3"),
        "m": ("0", "1a", "1b", "1c"),
    },
}

EDITIONS = ("ajcc-8",)   # the only edition this table is shaped from;
                        # do not claim 7th-edition stages from it
BASES = ("clinical", "pathological")   # c/p prefix


class StagingError(Exception):
    pass


class StagingRegistry:
    """Per-patient append-only TNM stage history."""

    def __init__(self):
        self.stagings = {}   # patient_id -> [stage records]

    def stage(self, patient_id, *, site, t, n, m, basis, by, day,
              evidence_ref, edition="ajcc-8"):
        if edition not in EDITIONS:
            raise StagingError(
                f"edition '{edition}' not in {EDITIONS} — the edition "
                "is part of the stage; an unshaped edition is not a "
                "stage, it is a guess")
        if site not in TNM_SITES:
            raise StagingError(
                f"no category table for site '{site}' — absence of a "
                "table is a refusal, not clearance to stage freehand")
        if basis not in BASES:
            raise StagingError(f"basis must be one of {BASES} — c and "
                               "p are different measurements")
        table = TNM_SITES[site]
        for axis, value in (("t", t), ("n", n), ("m", m)):
            if not value:
                raise StagingError(f"{axis.upper()} category required "
                                   "— an unstaged axis is not a zero")
            if value.lower() not in table[axis]:
                raise StagingError(
                    f"impossible category {axis.upper()}{value} for "
                    f"{site} under {edition} — valid: "
                    f"{sorted(table[axis])}")
        if not by:
            raise StagingError("unsigned stages are no stage")
        if not evidence_ref:
            raise StagingError("a stage cites its evidence — the "
                               "report/specimen it was read from")
        prefix = "c" if basis == "clinical" else "p"
        rec = {"patient_id": patient_id, "site": site,
               "t": t.lower(), "n": n.lower(), "m": m.lower(),
               "basis": basis, "edition": edition, "by": by,
               "day": day, "evidence_ref": evidence_ref,
               "notation": f"{prefix}T{t.upper()}N{n.upper()}"
                           f"M{m.upper()}",
               "version": STAGING_VERSION}
        self.stagings.setdefault(patient_id, []).append(rec)
        return dict(rec)

    def current_stage(self, patient_id, *, site=None):
        """Latest stage for the patient (optionally per site) with the
        full history — restaging never erases the first stage."""
        rows = [r for r in self.stagings.get(patient_id, [])
                if site is None or r["site"] == site]
        if not rows:
            raise StagingError(
                f"no stage on record for {patient_id}"
                + (f" ({site})" if site else "")
                + " — absence is not stage 0")
        return {"current": dict(rows[-1]),
                "history": [dict(r) for r in rows],
                "restaged": len(rows) > 1,
                "version": STAGING_VERSION}
