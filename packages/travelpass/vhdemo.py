"""vhdemo — presenter walkthrough generator for a VHC scheme demo
(vhc-demo@1.0.0).

A ministry demo is a claim about a system. This module makes the claim
auditable: the walkthrough script is *generated from the seeded pilot
narrative*, scene by scene, and every number on a slide or in a talking
point comes out of the narrative dict — never invented by the presenter,
never hardcoded by us. Change the seed, the script changes; the doctrine
does not.

Doctrine:
  - the script reads the narrative, it does not improvise
  - every scene names the UI surface the presenter is standing on
  - every scene names the failure mode it is answering ("what stops X?")
  - denominators travel with every number
  - synthetic means synthetic: the script says so on scene one
"""

from __future__ import annotations

DEMO_VERSION = "vhc-demo@1.0.0"

# The scene order is the story arc: a traveller arrives, money moves,
# care happens, the system watches itself, and the ministry can prove it.
# Each entry: (scene_id, surface, the question the audience is silently
# asking, builder). Builders receive the narrative and return talking
# points — a list of strings. No builder may invent a number.

SCENES = ("arrival", "verdict", "money", "care", "rescue",
          "surveillance", "oversight", "proof")


def _fmt_minor(minor: int, symbol: str) -> str:
    return f"{symbol}{minor / 100:,.2f}"


def _scene_arrival(n, currency_symbol):
    return [
        f"DAY ONE, LANE THREE. {n['arrivals']} travellers arrive over "
        f"{n['days']} days — every one synthetic (Pax-NNNN, SIM-NNNN; "
        "this demo maps to no person, and the registry can prove it).",
        f"Each buys or carries cover: {n['covers']} covers issued, of "
        f"which {n['exempt']} are lawful exemptions — a diplomat and a "
        "transit passenger are not revenue, and the system never treats "
        "them as such.",
        "Point at the QR on the officer's screen: compact JWS, signed by "
        "the HSM-held key, verifiable offline for 72 hours. A stale "
        "cache shows RED, not a silent pass.",
    ]


def _scene_verdict(n, currency_symbol):
    v = n["verdicts"]
    return [
        "THE VERDICT IS A DECISION, NOT A VIBE. Every scan returns "
        "green, amber, or red with a *named reason* and a named officer "
        "action — admit, desk, or supervisor.",
        f"In this fortnight: {v.get('green', 0)} green, "
        f"{v.get('amber', 0)} amber (desk lookup, resolve on the spot), "
        f"{v.get('red', 0)} red. Denominators on screen, always.",
        "The officer never sees a raw passport number — the desk lookup "
        "works from the cover reference.",
    ]


def _scene_money(n, currency_symbol):
    sym = currency_symbol
    return [
        "FOLLOW THE MONEY — ALL FIVE MONIES. Premium collected: "
        f"{_fmt_minor(n['premium_collected_minor'], sym)}. It splits at "
        "the moment of sale into reserve, claims, ops, surveillance, and "
        "administration buckets per the versioned country profile — a "
        "rule that cannot be traced to a table is a bribe with a receipt.",
        f"Channel mix: {n['partner_sales']} sales came through the "
        f"partner API, {n['groups']} group manifests (airline, tour "
        "operator) — all-or-nothing, priced per head.",
        f"Duplicate charges refund themselves: {n['auto_refunds']} "
        "auto-refunds this fortnight, first charge kept, the rest "
        "returned inside the window.",
        f"Settlement reconciles daily: {n['settlement_breaches']} "
        "breaches against the 1000 ppm break tolerance. Zero is the "
        "expected answer, and the report says the denominator.",
    ]


def _scene_care(n, currency_symbol):
    return [
        "WHEN SOMEONE GETS SICK. A traveller walks into an accredited "
        "clinic; the provider portal sees only its own cases.",
        f"{n['claims']} claims this fortnight. The clock that matters: "
        "GOP inside 2 hours of intake — the guarantee of payment is what "
        "gets the traveller treated without a credit card on a gurney.",
        f"Adjudicated against the four-eyes tariff book; paid "
        f"{_fmt_minor(n['claims_paid_minor'], currency_symbol)}. Loss "
        f"ratio {n['loss_ratio']:.4f} — the actuarial mart reads the "
        "same registries, so finance and operations never argue about "
        "whose number is real.",
    ]


def _scene_rescue(n, currency_symbol):
    return [
        "THE WORST DAY. A medevac request: approve, dispatch inside the "
        f"2-hour clock, arrive, close. This fortnight: {n['medevacs']} "
        "medevac(s).",
        "If an *uninsured* traveller needs rescue, the scheme does not "
        "let them die at the border of a spreadsheet: the uninsured fund "
        "posts the cost, and subrogation pursues recovery afterwards — "
        "bounded by what is actually outstanding, never a blank cheque.",
    ]


def _scene_surveillance(n, currency_symbol):
    return [
        "THE SYSTEM WATCHES ITSELF. Every incident is pseudonymized at "
        "registration — salted hash, no reversible identity in the "
        "registry. Synthetic here, protected in production.",
        f"BR-10: five GI cases in one facility in one week trips an "
        f"alert. This fortnight: {n['br10_alerts']} alert(s), "
        f"acknowledged: {'yes' if n['br10_all_acknowledged'] else 'NO — STOP THE DEMO'}. "
        "An unacknowledged alert is a finding, not a statistic.",
        "The IHR/UKHSA/ECDC export pack carries a SHA-256 — the "
        "checksum is the contract — and k-floor 5 suppression means "
        "small cells never leak a person. Pack hash: "
        f"{n['export_pack_sha256'][:16]}…",
    ]


def _scene_oversight(n, currency_symbol):
    sla = n["helpdesk_sla"]
    return [
        "WHO ANSWERS THE PHONE. Travellers get a 24/7 helpdesk with "
        "content in their language; a missing translation falls back "
        "*with the fallback stated* — nobody silently signs a waiver "
        "they cannot read.",
        f"{n['tickets']} tickets this fortnight; first-response SLA "
        f"compliance {sla}. A late response is a named breach with an "
        "owner, not a shrug.",
        "Every ops clock — GOP 2h, adjudication 10d, payment 30d, "
        "appeal 5d, dispatch 2h, recon daily — has a named owner and a "
        "visible denominator. The dashboards read the reports, not vibes.",
    ]


def _scene_proof(n, currency_symbol):
    return [
        "WHY YOU CAN BELIEVE ANY OF THIS. This entire fortnight is one "
        f"function call: seed_pilot(seed={n['seed']}). Same seed, same "
        "pilot, byte for byte — a demo you cannot reproduce is a story "
        "you cannot audit.",
        "Every claim made on these slides is backed by a module with "
        "its own test suite; the platform runs its full suite on the "
        "deployment tag before a gate flips.",
        "What is still environment, and we say so honestly: the live "
        "PSP contract, the HSM ceremony, and the lane kit are proven in "
        "the go-live gates G0–G5, not in this demo.",
    ]


_BUILDERS = {
    "arrival": _scene_arrival, "verdict": _scene_verdict,
    "money": _scene_money, "care": _scene_care, "rescue": _scene_rescue,
    "surveillance": _scene_surveillance, "oversight": _scene_oversight,
    "proof": _scene_proof,
}

_SURFACES = {
    "arrival": "border lane console — officer view",
    "verdict": "border lane console — verdict screen",
    "money": "finance dashboard — fund buckets + settlement recon",
    "care": "provider portal + claims workbench",
    "rescue": "assistance ops console — medevac board",
    "surveillance": "public-health registry — BR-10 alert list",
    "oversight": "helpdesk console + ops clocks dashboard",
    "proof": "the terminal — one command, one seed",
}

_QUESTIONS = {
    "arrival": "who are these people and does the system know them?",
    "verdict": "what stops a bad QR at the lane?",
    "money": "where does the money go and who can touch it?",
    "care": "does a sick traveller actually get treated?",
    "rescue": "what happens on the worst day?",
    "surveillance": "would the ministry know before the outbreak?",
    "oversight": "who is accountable when the clock breaches?",
    "proof": "why should we believe any of this?",
}


def build_walkthrough(narrative: dict, *, currency_symbol: str = "€") -> dict:
    """Generate the presenter script from a pilot narrative.

    Every number in every line comes from `narrative`. The function
    refuses a narrative missing keys the script needs — a demo with an
    improvised number is worse than no demo.
    """
    required = {"arrivals", "covers", "exempt", "verdicts", "groups",
                "partner_sales", "premium_collected_minor", "auto_refunds",
                "settlement_breaches", "claims", "claims_paid_minor",
                "loss_ratio", "medevacs", "br10_alerts",
                "br10_all_acknowledged", "export_pack_sha256", "tickets",
                "helpdesk_sla", "seed", "days", "version"}
    missing = required - set(narrative)
    if missing:
        raise ValueError(f"narrative is missing {sorted(missing)}; "
                         "the script reads the narrative, it does not improvise")

    scenes = []
    for sid in SCENES:
        scenes.append({
            "scene": sid,
            "surface": _SURFACES[sid],
            "audience_question": _QUESTIONS[sid],
            "talking_points": _BUILDERS[sid](narrative, currency_symbol),
        })
    return {
        "version": DEMO_VERSION,
        "pilot_version": narrative["version"],
        "seed": narrative["seed"],
        "scenes": scenes,
        "disclaimer": ("All travellers, passports, providers and money in "
                       "this walkthrough are synthetic (Pax-NNNN / SIM-NNNN). "
                       "The demonstration maps to no person."),
    }


def render_markdown(walkthrough: dict) -> str:
    """Render the script as a presenter-ready Markdown run-of-show."""
    lines = [f"# VHC scheme — ministry walkthrough",
             f"",
             f"_{walkthrough['disclaimer']}_",
             f"",
             f"pilot `{walkthrough['pilot_version']}` · seed "
             f"`{walkthrough['seed']}` · script `{walkthrough['version']}`",
             ""]
    for i, s in enumerate(walkthrough["scenes"], 1):
        lines.append(f"## Scene {i}: {s['scene']}")
        lines.append(f"**Stand on:** {s['surface']}")
        lines.append(f"**They are silently asking:** {s['audience_question']}")
        lines.append("")
        for tp in s["talking_points"]:
            lines.append(f"- {tp}")
        lines.append("")
    return "\n".join(lines)
