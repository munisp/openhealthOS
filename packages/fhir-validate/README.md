# fhir-validate

Structural validation of resources against the Nigerian FHIR profiles in
`fhir/profiles/`. Stdlib-only, edge-safe: checks resourceType match plus
presence of every element the profile marks `min ≥ 1`, including sliced
elements (e.g. `Claim.extension:adjudicationStatus`).

Deliberately structural — full conformance (terminology bindings,
invariants, reference resolution) belongs to the FHIR server. What the
edge needs before a resource enters the SHR or a claims batch is a hard
guarantee that everything the profile requires is present.

```bash
# single resource
python3 validate.py fhir/examples/claim-valid.json fhir/profiles/openhealth-claim.json

# self-tests (pinned against the repo's own profiles + examples)
python3 -m unittest -v
```

Exit code 1 on any violation — safe for CI and for pre-ingest gating.
