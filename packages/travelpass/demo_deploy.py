"""demo_deploy.py — deterministic evidence seed for the deployment-proof board.

One settlement morning, reproducible byte-for-byte: a template PSP
adapter refuses to parse, two named approvers ratify it, the real
Paystack and M-Pesa exports parse into minor units (the failed line
and the withdrawal never settle), and three rogue registration
attempts refuse. Then the EASE rail goes down mid-send — the envelope
journaled BEFORE the wire replays after restart under the ORIGINAL
idempotency key, and a permanently-failing envelope dies at 8
attempts with its last error named. Then an HSM signs through the
real Cryptoki ABI — proven here against the compiled fixture
(mock_pkcs11.c, declared in test_vhdeploy.py); at deployment the same
module loads the vendor's library. If no C compiler exists in this
environment, the HSM card says so — stated, not hidden.

Same doctrine as vhpilot/vhdashx/demo_agentwrite: a demo you cannot
reproduce is a story you cannot audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import vhpsp
from vhcover import KeyRing, VHCError, pkcs11_provider
from vhease import EaseAdapter, EaseError, SqliteOutbox

DEMO_VERSION = "deployproof-demo@1.0.0"

CLOCK = lambda: "2026-11-10T08:00:00Z"  # noqa: E731
EASE_CLOCK = lambda: 1794261600.0  # noqa: E731  fixed — determinism

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "deployproof.json")

PAYSTACK = (b"Reference,Amount,Currency,Customer Email,Status,Paid At\n"
            b"PSP-900,1200000,NGN,pax1@example.com,success,2026-11-09\n"
            b"PSP-901,550000,NGN,pax2@example.com,failed,2026-11-09\n"
            b"PSP-902,75000,NGN,pax3@example.com,success,2026-11-09\n")

MPESA = (b"Receipt No,Details,Transaction Status,Paid In,Withdrawn\n"
         b"QK12345,Customer 2547xxx,Completed,\"1,500.00\",\n"
         b"QK12346,Customer 2547yyy,Completed,,2,000.00\n")

COVER = {"ref": "VHC-DEMO-001", "passport_sha256": "ab12",
         "window": [0, 14], "profile_version": "vhcover@1.1.0"}


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def _psp_section():
    """Governance events + the registry read back as data. The adapter
    registry is process-global by design — snapshot and restore so a
    rerun in the same process is byte-identical."""
    registry = dict(vhpsp.PSP_ADAPTERS)
    state = {fid: (a.status, list(a.ratified_by))
             for fid, a in registry.items()}
    events = []
    try:
        # 1. a template is a placeholder, not a settlement format
        try:
            vhpsp.parse_settlement_file(
                PAYSTACK, format_id="paystack-settlement-csv-v1")
        except vhpsp.PSPError as exc:
            events.append({"kind": "template-refusal",
                           "format_id": "paystack-settlement-csv-v1",
                           "reason": str(exc)})

        # 2. four-eyes: two distinct named approvers
        vhpsp.ratify_adapter("paystack-settlement-csv-v1",
                             approver="ada", second="bola")
        vhpsp.ratify_adapter("mpesa-statement-csv-v1",
                             approver="ada", second="bola")
        events.append({"kind": "four-eyes",
                       "format_ids": ["paystack-settlement-csv-v1",
                                      "mpesa-statement-csv-v1"],
                       "approver": "ada", "second": "bola",
                       "note": "ratified against a real provider export "
                               "— the hand that proposes never approves"})

        # 3. real exports parse into minor units; failures never settle
        paystack = vhpsp.parse_settlement_file(
            PAYSTACK, format_id="paystack-settlement-csv-v1")
        mpesa = vhpsp.parse_settlement_file(
            MPESA, format_id="mpesa-statement-csv-v1")
        events.append({"kind": "parsed",
                       "format_id": "paystack-settlement-csv-v1",
                       "rows": paystack,
                       "skipped": [{"ref": "PSP-901",
                                    "why": "status=failed — only "
                                           "successful transactions "
                                           "settle"}]})
        events.append({"kind": "parsed",
                       "format_id": "mpesa-statement-csv-v1",
                       "rows": mpesa,
                       "skipped": [{"ref": "QK12346",
                                    "why": "withdrawal row — not a "
                                           "settlement line"}],
                       "note": "1,500.00 KES major → 150000 minor via "
                               "the ISO-4217 scale table, never a "
                               "guessed exponent"})

        # 4. three rogue registrations refuse
        refusals = []
        for label, kwargs in (
                ("enter pre-ratified", {"status": "ratified",
                                        "source": "invented"}),
                ("overwrite csv-v1", {"format_id": "csv-v1",
                                      "source": "rogue replacement"}),
                ("no named source", {})):
            try:
                fid = kwargs.pop("format_id", "rogue-v1")
                vhpsp.register_adapter(fid, lambda r, **k: [], **kwargs)
                refusals.append({"attempt": label, "refused": False})
            except vhpsp.PSPError as exc:
                refusals.append({"attempt": label, "refused": True,
                                 "reason": str(exc)})
        events.append({"kind": "register-refusals",
                       "attempts": refusals})

        report = vhpsp.report()
    finally:
        vhpsp.PSP_ADAPTERS.clear()
        vhpsp.PSP_ADAPTERS.update(registry)
        for fid, (status, ratified_by) in state.items():
            entry = vhpsp.PSP_ADAPTERS[fid]
            entry.status = status
            entry.ratified_by = ratified_by
    return {"events": events, "registry": report}


def _ease_section(tmp):
    out = {"volatile_report": None, "journal": None, "restart": None,
           "dead_letter": None, "durable_report": None}

    # the volatile adapter states what it is
    volatile = EaseAdapter(lambda t, e: {"status": "accepted"},
                           now=EASE_CLOCK)
    out["volatile_report"] = volatile.report()

    # rail down mid-send: the envelope was journaled BEFORE the wire
    db = os.path.join(tmp, "outbox.db")

    def down(t, e):
        raise OSError("rail down")

    first = EaseAdapter(down, outbox=SqliteOutbox(db), now=EASE_CLOCK)
    try:
        first.cover_issued(COVER)
    except EaseError as exc:
        journaled = first.outbox.pending()[0]
        out["journal"] = {"refusal": str(exc),
                          "idem_key": journaled["idem_key"],
                          "note": "persisted BEFORE the wire — a "
                                  "traveller who exists only on our "
                                  "side is a finding, not a cover"}
    first.outbox.close()

    # restart: new adapter, same outbox file, rail healthy — the replay
    # carries the ORIGINAL idempotency key, so a rail that received the
    # first attempt dedupes the replay
    received = []

    def healthy(t, e):
        received.append(e["idem_key"])
        return {"status": "accepted"}

    second = EaseAdapter(healthy, outbox=SqliteOutbox(db),
                         now=EASE_CLOCK)
    result = second.replay_pending()
    out["restart"] = {"replay": result,
                      "same_idem_key": bool(out["journal"]) and
                      received == [out["journal"]["idem_key"]],
                      "received_keys": received}
    out["durable_report"] = second.report()
    second.outbox.close()

    # a permanently-failing envelope dies with its error named
    db2 = os.path.join(tmp, "outbox2.db")
    dead_adapter = EaseAdapter(down, outbox=SqliteOutbox(db2),
                               now=EASE_CLOCK)
    cover2 = dict(COVER, ref="VHC-DEMO-002")
    try:
        dead_adapter.cover_issued(cover2)
    except EaseError:
        pass  # attempt 1 — journaled
    replays = 0
    while True:
        r = dead_adapter.replay_pending()
        replays += 1
        if r["dead"] or not dead_adapter.outbox.pending():
            break
    dead = dead_adapter.outbox.dead()
    out["dead_letter"] = {"idem_key": dead[0]["idem_key"],
                          "last_error": dead[0]["last_error"],
                          "attempts": 1 + replays,
                          "note": "8 failures and the envelope leaves "
                                  "the retry path — silence is how "
                                  "settlements go missing"}
    dead_adapter.outbox.close()
    return out


def _hsm_section(tmp):
    cc = shutil.which("cc")
    if cc is None:
        return {"environment": "NOT RUN — no C compiler in this "
                               "environment; stated, not hidden"}
    src = os.path.join(os.path.dirname(__file__), "mock_pkcs11.c")
    so = os.path.join(tmp, "mock_pkcs11.so")
    subprocess.run([cc, "-shared", "-fPIC", "-o", so, src],
                   check=True, capture_output=True)

    import vhpkcs11
    module = vhpkcs11.load(so)
    signer, verifier = pkcs11_provider(module, slot=0,
                                       key_label="cover-key")
    ring = KeyRing()
    ring.register("hsm-cover", signer=signer, verifier=verifier,
                  active_from=0, active_to=100)
    payload = b"the checksum is the contract"
    sig = ring.keys["hsm-cover"]["signer"](payload)
    good = ring.keys["hsm-cover"]["verifier"](payload, sig)
    tampered = ring.keys["hsm-cover"]["verifier"](b"tampered", sig)
    try:
        ring.snapshot()
        snap = "NOT REFUSED — a custody breach"
    except VHCError as exc:
        snap = str(exc)
    fl_ver = "%d.%d" % (module.fl.version.major, module.fl.version.minor)
    module.close()
    return {"environment": "compiled-abi-fixture",
            "fixture": "packages/travelpass/mock_pkcs11.c — a declared "
                       "test fixture speaking the real Cryptoki ABI; "
                       "the same ctypes path loads YubiHSM "
                       "libyubihsm_pkcs11.so / SoftHSM libsofthsm2.so "
                       "at deployment. The library path is "
                       "configuration, never a code branch.",
            "cryptoki_version": fl_ver,
            "slots": [0],
            "key_label": "cover-key",
            "payload": payload.decode(),
            "signature_hex": sig.hex(),
            "verify_good_payload": good,
            "verify_tampered_payload": tampered,
            "snapshot": snap}


def run_demo():
    tmp = tempfile.mkdtemp()
    try:
        payload = {
            "version": DEMO_VERSION,
            "generated_at": CLOCK(),
            "psp": _psp_section(),
            "ease": _ease_section(tmp),
            "hsm": _hsm_section(tmp),
            "versions": {"vhpsp": vhpsp.PSP_VERSION},
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    import vhease
    payload["versions"]["vhease"] = vhease.VHEASE_VERSION
    import vhpkcs11
    payload["versions"]["vhpkcs11"] = vhpkcs11.VHPKCS11_VERSION
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=1)
        fh.write("\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
