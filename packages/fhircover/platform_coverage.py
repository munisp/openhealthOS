"""platform_coverage.py — OpenHealth OS's declared FHIR coverage.

The platform's coverage claim, with evidence per type and the
denominator stated. Medplum (the production FHIR R4 store) serves the
full REST surface; shr-service is the contract-only seam whose
directly-served types are listed separately. A type reachable only
through Medplum is still readable — the evidence column says through
what.

This file is data with an opinion: when a read path is added or
removed, this file changes in the same commit, or the number is a lie.
"""

from fhircover import US_CORE_READABLE, CoverageMatrix

MEDPLUM = "Medplum FHIR R4 server (full REST surface)"
SHR = "shr-service contract seam"


def platform_matrix():
    m = CoverageMatrix(US_CORE_READABLE,
                       denominator_version="us-core-9.0.0")
    # Direct shr-service evidence (grep-verified 2026-11-09):
    direct = {"Patient", "Observation", "Condition", "Immunization",
              "Encounter"}
    for rt in US_CORE_READABLE:
        evidence = [MEDPLUM]
        if rt in direct:
            evidence.append(SHR)
        m.claim(rt, evidence=" + ".join(evidence))
    return m


if __name__ == "__main__":
    import json
    print(json.dumps(platform_matrix().report(), indent=2))
