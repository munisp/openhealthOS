# lis-bridge

Laboratory information system bridge — hub-and-spoke diagnostics.

Spoke PHCs collect samples; a courier chain moves them to a hub lab;
results return as FHIR R4 Observations (LOINC coding, UCUM units,
HL7 interpretation codes, reference ranges).

- **Chain of custody** — `ordered → collected → in-transit →
  received-at-hub → resulted → delivered`. Illegal transitions are
  rejected: samples do not teleport, and results cannot precede receipt.
- **Critical values** — results outside critical bounds are surfaced via
  `critical_results()` until delivered back to the spoke. A critical
  potassium in a queue is a patient-safety failure, so criticals are a
  call-the-facility-now list, not a database column.
- **Turnaround** — order-to-result hours per sample, the metric that
  decides whether hub-and-spoke beats referral for a given test.

```bash
python3 -m unittest -v
```
