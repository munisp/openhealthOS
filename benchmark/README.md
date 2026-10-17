# benchmark — Nigerian Million-Claim Challenge

Measures the adjudication pipeline against a national-scale workload:
one million claims is roughly a busy month across a state scheme.

The harness generates a **deterministic** synthetic claim stream (seeded;
the same seed always produces the same stream), runs it through the **real
adjudicator** — the same decision function the orchestrator shells out to
in production — and reports throughput, latency percentiles, decision
distribution, and correctness invariants.

## Truthful scoring

- No cached verdicts, no skipped rules — every number is measured wall
  time on the actual decision function.
- Correctness invariants are asserted, not assumed: every decision carries
  reason codes (no silent verdicts), every approval satisfies
  `payable = allowed - copay`, no zero-amount approvals.
- **Non-zero exit on any invariant violation** — a fast wrong engine is a
  failed benchmark, whatever the throughput says. The very first harness
  run caught a real bug this way: PEND verdicts carried their justification
  only in `flags`, leaving `reasons` empty; the adjudicator now copies
  flags into reasons for pended claims.

## Workload mix

80% clean claims, 20% adversarial (frequency-cap breaches, duplicates,
price drift, benefit violations, inactive members). A benchmark that is
half adversarial measures the wrong system; one with no adversarial cases
measures nothing.

## Measured baseline

Sandbox VM, Python 3.12, seed 42, 1,000,000 claims:

| Metric | Value |
| --- | --- |
| Wall time | 24.3 s |
| Throughput | ~41,000 claims/s |
| Latency p50 / p99 | 0.021 ms / 0.046 ms |
| Distribution | 800,349 APPROVE · 149,953 PEND · 49,698 DENY |
| Invariant violations | 0 |

## Usage

```bash
python3 million_claims.py --claims 1000000 --seed 42 --out report.json
python3 -m unittest -v   # harness self-tests
```
