"""fhircover.py — honest FHIR coverage counting (ADR-0061).

last-ehr's fhir-coverage.md doctrine: "how comprehensive is it?"
deserves a number with a stated denominator, not an adjective — and
the ceiling matters as much as the current mark. There is no
"percentage of FHIR R4" here on purpose: FHIR R4 has 150+ resource
types and nobody needs all of them; the denominator must be a profile
set implementers are actually asked about (US Core's 27 readable
types) or a declared local equivalent.

Doctrine:
  - the denominator is DECLARED and VERSIONED, never implied
  - coverage is per-resource-type with evidence (which package/route
    serves it), not a bare count
  - "readable" means a real read path exists; aspirational entries are
    listed as gaps, not rounded up
  - a coverage claim without its denominator version refuses to
    aggregate — numbers without denominators are marketing
"""

from __future__ import annotations

VERSION = "fhircover@1.0.0"

# US Core 9.0.0: 27 distinct readable resource types (per the published
# profile list, as cited by last-ehr docs/fhir-coverage.md).
US_CORE_READABLE = (
    "AllergyIntolerance", "CarePlan", "CareTeam", "Condition",
    "Coverage", "Device", "DiagnosticReport", "DocumentReference",
    "Encounter", "Goal", "Immunization", "Location",
    "Medication", "MedicationDispense", "MedicationRequest",
    "Observation", "Organization", "Patient", "Practitioner",
    "PractitionerRole", "Procedure", "Provenance", "Questionnaire",
    "QuestionnaireResponse", "RelatedPerson", "ServiceRequest",
    "Specimen",
)


class CoverageError(Exception):
    pass


class CoverageMatrix:
    def __init__(self, denominator, *, denominator_version):
        if not denominator:
            raise CoverageError("coverage without a denominator is an "
                                "adjective, not a number")
        if not denominator_version:
            raise CoverageError("a denominator without a version cannot "
                                "be re-verified")
        self.denominator = tuple(denominator)
        self.denominator_version = denominator_version
        self._entries = {}

    def claim(self, resource_type, *, evidence):
        """evidence: the package/route that actually serves reads.
        A claim without evidence is a hope."""
        if resource_type not in self.denominator:
            raise CoverageError(
                f"{resource_type} is not in denominator "
                f"{self.denominator_version} — extend the denominator "
                "explicitly, never silently")
        if not evidence:
            raise CoverageError(f"{resource_type}: coverage claimed "
                                "without evidence")
        self._entries[resource_type] = {"status": "readable",
                                        "evidence": evidence}

    def report(self):
        readable = sorted(self._entries)
        gaps = [r for r in self.denominator if r not in self._entries]
        return {
            "denominator": self.denominator_version,
            "denominator_size": len(self.denominator),
            "readable": len(readable),
            "gaps": gaps,
            "fraction": f"{len(readable)}/{len(self.denominator)}",
            "entries": dict(self._entries),
            "note": "no percentage of FHIR R4 is computed — the "
                    "denominator is the declared profile set",
        }
