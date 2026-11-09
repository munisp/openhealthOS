"""test_fhircover.py — honest coverage counting."""

import pytest

from fhircover import (CoverageError, CoverageMatrix, US_CORE_READABLE)


def make_matrix():
    return CoverageMatrix(US_CORE_READABLE,
                          denominator_version="us-core-9.0.0")


def test_denominator_required():
    with pytest.raises(CoverageError, match="adjective"):
        CoverageMatrix([], denominator_version="x")
    with pytest.raises(CoverageError, match="re-verified"):
        CoverageMatrix(["Patient"], denominator_version="")


def test_claim_needs_evidence():
    m = make_matrix()
    with pytest.raises(CoverageError, match="without evidence"):
        m.claim("Patient", evidence="")
    m.claim("Patient", evidence="shr-service GET /Patient/{id}")
    assert m.report()["readable"] == 1


def test_off_denominator_refused():
    m = make_matrix()
    with pytest.raises(CoverageError, match="not in denominator"):
        m.claim("Astronaut", evidence="somewhere")


def test_report_names_gaps():
    m = make_matrix()
    for rt in ("Patient", "Observation", "Condition",
               "AllergyIntolerance", "MedicationRequest",
               "Immunization", "DocumentReference"):
        m.claim(rt, evidence="shr-service + Medplum")
    r = m.report()
    assert r["fraction"] == "7/27"
    assert len(r["gaps"]) == 20
    assert "Specimen" in r["gaps"]
    assert "percentage" in r["note"]


def test_platform_declaration():
    from platform_coverage import platform_matrix
    r = platform_matrix().report()
    assert r["fraction"] == "27/27"
    assert r["gaps"] == []
    # shr-service direct evidence is named, not implied
    assert "shr-service" in r["entries"]["Patient"]["evidence"]
