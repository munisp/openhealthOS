"""shipgate@1.0.0 — production readiness as data, never as adjectives.

The question "is this production ready?" has exactly one honest shape
on this platform: per capability, with evidence, with named remainders.
Not a percentage. Not a vibe. A gate.

Doctrines enforced here:

- A claim without evidence is marketing. A capability whose evidence
  list is EMPTY assesses as "unsubstantiated" — red, always. The gate
  cannot be greened by adjectives.
- A remainder is owned or it is a defect. Every outstanding item
  carries "needs" (what closes it) and "owner" (platform / operations /
  partner / regulator). An outstanding item missing either is a
  malformed claim and the whole assessment REFUSES — fail closed; a
  bad entry stops the report, it is never skipped.
- Evidence kinds are a closed vocabulary, pinned as data:
  test-suite, storm, wire-conformance, determinism, external-review.
  An unknown kind is refused at assessment time.
- The verdict vocabulary is three words and no more:
    proven            — evidence exists, no outstanding items   (green)
    stated-remainder  — evidence exists, remainders NAMED       (amber)
    unsubstantiated   — no evidence                             (red)
  There is deliberately no "production ready" verdict. Readiness is a
  property of evidence, and the strongest thing this gate ever says
  about code is "proven".
- The word "demo" on an evidence surface refers to the DATA (synthetic,
  deterministic, reproducible), never the machinery: every board
  exercises the production code paths. Real patient data must never
  appear on a screenshot-verifiable page.

Stdlib only (ADR-0005). No clocks are read here.
"""

from __future__ import annotations

SHIPGATE_VERSION = "shipgate@1.0.0"

#: The only evidence kinds the gate recognises. Closed on purpose.
EVIDENCE_KINDS = ("test-suite", "storm", "wire-conformance",
                  "determinism", "external-review")

#: The only owners a remainder may name. If none of these can close
#: it, it is not a remainder — it is an unowned risk, and the entry
#: refuses to load.
OWNERS = ("platform", "operations", "partner", "regulator")


class ShipgateError(ValueError):
    """A malformed capability claim. Fail closed, always named."""


#: The capability register — entries, not adjectives. Each entry:
#:   id, title, layer (where the machinery lives),
#:   evidence: [{"kind", "ref"}]   — what proves it, with a pointer
#:   outstanding: [{"item", "needs", "owner"}] — what is NOT closed
CAPABILITIES = [
    {"id": "audit-chain",
     "title": "Audit chain + Merkle anchors",
     "layer": "packages/auditlake",
     "evidence": [
         {"kind": "test-suite", "ref": "packages/auditlake tests"},
         {"kind": "determinism",
          "ref": "anchorproof demo — byte-identical reruns"},
     ],
     "outstanding": []},
    {"id": "external-notary",
     "title": "External root publication (anti whole-chain-rewrite)",
     "layer": "packages/ledgeranchor",
     "evidence": [
         {"kind": "test-suite", "ref": "25 ledgeranchor tests"},
         {"kind": "determinism",
          "ref": "anchorproof-demo — history-rewritten demonstrated"},
     ],
     "outstanding": [
         {"item": "fabric-gateway consortium backend",
          "needs": "a real consortium network with sovereign peers "
                   "(ADR-0069: ECOWAS corridor is the qualifying case)",
          "owner": "partner"},
     ]},
    {"id": "offline-qr-verify",
     "title": "Offline cover verification (QR + canonical gate)",
     "layer": "packages/travelpass vhcover + qrtrust",
     "evidence": [
         {"kind": "test-suite",
          "ref": "exhaustive single-char tamper sweep + garbage sweeps"},
         {"kind": "determinism",
          "ref": "tamperproof demo — byte-identical reruns"},
     ],
     "outstanding": []},
    {"id": "proof-index",
     "title": "Evidence self-verification (the meta layer)",
     "layer": "packages/proofindex",
     "evidence": [
         {"kind": "test-suite", "ref": "22 proofindex tests"},
         {"kind": "determinism",
          "ref": "12-exporter byte-determinism meta gate"},
     ],
     "outstanding": []},
    {"id": "psp-settlement",
     "title": "PSP settlement file adapters",
     "layer": "packages/travelpass vhpsp",
     "evidence": [
         {"kind": "wire-conformance",
          "ref": "Paystack/Flutterwave/Stripe/M-Pesa export shapes "
                 "as four-eyes-ratified template adapters"},
         {"kind": "determinism", "ref": "deployproof demo"},
     ],
     "outstanding": [
         {"item": "live certification",
          "needs": "real settlement exports from each PSP parsed in "
                   "production",
          "owner": "partner"},
     ]},
    {"id": "hsm-custody",
     "title": "Signing-key custody (PKCS#11)",
     "layer": "packages/vhpkcs11 + pkcs11_provider",
     "evidence": [
         {"kind": "wire-conformance",
          "ref": "Cryptoki v2.40 ABI proven against compiled mock "
                 "through the provider and keyring"},
     ],
     "outstanding": [
         {"item": "device certification",
          "needs": "a real HSM device on the production host",
          "owner": "operations"},
     ]},
    {"id": "ease-rail",
     "title": "EASE production rail",
     "layer": "packages/travelpass vhease",
     "evidence": [
         {"kind": "test-suite",
          "ref": "SqliteOutbox persist-before-send + restart replay "
                 "under the original idempotency key"},
         {"kind": "determinism", "ref": "deployproof demo"},
     ],
     "outstanding": [
         {"item": "production endpoint certification",
          "needs": "the operator's live EASE endpoint accepting "
                   "submissions",
          "owner": "operations"},
     ]},
    {"id": "erp-bridges",
     "title": "ERP bridges (ERPNext + Odoo)",
     "layer": "packages/erpnext-bridge + odoo-bridge",
     "evidence": [
         {"kind": "wire-conformance",
          "ref": "real wire protocols against in-process fakes"},
         {"kind": "storm",
          "ref": "erpstorm: 10k events, offline window, replay — "
                 "exactly-once, order preserved"},
     ],
     "outstanding": [
         {"item": "live server certification",
          "needs": "a real ERPNext/Odoo instance on the customer's "
                   "infrastructure",
          "owner": "operations"},
     ]},
    {"id": "sync-engine",
     "title": "Offline-first sync engine",
     "layer": "packages/syncmesh + syncgate.js",
     "evidence": [
         {"kind": "storm",
          "ref": "syncstorm: 1M mutations / 2,000 devices — "
                 "exactly-once, zero lost verdicts"},
         {"kind": "test-suite", "ref": "77 syncmesh tests"},
     ],
     "outstanding": [
         {"item": "managed sync service",
          "needs": "a PowerSync (or self-hosted equivalent) service "
                   "deployment",
          "owner": "operations"},
     ]},
    {"id": "whatsapp-lane",
     "title": "WhatsApp engagement lane (howUdeyChat)",
     "layer": "howUdeyChat wagateway@1.1.0",
     "evidence": [
         {"kind": "test-suite",
          "ref": "webhook dedupe, status monotonicity, template "
                 "registry, messaging-tier caps"},
     ],
     "outstanding": [
         {"item": "Meta Business approval",
          "needs": "approved business account + production number",
          "owner": "operations"},
     ]},
    {"id": "ussd-lane",
     "title": "USSD engagement lane",
     "layer": "howUdeyChat ussdwire",
     "evidence": [
         {"kind": "test-suite",
          "ref": "session state machine + delivery ladder tests"},
     ],
     "outstanding": [
         {"item": "aggregator contract",
          "needs": "a signed aggregator (e.g. Africa's Talking) "
                   "shortcode in each market",
          "owner": "partner"},
     ]},
    {"id": "agent-write",
     "title": "Agent write path (AI-assisted charting)",
     "layer": "packages/agentwrite + writegate + fhirguard",
     "evidence": [
         {"kind": "test-suite",
          "ref": "scripted-reviewer conformance harness incl. "
                 "broken-gate negative test"},
         {"kind": "determinism", "ref": "agentwrite demo"},
     ],
     "outstanding": []},
    {"id": "regulatory",
     "title": "Regulatory clearance",
     "layer": "outside the codebase — stated, never implied",
     "evidence": [
         {"kind": "external-review",
          "ref": "none yet — this row exists so absence is stated"},
     ],
     "outstanding": [
         {"item": "health-authority review",
          "needs": "ministry / NAFDAC-class review of clinical "
                   "content and data protection posture",
          "owner": "regulator"},
         {"item": "data-protection registration",
          "needs": "NDPC-class registration per operating market",
          "owner": "regulator"},
     ]},
]

_SEVERITY = {"proven": "green", "stated-remainder": "amber",
             "unsubstantiated": "red"}


def validate_entry(entry: dict) -> None:
    """Fail closed on a malformed claim. Raises ShipgateError with the
    named reason; a bad entry stops the assessment, never skips."""
    for field in ("id", "title", "layer", "evidence", "outstanding"):
        if field not in entry:
            raise ShipgateError("capability claim missing field: "
                                + field)
    if not isinstance(entry["id"], str) or not entry["id"]:
        raise ShipgateError("capability id must be a non-empty string")
    for ev in entry["evidence"]:
        if ev.get("kind") not in EVIDENCE_KINDS:
            raise ShipgateError(
                "unknown evidence kind %r on %s — the vocabulary is "
                "closed" % (ev.get("kind"), entry["id"]))
        if not isinstance(ev.get("ref"), str) or not ev["ref"]:
            raise ShipgateError("evidence on %s lacks a ref — a proof "
                                "you cannot point at is not a proof"
                                % entry["id"])
    for out in entry["outstanding"]:
        if not isinstance(out.get("item"), str) or not out["item"]:
            raise ShipgateError("outstanding item on %s is unnamed"
                                % entry["id"])
        if not isinstance(out.get("needs"), str) or not out["needs"]:
            raise ShipgateError("outstanding %r on %s has no 'needs' — "
                                "an unclosable remainder is a defect"
                                % (out.get("item"), entry["id"]))
        if out.get("owner") not in OWNERS:
            raise ShipgateError("outstanding %r on %s has no named "
                                "owner — an unowned remainder is a "
                                "defect" % (out.get("item"),
                                            entry["id"]))


def verdict_of(entry: dict) -> str:
    """Three words, no more. No evidence → unsubstantiated (red),
    regardless of how impressive the title sounds."""
    if not entry["evidence"]:
        return "unsubstantiated"
    if entry["outstanding"]:
        return "stated-remainder"
    return "proven"


def assess() -> dict:
    """Assess the whole register. Validates every entry first — one
    malformed claim refuses the report."""
    ids = set()
    for entry in CAPABILITIES:
        validate_entry(entry)
        if entry["id"] in ids:
            raise ShipgateError("duplicate capability id: "
                                + entry["id"])
        ids.add(entry["id"])
    rows = []
    for entry in CAPABILITIES:
        verdict = verdict_of(entry)
        rows.append({
            "id": entry["id"], "title": entry["title"],
            "layer": entry["layer"], "verdict": verdict,
            "severity": _SEVERITY[verdict],
            "evidence": entry["evidence"],
            "outstanding": entry["outstanding"],
        })
    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in _SEVERITY}
    return {
        "version": SHIPGATE_VERSION,
        "capabilities": rows,
        "summary": {
            "total": len(rows),
            "proven": counts["proven"],
            "stated_remainder": counts["stated-remainder"],
            "unsubstantiated": counts["unsubstantiated"],
            "all_substantiated": counts["unsubstantiated"] == 0,
            "all_remainders_owned": all(
                o.get("owner") in OWNERS and o.get("needs")
                for r in rows for o in r["outstanding"]),
        },
    }
