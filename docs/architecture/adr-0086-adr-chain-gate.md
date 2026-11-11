# ADR-0086: The ADR chain is governed data — one canonical headline, numbers never collide, gaps stated

- Status: accepted
- Date: 2026-11-11
- Wave: 22 (README row 221)

## Context

Wave-22 recon censused the decision chain itself and found it had
silently collided: the October-20 wave and the November-9/10 wave both
allocated ADR-0060, ADR-0061 and ADR-0062 to *different* decisions.
Every inbound reference — package docstrings, infra comments, README
rows 59–61 and 195–198, and ADR cross-references — was then ambiguous
about which decision it cited, and three README links were one rename
away from going dead. Nothing watched the chain: the estate gates its
payloads, its shells, its geodesy and its evidence freshness, but the
record of *why* was ungoverned.

The same recon found format drift: 4 files used an em-dash headline
(`# ADR-NNNN — title`) against 81 using the canonical colon form, and
14 files declared status through four different markdown decorations.

## Decision

1. **Renumber, never reuse.** The October decisions moved to
   ADR-0083 (registry lakehouse feed), ADR-0084 (third-party
   integration surface) and ADR-0085 (APISIX gateway); the November
   agent-write decisions keep 0060–0062 where the larger reference
   graph (five packages, three ADRs, memory) already points. All 14
   inbound reference sites updated in the same wave.

2. **One canonical headline**: `# ADR-NNNN: title`. The four em-dash
   files were normalised; a second variant is a defect, not a style.

3. **Status is a fact, decoration is not.** The gate accepts any
   markdown decoration around the `Status` token (plain, bold, bullet,
   heading); it does not accept *no status*.

4. **The chain is gated.** `packages/adrchain` audits the chain with
   injected tree readers: duplicate numbers are red with every claimant
   named; filename/headline disagreement is red; a missing headline is
   red; gaps are amber and *stated* (numbers may be retired, but never
   silently); a missing status is amber. `TestRealTree` pins the live
   tree: 86 files, 86 distinct numbers, no gaps, no duplicates.

5. **The gate audits the real tree at export** (adrproof demo), so an
   estate drift makes the freshness gate name the payload stale.

## Consequences

- The collision class is now unrepresentable without a red gate.
- Future waves allocate the next number by reading the chain, and the
  meta gate proves the chain has one.
- A retired number will appear as a stated amber gap — a visible,
  explainable hole, not a silent one.

## Alternatives considered

- *Renumber the November ADRs instead*: rejected — their inbound
  reference graph (agentwrite, writegate, absentia, fhircover,
  cdshooks, mcptools docstrings plus ADR-0062's builds-on chain) is
  larger than the October graph.
- *Teach the gate both headline variants*: rejected per the stamp
  contract doctrine — a second variant is a defect; normalise the
  files, not the gate.
- *Gaps as red*: rejected — retirement is legitimate; silence is the
  defect.
