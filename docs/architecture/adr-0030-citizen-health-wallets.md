# ADR-0030: Citizen health wallets — purpose-restricted money on the patient's phone

Status: accepted (implemented)
Deciders: platform architecture

## Context

Two funding questions arrive together:

1. **Government**: can the state fund citizens directly (BHCPF-style
   subsidies, emergency funds) into wallets that can *only* pay for
   healthcare — not be cashed out, diverted, or spent on airtime?
2. **Private givers**: can a diaspora relative, a friend, or an employer
   gift money into someone's health wallet — with the same guarantee
   that the gift pays for care and nothing else?

The wallet-service (ADR-0003/0013) already had facility wallets and
member premium accounts, but no citizen-facing wallet and no
purpose-restriction. Nothing about the ledger enforces policy — the
ledger is a faithful bookkeeper, not a gatekeeper.

## Decision

Citizen health wallets on the existing ledger seam, with the
restriction enforced at wallet-service as an **allow-list** (never a
deny-list — same doctrine as the ADR-0028 de-ID keep-list).

### Accounts

| Account | Purpose |
|---|---|
| `CITIZEN-<nin>` | general health wallet |
| `CITIZEN-<nin>:<ENVELOPE>` | sub-programme envelope (MATERNAL, UNDER5, EMERGENCY, NCD) |
| `GOV-PROGRAMME-<name>` | treasury-funded subsidy source |
| `GIFTS-IN` | PSP/remittance settlement for private gifts |

### The allow-list

A citizen wallet may pay only: `WALLET-*` (facilities), `PHARM-*`
(pharmacies), `NEMTC-*` (ambulance), `TELEMED-*` (telehealth). Anything
else — airtime, P2P transfer to another citizen, betting — is refused
with 403 **before the ledger is asked**, so refused attempts are
themselves part of the audit story. Envelope policies narrow the
allow-list further: a MATERNAL grant pays facilities only; UNDER5 adds
pharmacies; EMERGENCY adds the ambulance fleet.

### Funding sources — government and private

- **Government** (`FundCitizen`): programme account is topped up from
  `TREASURY-FLOAT` (CBN/FAAC disbursement seam) and posts to the wallet.
  Expiry is supported; expired unspent funds sweep back to the
  programme (treasury operation, actor required).
- **Private gift** (`GiftCitizen`): the giver pays by card, bank, or
  remittance; the PSP settles naira into `PSP-FLOAT` (the same seam as
  premium micro-collections), the gift settles into `GIFTS-IN`, then the
  wallet. The giver never needs a ledger account; attribution rides in
  the tx id (`GIFT-<giver>-<ref>`). **A gift inherits the allow-list** —
  that is the product: diaspora money that provably buys care.

Every movement is `PostOnce`-idempotent with a mandatory caller ref:
a retried disbursement, gift, or payment can never post twice.

### Channels

- **PWA** (patient app) — balance and payments with data.
- **USSD** (`ussd-gateway` menu option 4) — NIN → balance → pay a
  facility short-code → naira amount; deterministic ref per session
  makes a redial safe. Refusals read "Your money is safe."
- **Offline** — PWA spend requests queue in the outbox and settle on
  sync; the ledger dedupes.

## Consequences

- The state can prove subsidy money reached citizens and was spent only
  on care — per kobo, from the ledger journal.
- Diaspora gifting becomes a funding rail with a built-in guarantee;
  KYC/AML obligations sit at the PSP/remittance boundary, not in the
  platform.
- Restriction is service-layer policy, not ledger law: a future
  programme with broader permissions adds an envelope policy, not a
  fork.
- Known limit: the envelope registry (programme, expiry) is in-memory
  in this pilot; the state plane moves it to Postgres (same table
  shape), as with idempotency records.
