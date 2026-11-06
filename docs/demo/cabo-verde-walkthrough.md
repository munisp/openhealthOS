# VHC scheme — cabo-verde walkthrough

_All travellers, passports, providers and money in this walkthrough are synthetic (Pax-NNNN / SIM-NNNN). The demonstration maps to no person._

pilot `vhc-pilot@1.0.0` · seed `42` · script `vhc-demo@1.0.0`

## Scene 1: arrival
**Stand on:** border lane console — officer view
**They are silently asking:** who are these people and does the system know them?
**Why this room cares:** Tourism is the economy: every arrival through Sal, Praia or São Vicente is covered revenue and a health obligation at the same counter.

- DAY ONE, LANE THREE. 328 travellers arrive over 14 days — every one synthetic (Pax-NNNN, SIM-NNNN; this demo maps to no person, and the registry can prove it).
- Each buys or carries cover: 328 covers issued, of which 2 are lawful exemptions — a diplomat and a transit passenger are not revenue, and the system never treats them as such.
- Point at the QR on the officer's screen: compact JWS, signed by the HSM-held key, verifiable offline for 72 hours. A stale cache shows RED, not a silent pass.

## Scene 2: verdict
**Stand on:** border lane console — verdict screen
**They are silently asking:** what stops a bad QR at the lane?
**Why this room cares:** The lane at Amílcar Cabral is the border: a QR that verifies offline for 72 hours survives the inter-island network better than any always-online system.

- THE VERDICT IS A DECISION, NOT A VIBE. Every scan returns green, amber, or red with a *named reason* and a named officer action — admit, desk, or supervisor.
- In this fortnight: 127 green, 0 amber (desk lookup, resolve on the spot), 0 red. Denominators on screen, always.
- The officer never sees a raw passport number — the desk lookup works from the cover reference.

## Scene 3: rescue
**Stand on:** assistance ops console — medevac board
**They are silently asking:** what happens on the worst day?
**Why this room cares:** On nine inhabited islands the worst day is an inter-island medevac — fixed-wing, rotary or boat — and the 2-hour dispatch clock is the promise that matters.

- THE WORST DAY. A medevac request: approve, dispatch inside the 2-hour clock, arrive, close. This fortnight: 1 medevac(s).
- If an *uninsured* traveller needs rescue, the scheme does not let them die at the border of a spreadsheet: the uninsured fund posts the cost, and subrogation pursues recovery afterwards — bounded by what is actually outstanding, never a blank cheque.

## Scene 4: money
**Stand on:** finance dashboard — fund buckets + settlement recon
**They are silently asking:** where does the money go and who can touch it?
**Why this room cares:** The treasury asks where the escudos sit: five buckets, a versioned split, and a settlement file that reconciles daily against the PSP.

- FOLLOW THE MONEY — ALL FIVE MONIES. Premium collected: 1.026.842$81. It splits at the moment of sale into reserve, claims, ops, surveillance, and administration buckets per the versioned country profile — a rule that cannot be traced to a table is a bribe with a receipt.
- Channel mix: 65 sales came through the partner API, 3 group manifests (airline, tour operator) — all-or-nothing, priced per head.
- Duplicate charges refund themselves: 0 auto-refunds this fortnight, first charge kept, the rest returned inside the window.
- Settlement reconciles daily: 0 breaches against the 1000 ppm break tolerance. Zero is the expected answer, and the report says the denominator.

## Scene 5: care
**Stand on:** provider portal + claims workbench
**They are silently asking:** does a sick traveller actually get treated?
**Why this room cares:** A visitor falls ill on Santo Antão; the GOP inside 2 hours is what gets them treated before the ferry decision.

- WHEN SOMEONE GETS SICK. A traveller walks into an accredited clinic; the provider portal sees only its own cases.
- 10 claims this fortnight. The clock that matters: GOP inside 2 hours of intake — the guarantee of payment is what gets the traveller treated without a credit card on a gurney.
- Adjudicated against the four-eyes tariff book; paid 77.185$50. Loss ratio 0.0723 — the actuarial mart reads the same registries, so finance and operations never argue about whose number is real.

## Scene 6: surveillance
**Stand on:** public-health registry — BR-10 alert list
**They are silently asking:** would the ministry know before the outbreak?
**Why this room cares:** Port health is national health: five GI cases in one facility in one week is an IHR-relevant signal with an export pack, not a rumour.

- THE SYSTEM WATCHES ITSELF. Every incident is pseudonymized at registration — salted hash, no reversible identity in the registry. Synthetic here, protected in production.
- BR-10: five GI cases in one facility in one week trips an alert. This fortnight: 1 alert(s), acknowledged: yes. An unacknowledged alert is a finding, not a statistic.
- The IHR/UKHSA/ECDC export pack carries a SHA-256 — the checksum is the contract — and k-floor 5 suppression means small cells never leak a person. Pack hash: 1a1f2fa3ce1bc77f…

## Scene 7: oversight
**Stand on:** helpdesk console + ops clocks dashboard
**They are silently asking:** who is accountable when the clock breaches?
**Why this room cares:** Visitors need help in PT, EN, FR, DE, NL — a missing translation falls back with the fallback stated, never silently.

- WHO ANSWERS THE PHONE. Travellers get a 24/7 helpdesk with content in their language; a missing translation falls back *with the fallback stated* — nobody silently signs a waiver they cannot read.
- 8 tickets this fortnight; first-response SLA compliance {'compliant': 8, 'responded': 8}. A late response is a named breach with an owner, not a shrug.
- Every ops clock — GOP 2h, adjudication 10d, payment 30d, appeal 5d, dispatch 2h, recon daily — has a named owner and a visible denominator. The dashboards read the reports, not vibes.

## Scene 8: proof
**Stand on:** the terminal — one command, one seed
**They are silently asking:** why should we believe any of this?
**Why this room cares:** The pilot fortnight reruns from one seed in front of the ministry — a demo you cannot reproduce is a story you cannot audit.

- WHY YOU CAN BELIEVE ANY OF THIS. This entire fortnight is one function call: seed_pilot(seed=42). Same seed, same pilot, byte for byte — a demo you cannot reproduce is a story you cannot audit.
- Every claim made on these slides is backed by a module with its own test suite; the platform runs its full suite on the deployment tag before a gate flips.
- What is still environment, and we say so honestly: the live PSP contract, the HSM ceremony, and the lane kit are proven in the go-live gates G0–G5, not in this demo.
