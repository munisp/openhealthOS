# QR & barcode standards — OpenHealth OS (ADR-0055)

Every code the platform prints or displays follows these rules. They exist
because the scanning device is usually a $100 Android with a cracked screen
in a brightly lit ward — not a desk scanner.

## Payload budget

| | |
|---|---|
| Target | ≤ 800 bytes (low-density QR, scans first try) |
| Hard ceiling | 2000 bytes — minters REFUSE beyond this (qrtrust `check_budget`) |
| Beyond the ceiling | use a link-style QR (SHL model: URL + key in the code, payload fetched) |

## Error correction & rendering

- **EC level Q (25% damage tolerance)** — never L. Dirt, folds and cracked
  screens are the norm.
- Quiet zone ≥ 4 modules on all sides; no logos inside the code area.
- Printed tags: matte laminate (gloss glare defeats cameras in sunlight),
  minimum 25mm for ≤ 80-byte payloads, larger for more.
- Screen-displayed codes (PWA): minimum 240×240 px render, white
  background, no theme tinting behind the code.

## Signing doctrine

- Everything the platform *issues* is signed (member cards OHMEM1,
  presence codes OHPRS1, asset tags OHAST2, IPS OHIPS1). Commodity
  barcodes (GTIN on stock) are identity-only — unsigned by nature;
  robustness there comes from process (double-scan suppression,
  tolerances), not crypto.
- v2 payloads carry the **key id** as the second field; verification uses
  the qrtrust keyring. Rotation adds a key and flips OH_QR_ACTIVE — field
  tags keep verifying until revoked.
- Revocation is a JSONL of payload bodies (OH_QR_REVOKED), hard-reject.
- Verification is **offline** everywhere; a scan that needs the network
  is a failed design at a PHC.

## What a scan proves — and what it doesn't

- Signature proves the *code* is authentic. It does not prove the code is
  attached to the right physical object — that is what the custody spine
  and scan forensics (impossible travel, bursts, stale patterns) are for.
- An anomaly is a question for an auditor, never an automated accusation.
