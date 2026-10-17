#!/usr/bin/env python3
"""OpenHealth OS — structural FHIR profile validation.

Validates resources against the Nigerian profiles in fhir/profiles at the
level a stdlib-only edge stack can own: resourceType match plus presence
of every element the profile marks min ≥ 1 (including sliced elements like
Claim.extension:adjudicationStatus). This is deliberately structural —
full FHIR conformance (terminology bindings, invariants) belongs to a
FHIR server; what the edge needs is a hard guarantee that a resource
carries everything the profile requires before it enters the SHR or a
claims batch.

Usage:
    python3 validate.py RESOURCE_JSON PROFILE_JSON   # exit 1 on violations
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    element_id: str      # e.g. "Claim.status" or "Claim.extension:adjudicationStatus"
    min_card: int
    slice_name: str | None  # e.g. "adjudicationStatus" for sliced elements


def load_requirements(profile: dict) -> tuple[str, list[Requirement]]:
    """Extract (resourceType, required elements) from a StructureDefinition."""
    rtype = profile.get("type")
    reqs = []
    for el in profile.get("differential", {}).get("element", []):
        if el.get("min", 0) >= 1:
            eid = el["id"]
            path, _, slice_name = eid.partition(":")
            reqs.append(Requirement(path, el["min"], slice_name or None))
    return rtype, reqs


def _get_path(resource: dict, dotted: str):
    """Walk 'Patient.name.given' — lists transparently searched."""
    parts = dotted.split(".")[1:]  # strip the resourceType head
    current = [resource]
    for part in parts:
        nxt = []
        for node in current:
            if isinstance(node, dict) and part in node:
                value = node[part]
                nxt.extend(value if isinstance(value, list) else [value])
        current = nxt
        if not current:
            return []
    return current


def _slice_present(resource: dict, req: Requirement) -> bool:
    """Sliced required element: check the named slice exists. For the
    profiles here, slices are identified by a 'code' (extensions) or
    'system' (identifiers) carrying the slice name."""
    values = _get_path(resource, req.element_id)
    haystack = json.dumps(values).lower()
    return req.slice_name.lower() in haystack


def validate(resource: dict, profile: dict) -> list[str]:
    """Return the list of violations ([] = valid)."""
    violations = []
    rtype, reqs = load_requirements(profile)
    if resource.get("resourceType") != rtype:
        return [f"resourceType is {resource.get('resourceType')!r}, profile constrains {rtype!r}"]
    for req in reqs:
        if req.slice_name:
            if not _slice_present(resource, req):
                violations.append(f"missing required slice: {req.element_id}:{req.slice_name}")
        elif not _get_path(resource, req.element_id):
            violations.append(f"missing required element: {req.element_id}")
    return violations


def _resolve_profile(resource_type: str) -> dict:
    """One-arg mode: find the OpenHealth profile constraining this
    resourceType under fhir/profiles/ (relative to this file)."""
    import glob
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(here, "..", "..", "fhir", "profiles", "*.json"))):
        profile = json.load(open(path))
        if profile.get("resourceType") == "StructureDefinition":
            for el in profile.get("snapshot", {}).get("element", []):
                if el.get("id") == profile.get("type"):
                    break
            if profile.get("type") == resource_type:
                return profile
    raise SystemExit(f"no profile found for resourceType {resource_type!r}")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    resource = json.load(open(sys.argv[1]))
    profile = (json.load(open(sys.argv[2])) if len(sys.argv) == 3
               else _resolve_profile(resource.get("resourceType", "")))
    violations = validate(resource, profile)
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print(f"OK: {sys.argv[1]} satisfies {profile.get('id', 'profile')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
