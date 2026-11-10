# ADR-0078: Cross-runtime geodesy convergence — and the behavior guard

- Status: accepted (2026-11-11)
- Extends: ADR-0075 (geodesy convergence), ADR-0077 (structure gate; census-miss doctrine)

## Context

ADR-0075 converged the estate's Python distance arithmetic on
`geolock.GEODESY_CONTRACT` and presented the census as the estate's set
of private haversines. ADR-0077 already corrected that census once
(gisexport's closure). Wave-17 recon asked the scope question directly —
*which runtimes measure distance?* — and found the wave-14 census had
swept **Python only**. The estate actually measured distance four more
ways:

- `apps/dashboards/js/map.js` — IUGG radius 6371.0088, **unclamped**
  (antipodal inputs → NaN), comment claiming it "mirrors geo.go";
- `apps/patient/js/geo.js` — same private copy;
- `services/registry-service/geo.go` — IUGG 6371.0088, unclamped;
- `services/dispatch-service/fleet.go` — r=6371.0 but **atan2-form** — a
  local preference, not the contract; bit-divergent from the asin form.

And in Python, the router's drift guard pinned the contract's
*constants* while `routing.haversine_km`'s *arithmetic* was unpinned —
a formula edit with untouched constants would have stayed green. The
guard pinned data, not behavior.

## Decision

1. **One contract across runtimes.** Every runtime computes spherical
   haversine at r=6371.0 with the sqrt argument clamped to 1.0, in the
   same operation order as `geolock.distance_km`. The web tier gets one
   canonical module, `apps/pwa/js/geodesy.js` (contract as data,
   range-checks naming the offender, clamp stated); `map.js` and
   `geo.js` delegate to it under their historic `haversineKm` names.
   Both Go services converge to the contract formula; the atan2 variant
   is retired — convergence beats local preference, and the clamp
   absorbs the antipodal rounding atan2 avoided.

2. **Conformance is pinned by suites, per runtime, honestly.**
   - JavaScript: `apps/pwa/tests/geodesy-conformance.test.mjs` reads the
     stamped `georuntimeproof.json` and asserts **bit-identity** (`===`)
     against its Python-computed vectors — CPython/glibc and V8 agree
     bit-for-bit on the five reference legs (empirically verified). The
     payload IS the vector source: tamper with a vector and proofindex
     *and* the conformance suite go red.
   - Go: `geo_test.go` / `fleet_test.go` pin each service's **exact
     outputs as literals** (Go's pure-Go libm is deterministic), and
     assert the stated envelope against the Python vectors: Go's
     fdlibm-derived libm diverges from CPython/glibc by ~1 ulp on some
     legs (measured worst: 2.3e-13 km — about a tenth of a micrometre at
     630 km). The envelope is data on the payload; nobody claims the
     silicon agrees.
   - Python: `routing.haversine_km` joins the geodesyproof matrix as the
     eighth site — the drift guard now pins **behavior** (bit-identity
     on five legs), not just constants. routing@1.2.0 also gains the
     clamp: it was the estate's last unclamped Python haversine.

3. **Censuses state their scope.** Every census now names its runtimes
   and its method on the artifact. An unscoped census is a claim, not a
   measurement; the wave-14 sweep's Python-only scope is stated on both
   geodesyproof and georuntimeproof, next to the misses waves 16/17
   closed.

## Consequences

- One radius, one formula, one clamp — Python, JavaScript, Go.
- Three independent gates pin the convergence: the Python matrix
  (bit-identity, 8 sites), the node suite (bit-identity against the
  stamped payload), the Go suites (pinned literals + stated envelope).
- The retired radii and the atan2 variant are disclosed as data
  (`supersedes` members, measured divergences), not scrubbed.

## Alternatives considered

- **Leave Go on atan2.** Refused: two formulas is two contracts; the
  estate's doctrine is one contract with stated envelopes, not local
  preferences.
- **Claim full cross-runtime bit-identity.** Refused by measurement:
  Go's libm diverges by ~1 ulp. An overclaim on a proof surface is a
  defect; the envelope is stated instead.
- **Generate the JS module from Python.** Deferred: the module is 40
  lines, the conformance suite pins it bit-for-bit, and a generator
  adds a build step the offline PWA does not need. If a third JS site
  ever appears, generation becomes the answer.
