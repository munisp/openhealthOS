"""test_derive.py — derivation registry + reference scores + harness."""

import pytest

from derive import (DerivationRegistry, DeriveError, load_builtins,
                    validate_output, fingerprint)


@pytest.fixture()
def registry():
    reg = DerivationRegistry()
    load_builtins(reg, proposer="clinician-a")
    for name in ("sofa", "qsofa", "kdigo-aki", "meld-na", "charlson",
                 "suspected-infection", "sepsis-3"):
        reg.ratify(name, "1.0.0", approver="cmo")
    return reg


def test_four_eyes(registry):
    reg = DerivationRegistry()
    load_builtins(reg, proposer="clinician-a")
    with pytest.raises(DeriveError, match="never ratifies"):
        reg.ratify("sofa", "1.0.0", approver="clinician-a")
    with pytest.raises(DeriveError, match="placeholder"):
        reg.compute("sofa", {})


def test_sofa_bands(registry):
    out = registry.compute("sofa", {
        "pao2": 90, "fio2": 0.9, "on_vent": True,       # ratio 100 -> 4
        "platelets": 80,                                 # -> 2
        "bilirubin": 7.0,                                # -> 3
        "map": 60,                                       # -> 1
        "gcs": 8,                                        # -> 3
        "creatinine": 3.8,                               # -> 3
    })
    assert out["value"] == 16
    assert out["components"]["respiration"] == 4
    assert out["grain"] == "per-stay-per-day"
    assert out["spec_fingerprint"]


def test_sofa_missing_refuses(registry):
    with pytest.raises(DeriveError, match="absence is not a value"):
        registry.compute("sofa", {"pao2": 100, "fio2": 0.5,
                                  "platelets": 200, "bilirubin": 1.0,
                                  "map": 80})  # gcs + creatinine absent


def test_qsofa(registry):
    assert registry.compute("qsofa", {"rr": 24, "gcs": 14,
                                      "sbp": 95})["value"] == 3
    assert registry.compute("qsofa", {"rr": 16, "gcs": 15,
                                      "sbp": 120})["value"] == 0


def test_kdigo_stages(registry):
    s3 = registry.compute("kdigo-aki", {"baseline_creatinine": 1.0,
                                        "creatinine": 3.4})
    assert s3["value"] == 3
    uo = registry.compute("kdigo-aki", {"baseline_creatinine": 1.0,
                                        "creatinine": 1.1,
                                        "urine_ml_kg_hr": 0.4,
                                        "urine_window_hours": 14})
    assert uo["value"] == 2
    with pytest.raises(DeriveError, match="absence"):
        registry.compute("kdigo-aki", {"creatinine": 2.0})


def test_meld_na_bounds(registry):
    out = registry.compute("meld-na", {"bilirubin": 3.0, "inr": 1.8,
                                       "creatinine": 1.9,
                                       "sodium": 130})
    assert 6 <= out["value"] <= 40
    dia = registry.compute("meld-na", {"bilirubin": 1.0, "inr": 1.0,
                                       "creatinine": 0.6, "sodium": 140,
                                       "on_dialysis": True})
    assert dia["value"] >= 6
    assert dia["sodium_used"] == 137  # clamped


def test_charlson(registry):
    out = registry.compute("charlson", {
        "icd10_codes": ["I21.0", "I50.9", "N18.4", "E11.9"], "age": 65})
    # MI 1 + CHF 1 + CKD 2 + DM 1 + age 3 = 8
    assert out["value"] == 8
    assert "myocardial-infarction" in out["conditions"]
    with pytest.raises(DeriveError, match="implausible age"):
        registry.compute("charlson", {"icd10_codes": [], "age": 400})


def test_suspected_infection_windows(registry):
    si = registry.compute("suspected-infection",
                          {"culture_time": 100, "antibiotic_time": 150})
    assert si["value"] == 1 and si["basis"] == "culture->abx within 72h"
    no = registry.compute("suspected-infection",
                          {"culture_time": 100, "antibiotic_time": 300})
    assert no["value"] == 0
    rev = registry.compute("suspected-infection",
                           {"culture_time": 110, "antibiotic_time": 100})
    assert rev["value"] == 1
    with pytest.raises(DeriveError, match="BOTH"):
        registry.compute("suspected-infection", {"culture_time": 100})


def test_sepsis3(registry):
    assert registry.compute("sepsis-3", {"suspected_infection": 1,
                                         "sofa_total": 3})["value"] == 1
    assert registry.compute("sepsis-3", {"suspected_infection": 1,
                                         "sofa_total": 1})["value"] == 0


def test_fingerprint_stable(registry):
    spec = registry.get("sofa")
    assert fingerprint(spec) == fingerprint(dict(spec))


def test_validate_output_harness(registry):
    spec = registry.get("sofa")
    rows = [{"stay": "s1", "day": 1, "value": 8},
            {"stay": "s1", "day": 2, "value": 10}]
    good = validate_output(spec, rows, key_cols=("stay", "day"),
                           expected_keys=[("s1", 1), ("s1", 2)])
    assert good["ok"]
    duped = rows + [{"stay": "s1", "day": 1, "value": 9}]
    bad = validate_output(spec, duped, key_cols=("stay", "day"))
    assert not bad["ok"]
    oor = [{"stay": "s1", "day": 1, "value": 99}]
    assert not validate_output(spec, oor, key_cols=("stay", "day"))["ok"]
    missing = validate_output(spec, rows, key_cols=("stay", "day"),
                              expected_keys=[("s1", 1), ("s1", 2),
                                             ("s1", 3)])
    assert not missing["ok"]
