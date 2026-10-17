# Contributing

OpenHealth OS is digital public infrastructure — contributions are welcome and governance is deliberately boring.

## Ground rules

1. **Offline-first is non-negotiable.** Any change that adds a hard network dependency to a clinical, enrolment or claims workflow will be rejected. Degrade gracefully or don't ship.
2. **Compose, don't fork.** Prefer upstream open-source components. Re-implement only on licence or hot-path performance grounds, and record the decision in an ADR (`docs/architecture/`).
3. **Licence hygiene.** New dependencies must satisfy [LICENSING.md](LICENSING.md). BSL/SSPL/Elastic-licensed code is never embedded.
4. **AI writes are gated.** Any model/agent write into a health record goes through the Approval-Gated Agent Writes protocol (ADR-0004). No exceptions.
5. **Tests are the contract.** Every seam has a conformance test. CI runs: Go tests (all workspace modules), Rust tests, Python unit tests, and FHIR profile JSON validation.

## How to contribute

1. Fork, branch from `main`, keep PRs small and single-purpose.
2. Run the full local suite before pushing:
   ```bash
   for m in claims-orchestrator payment-switch-mock registry-service sync-agent; do
     (cd "services/$m" && go vet ./... && go test ./...)
   done
   cargo test --manifest-path services/ledger/Cargo.toml
   cargo test --manifest-path services/telemetry-collector/Cargo.toml
   python3 -m unittest discover -s packages/adjudicator
   python3 -m unittest discover -s packages/triage
   python3 -m unittest discover -s packages/dhis2-bridge
   ```
3. End-to-end smoke test: `./demo/demo.sh` must pass.
4. Commit messages: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`…).
5. Sign-off: we use the Developer Certificate of Origin (`git commit -s`).

## Where to start

- `good first issue` labels — typically documentation, test coverage, terminology mappings.
- FHIR profile work happens in `fhir/` against the published IG.
- Field feedback (deployment realities, failure modes) is as valuable as code — file it as an issue with the `field-report` label.

## Code of conduct

We follow the [Contributor Covenant](https://www.contributor-covenant.org/) v2.1. Report concerns to conduct@openhealthos.org.
