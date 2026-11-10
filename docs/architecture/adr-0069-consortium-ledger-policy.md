# ADR-0069: Consortium-ledger policy — when a blockchain earns its place

Date: 2026-11-10 · Status: accepted

## Context

The question "should this platform use blockchain (e.g. Hyperledger Fabric)?"
recurs. The estate already answers the single-operator half of it
(ADR-0018, `docs/fraud-and-integrity.md`): the audit chain is a
tamper-evident hash-chained file, Merkle-anchored, verifiable by a
100-line Python tool — boring gets verified. Every evidence payload
carries a sha256 stamp, and `packages/proofindex` re-verifies all of
them from one page.

But `packages/auditlake` names the residual gap in its own comments:
an attacker with write access could rewrite the WHOLE chain and
recompute every link. Merkle anchoring closes that only when the root
is committed "off-box — a root you no longer control the history of".
Today the anchors file sits on the same box; off-box publication is
manual and aspirational.

## Analysis — where a ledger adds nothing

A blockchain operated by the same party that operates the database
convinces no one: the auditor trusts the operator's key either way.
Inside one administrative domain (one payer, one ministry deployment,
one platform operator) the existing hash chains + anchors + proofindex
already deliver tamper-evidence at a fraction of the operational cost.
Fabric there is a database with extra steps. The standing doctrine —
**no blockchain for single-operator trust** — is reaffirmed.

## Where a ledger genuinely earns its place

A permissioned ledger (Hyperledger Fabric class) earns its operational
cost only when ALL of these hold:

1. **Multiple sovereign organizations** share state (not one org with
   many departments),
2. **mutual distrust** — no party accepts another's database as
   authoritative,
3. **no natural central operator** all parties accept.

On this platform exactly two scenarios meet the bar today:

- **ECOWAS roaming cover and settlement (`vhroam`/`vhsettle`)** — the
  strongest fit. A traveller covered in one state is treated in
  another: two national payers, two regulators, and the platform
  operator cannot be the arbiter of sovereign schemes. A Fabric
  channel per corridor with payer endorsement policies and private
  data collections for claim detail is the right shape — when the
  consortium actually stands up.
- **Pharmaceutical provenance (`coldchain`/`stockcheck` + a national
  regulator)** — conditional. The canonical Fabric use case, real only
  if manufacturers, distributors and the regulator run peers.

Everything else — claims audit, eMAR, consent, fund ledgers — stays on
hash chains. That is not a gap; it is the design.

## Decision

1. **Never embed a ledger.** Fabric is a Go/Java stack; embedding
   violates ADR-0005 (stdlib-only packages) and the compose-don't-embed
   licensing doctrine. Any future ledger is an EXTERNAL network.
2. **Build the platform's side of the wire now**: `packages/ledgeranchor`
   (ledgeranchor@1.0.0) — a narrow external-notary seam that publishes
   auditlake Merkle roots off-platform and verifies against them.
   - Backends are a registry, vhpsp doctrine: `file-publication@1.0.0`
     (the formalized off-box practice) is ratified;
     `fabric-gateway@1.0.0` ships as a TEMPLATE that refuses to operate
     until two distinct named approvers ratify it, and afterwards still
     requires an injected transport — a missing wire is stated, never
     faked.
   - Verification is fail-closed: "unanchored" is a stated state, a
     root mismatch after a whole-chain rewrite is RED
     "history-rewritten", and the checksum-is-the-contract rule applies
     to roots and receipts (64 lowercase hex, or a named refusal;
     compare_digest is guarded against non-ASCII before comparison).
3. **Honesty boundary**: wire-conformance of the Fabric template is
   proven against an injected fake transport in tests and the demo.
   Certification against a live Fabric network is outstanding and needs
   a real consortium, not code.

## Consequences

- The whole-chain-rewrite attack (auditlake's own stated residual) is
  defeated TODAY by the file-publication backend; the demo proves the
  attack passes local verification and is caught externally.
- When an ECOWAS corridor consortium (or a pharma-provenance network)
  stands up, integration is a ratification act plus a transport
  adapter — no engine changes.
- No new runtime dependency; stdlib only.
