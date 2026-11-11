"""demo_tamper.py — deterministic evidence seed for the tamper-gate board.

One fixed token, every gate exercised, reproducible byte-for-byte:
a genuine cover verifies GREEN (the control), then the attack surface
closes in plain sight — a tail tamper and a decode-identical signature
twin both die at the canonical-form gate, a mid-signature forgery dies
at the checksum, an unknown key and every garbage shape are RED with
named reasons, and the exhaustive tamper sweep + seeded garbage fuzz
report their counts with zero exceptions raised. The cross-module
fail-closed gates (qrtrust/assettag non-ASCII scans, the case-twin the
officer PWA now refuses) are shown as evidence too.

The token is minted with sign_claims over FIXED claims — never
purchase(), whose secrets.token_hex refs are deliberately unique.
Same doctrine as demo_deploy/vhpilot: a demo you cannot reproduce is
a story you cannot audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

from vhcover import (KeyRing, VHCCoverRegistry, VHCOVER_VERSION,
                     _b64u_dec, sign_claims)

DEMO_VERSION = "tamperproof-demo@1.0.0"

CLOCK = lambda: "2026-11-10T08:00:00Z"  # noqa: E731  fixed — determinism

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "tamperproof.json")

CV = "cv-vhc@1.0.0"
SECRET = "tamperproof-demo-secret"
CLAIMS = {"v": CV, "ref": "VHC-TAMPERPROOF", "kind": "cover", "vu": 100,
          "iss": "cv-vhc", "win": {"from": 10, "to": 49}}
DAY = 20
TAMPER_ALPHABET = "Ax0-_.=~"
B64U_ALPHABET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "abcdefghijklmnopqrstuvwxyz0123456789-_")


def _ring():
    ring = KeyRing()
    ring.register("k1", SECRET, active_from=0)
    return ring


def _reg():
    return VHCCoverRegistry(profile_version=CV, key_ring=_ring(),
                            issuer="cv-vhc")


def _token():
    return sign_claims(CLAIMS, key_ring=_ring(), key_id="k1", day=DAY)


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**payload, "sha256": digest}


def _verdict(reg, token):
    v = reg.verify(token, day=DAY)
    return {"verdict": v["verdict"], "reasons": v["reasons"],
            "officer_action": v["officer_action"]}


def _gates_section(token):
    reg = _reg()
    gates = []

    # 1. control — the genuine cover admits
    control = _verdict(reg, token)

    # 2. tail tamper — the classic photocopy edit; the final character
    #    of an unpadded segment carries spare bits, so this dies at the
    #    canonical-form gate before the checksum is even consulted
    tail = token[:-2] + ("xx" if not token.endswith("xx") else "yy")
    gates.append({"gate": "tail tamper",
                  "presented": "…" + tail[-8:],
                  "note": "the last two characters edited — caught as "
                          "non-canonical before the checksum",
                  **_verdict(reg, tail)})

    # 3. decode-identical twin — THE hole row 201 closed: the twin's
    #    signature decodes to the SAME 32 bytes; the string is not the
    #    issued string, so the contract says red
    sig_seg = token.split(".")[2]
    idx = B64U_ALPHABET.index(sig_seg[-1])
    twin = token[:-1] + B64U_ALPHABET[idx ^ 1]
    decoded_identical = _b64u_dec(twin.split(".")[2]) == _b64u_dec(sig_seg)
    gates.append({"gate": "decode-identical twin",
                  "presented": "…" + twin[-8:],
                  "note": "signature decodes to the IDENTICAL 32 bytes — "
                          "only the canonical-form gate sees it",
                  "decoded_bytes_identical": decoded_identical,
                  **_verdict(reg, twin)})

    # 4. mid-signature forgery — a fully-significant character flips;
    #    the string stays canonical, the checksum does the work
    pos = len(token) - 3
    mid = (token[:pos] + ("x" if token[pos] != "x" else "y")
           + token[pos + 1:])
    gates.append({"gate": "mid-signature forgery",
                  "presented": "…" + mid[-8:],
                  "note": "canonical string, wrong bytes — the HMAC "
                          "checksum itself refuses",
                  **_verdict(reg, mid)})

    # 5. unknown key — a token signed by a key this scheme never issued
    other = KeyRing()
    other.register("k9", "not-this-scheme", active_from=0)
    forged = sign_claims(CLAIMS, key_ring=other, key_id="k9", day=DAY)
    gates.append({"gate": "unknown key",
                  "presented": forged[:24] + "…",
                  "note": "well-formed, well-signed — by a key the scheme "
                          "never issued",
                  **_verdict(reg, forged)})

    # 6. garbage shapes — every one a red verdict, never a crash
    for label, garbage in (("empty scan", ""), ("no segments", "xyz"),
                           ("bad alphabet", "~!~.%"),
                           ("padding smuggle", token[:-4] + "====")):
        gates.append({"gate": f"garbage: {label}",
                      "presented": (garbage[:16] + "…") if garbage else "(empty)",
                      "note": "refused with named reasons — a bad QR is "
                              "a red verdict, not a crash",
                      **_verdict(reg, garbage)})

    return {"control": control, "gates": gates,
            "all_gates_red": all(g["verdict"] == "red" for g in gates),
            "control_green": control["verdict"] == "green"}


def _sweep_section(token):
    """The exhaustive contract: EVERY single-character tamper across the
    whole token is red, and verify() never raises."""
    reg = _reg()
    cases = red = raises = non_red = 0
    for pos in range(len(token)):
        for ch in TAMPER_ALPHABET:
            if ch == token[pos]:
                continue
            bad = token[:pos] + ch + token[pos + 1:]
            cases += 1
            try:
                v = reg.verify(bad, day=DAY)
            except Exception:
                raises += 1
                continue
            if v["verdict"] == "red" and v["reasons"]:
                red += 1
            else:
                non_red += 1
    rng = random.Random(20261110)
    pool = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789-_.=~!@# \n¡£")
    garbage_cases = garbage_red = garbage_raises = 0
    for _ in range(5000):
        n = rng.randrange(0, 400)
        s = "".join(rng.choice(pool) for _ in range(n))
        garbage_cases += 1
        try:
            v = reg.verify(s, day=DAY)
        except Exception:
            garbage_raises += 1
            continue
        if v["verdict"] == "red" and v["reasons"]:
            garbage_red += 1
    return {"token_length": len(token),
            "tamper": {"cases": cases, "red": red, "raises": raises,
                       "non_red": non_red},
            "garbage": {"cases": garbage_cases, "red": garbage_red,
                        "raises": garbage_raises}}


def _cross_module_section():
    """The fail-closed gates from the estate-wide sweep (row 202), shown
    as evidence: scanner-presented non-ASCII refuses with the module's
    own refusal type — never a TypeError at the gate."""
    here = os.path.dirname(__file__)
    evidence = []

    sys.path.insert(0, os.path.join(here, "..", "qrtrust"))
    import keyring as qrtrust_keyring
    body = "OHAST2.k2026.clinic-1.oxygen-plant.1761000000"
    secret = b"tamperproof-demo-secret-00"
    tag = body + "." + qrtrust_keyring.sign(secret, body)
    evil = body + ".é" + tag.rsplit(".", 1)[1][1:]
    try:
        qrtrust_keyring.verify_with_ring(evil, {"k2026": secret},
                                         "OHAST2", 3)
        refused = "NOT REFUSED — a finding, not a demo"
    except Exception as exc:  # the refusal type IS the evidence
        refused = type(exc).__name__
    evidence.append({"module": "qrtrust", "attack": "non-ASCII scan",
                     "refused_with": refused,
                     "fail_closed": refused == "TrustError"})

    # the case-twin the Python/Go engines always refused — and the
    # officer PWA now refuses too (normalization removed)
    twin = tag.rsplit(".", 1)[0] + "." + tag.rsplit(".", 1)[1].upper()
    try:
        qrtrust_keyring.verify_with_ring(twin, {"k2026": secret},
                                         "OHAST2", 3)
        refused = "NOT REFUSED — a finding, not a demo"
    except Exception as exc:
        refused = type(exc).__name__
    evidence.append({"module": "qrtrust", "attack": "case-twin signature",
                     "refused_with": refused,
                     "fail_closed": refused == "TrustError"})

    sys.path.insert(0, os.path.join(here, "..", "assettag"))
    import assettag
    atag = assettag.mint("clinic-1", "oxygen-plant", secret,
                         issued=1761000000)
    aevil = atag.rsplit(".", 1)[0] + ".é234567890abcdef"
    try:
        assettag.verify(aevil, secret, now=1761000100)
        refused = "NOT REFUSED — a finding, not a demo"
    except Exception as exc:
        refused = type(exc).__name__
    evidence.append({"module": "assettag", "attack": "non-ASCII scan",
                     "refused_with": refused,
                     "fail_closed": refused == "TagError"})

    return {"evidence": evidence,
            "all_fail_closed": all(e["fail_closed"] for e in evidence)}


def run_demo():
    token = _token()
    payload = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK(),
        "gate": _gates_section(token),
        "sweep": _sweep_section(token),
        "cross_module": _cross_module_section(),
        "versions": {"vhcover": VHCOVER_VERSION},
    }
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    payloadio.write_text(out, json.dumps(payload, indent=1) + "\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
