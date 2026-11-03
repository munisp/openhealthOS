"""units.py — UCUM-shaped unit normalization and LOINC binding gates.

The single most common device-integration patient-safety bug is a
unit mismatch: mg/dL read as mmol/L kills. Doctrine:

  - conversions are DATA, versioned, and explicit — factor + target,
    never an implicit convention;
  - an observation whose code is unbound passes through RAW with the
    absence stated — unbound is not a code, and silence is not a
    conversion;
  - conversion requires a numeric value; a non-numeric value through
    a conversion is a lie with decimals.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicehub import DeviceHubError  # noqa: E402

UNITS_VERSION = "ucum-shaped@1.0.0"

# local/device code -> LOINC (versioned; extend by row, not by fork)
LOINC_BINDINGS = {
    "K": "2823-3",      # potassium
    "NA": "2951-2",     # sodium
    "GLU": "2345-7",    # glucose
    "CREA": "2160-0",   # creatinine
    "HB": "718-7",      # hemoglobin
    "CHOL": "2093-3",   # total cholesterol
}

# (loinc, from_unit, to_unit) -> multiply factor
CONVERSIONS = {
    ("2345-7", "mg/dL", "mmol/L"): 1 / 18.018,
    ("2345-7", "mmol/L", "mg/dL"): 18.018,
    ("2160-0", "mg/dL", "umol/L"): 88.42,
    ("2160-0", "umol/L", "mg/dL"): 1 / 88.42,
    ("2093-3", "mg/dL", "mmol/L"): 0.02586,
    ("2093-3", "mmol/L", "mg/dL"): 38.67,
    ("718-7", "g/dL", "g/L"): 10.0,
    ("718-7", "g/L", "g/dL"): 0.1,
}


def normalize_observation(obs, *, target_unit=None):
    """Bind the code, convert the unit. Returns a new observation dict.

    - known code → loinc stamped;
    - target_unit given and a conversion exists → numeric value
      converted, `converted` marked with the factor's provenance;
    - target_unit given but no conversion row → refuse loudly (a
      missing row is a data gap, not an invitation to guess);
    - unknown code → passed raw with binding stated as absent."""
    out = dict(obs)
    code = obs.get("code")
    loinc = LOINC_BINDINGS.get(code)
    out["loinc"] = loinc
    if loinc is None:
        out["binding"] = None
        out["binding_note"] = ("unbound code — passed raw and stated; "
                               "unbound is not a code")
    else:
        out["binding"] = "loinc"
    if target_unit is None or target_unit == obs.get("units"):
        return out
    if loinc is None:
        raise DeviceHubError(
            f"cannot convert an unbound code {code!r} — bind the code "
            "before you convert its units; order matters")
    key = (loinc, obs.get("units"), target_unit)
    factor = CONVERSIONS.get(key)
    if factor is None:
        raise DeviceHubError(
            f"no conversion row for {key} — a missing row is a data "
            "gap, not an invitation to guess; add the row, version "
            "the table")
    value = obs.get("value_numeric")
    if value is None:
        raise DeviceHubError(
            f"{code}: conversion needs a numeric value — converting a "
            "string is a lie with decimals")
    out["value_numeric"] = round(value * factor, 4)
    out["value"] = str(out["value_numeric"])
    out["units"] = target_unit
    out["converted"] = {"from": obs.get("units"), "factor": factor,
                        "version": UNITS_VERSION}
    return out
