"""test_vhc_custody.py — HSM key custody behind the KeyRing and
currency-safe settlement.

Doctrines under test:
- Custody is pluggable: a signer callable signs, the engine never
  learns which custody it is holding.
- An HSM key cannot ride in an offline cache — snapshot refuses.
- A custody boundary is a stated RED verdict, never a crash.
- Mixed-currency sums refuse; a settlement file is per currency.
"""

import hashlib
import hmac

import pytest

from packages.travelpass.vhcover import (
    KeyRing, VHCError, VHCCoverRegistry, read_qr, sign_claims)
from packages.travelpass.vhsettle import (
    SettleError, auto_refund_duplicates, reconcile, settlement_file)


def _hsm(secret):
    """A toy HSM: the secret lives inside the closure; only sign/verify
    operations cross the boundary."""
    def sign(payload: bytes) -> bytes:
        return hmac.new(secret.encode(), payload,
                        hashlib.sha256).digest()

    def verify(payload: bytes, sig: bytes) -> bool:
        expected = hmac.new(secret.encode(), payload,
                            hashlib.sha256).digest()
        return hmac.compare_digest(expected, sig)

    return sign, verify


def _registry(ring):
    return VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                            key_ring=ring, issuer="cv-vhc")


def _buy(reg, key_id="k1"):
    q = reg.quote(days=30, age_years=35, day=1)
    return reg.purchase(quote_id=q["quote_id"], passport_ref="P-1",
                        full_name="Ada Lovelace", entry_day=10, day=1,
                        key_id=key_id)


# --- HSM custody ------------------------------------------------------

def test_hsm_signer_signs_and_ring_verifies_online():
    sign, verify = _hsm("vault-secret")
    ring = KeyRing()
    ring.register("hsm-1", signer=sign, verifier=verify, active_from=0)
    token = sign_claims({"ref": "VHC-X", "kind": "cover"},
                        key_ring=ring, key_id="hsm-1", day=1)
    claims, ok, reasons = read_qr(token, key_ring=ring, day=1)
    assert ok and claims["ref"] == "VHC-X" and reasons == []


def test_hsm_signed_cover_verifies_green_end_to_end():
    sign, verify = _hsm("vault-secret")
    ring = KeyRing()
    ring.register("hsm-1", signer=sign, verifier=verify, active_from=0)
    reg = _registry(ring)
    cover = _buy(reg, key_id="hsm-1")
    verdict = reg.verify(cover["qr"], day=12)
    assert verdict["verdict"] == "green"
    assert verdict["claims"]["ref"] == cover["ref"]


def test_hsm_key_refuses_offline_snapshot():
    sign, _ = _hsm("vault-secret")
    ring = KeyRing()
    ring.register("hsm-1", signer=sign, active_from=0)
    ring.register("k-plain", "field-secret", active_from=0)
    with pytest.raises(VHCError, match="cannot carry key 'hsm-1'"):
        ring.snapshot()


def test_ring_custody_snapshot_still_carries_secrets():
    ring = KeyRing()
    ring.register("k1", "sekrit", active_from=0)
    snap = ring.snapshot()
    assert snap["k1"]["secret"] == "sekrit"
    ring2 = KeyRing.from_snapshot(snap)
    token = sign_claims({"ref": "R"}, key_ring=ring, key_id="k1", day=1)
    claims, ok, _ = read_qr(token, key_ring=ring2, day=1)
    assert ok and claims["ref"] == "R"


def test_signer_without_verifier_states_custody_boundary():
    sign, _ = _hsm("vault-secret")
    signer_ring = KeyRing()
    signer_ring.register("hsm-1", signer=sign, active_from=0)
    token = sign_claims({"ref": "R"}, key_ring=signer_ring,
                        key_id="hsm-1", day=1)
    # a second ring that knows the key window but holds no secret and
    # no verifier cannot verify — a stated boundary, not a crash
    blind_ring = KeyRing()
    blind_ring.keys["hsm-1"] = {"secret": None, "signer": None,
                                "verifier": None, "active_from": 0,
                                "active_to": None}
    claims, ok, reasons = read_qr(token, key_ring=blind_ring, day=1)
    assert not ok and claims is None
    assert any("custody boundary" in r for r in reasons)


def test_custody_boundary_is_red_verdict_not_crash():
    sign, _ = _hsm("vault-secret")
    ring = KeyRing()
    ring.register("hsm-1", signer=sign, active_from=0)
    reg = _registry(ring)
    cover = _buy(reg, key_id="hsm-1")
    reg.key_ring.keys["hsm-1"] = {"secret": None, "signer": None,
                                  "verifier": None, "active_from": 0,
                                  "active_to": None}
    verdict = reg.verify(cover["qr"], day=12)
    assert verdict["verdict"] == "red"
    assert any("custody boundary" in r for r in verdict["reasons"])


def test_key_without_secret_or_signer_refuses_registration():
    ring = KeyRing()
    with pytest.raises(VHCError, match="secret or a signer"):
        ring.register("k-empty", active_from=0)


def test_hsm_signature_still_detects_tampering():
    sign, verify = _hsm("vault-secret")
    ring = KeyRing()
    ring.register("hsm-1", signer=sign, verifier=verify, active_from=0)
    token = sign_claims({"ref": "R", "amt": 100}, key_ring=ring,
                        key_id="hsm-1", day=1)
    body, sig = token.rsplit(".", 1)
    forged = body + "." + ("A" * len(sig))
    claims, ok, reasons = read_qr(forged, key_ring=ring, day=1)
    assert not ok and "unverifiable: bad signature" in reasons


def test_custody_audited_on_registration():
    ring = KeyRing()
    sign, _ = _hsm("s")
    ring.register("hsm-1", signer=sign, active_from=0)
    ring.register("k1", "sekrit", active_from=0)
    custody = {a["key_id"]: a["custody"] for a in ring.audit}
    assert custody == {"hsm-1": "hsm", "k1": "ring"}


# --- currency-safe settlement -----------------------------------------

def _txn(ref, amount, currency=None, day=5, psp="vinti4"):
    t = {"ref": ref, "amount_minor": amount, "psp": psp, "day": day}
    if currency:
        t["currency"] = currency
    return t


def test_settlement_file_refuses_mixed_currency():
    txns = [_txn("a", 2500, "EUR"), _txn("b", 5000, "NGN")]
    with pytest.raises(SettleError, match="mixed-currency sums refuse"):
        settlement_file(txns, day=5, psp="vinti4")


def test_settlement_file_per_currency_filter():
    txns = [_txn("a", 2500, "EUR"), _txn("b", 5000, "NGN"),
            _txn("c", 2500, "EUR")]
    f = settlement_file(txns, day=5, psp="vinti4", currency="EUR")
    assert f["currency"] == "EUR"
    assert f["count"] == 2 and f["total_minor"] == 5000
    assert {r["ref"] for r in f["rows"]} == {"a", "c"}


def test_settlement_file_single_currency_records_it():
    txns = [_txn("a", 2500, "EUR"), _txn("b", 2500, "EUR")]
    f = settlement_file(txns, day=5, psp="vinti4")
    assert f["currency"] == "EUR" and f["total_minor"] == 5000


def test_legacy_rows_without_currency_still_settle():
    txns = [_txn("a", 2500), _txn("b", 2500)]
    f = settlement_file(txns, day=5, psp="vinti4")
    assert f["currency"] is None and f["total_minor"] == 5000


def test_reconcile_refuses_psp_file_in_wrong_currency():
    txns = [_txn("a", 2500, "EUR")]
    psp_rows = [{"ref": "a", "amount_minor": 2500, "currency": "NGN"}]
    with pytest.raises(SettleError, match="split the file per currency"):
        reconcile(txns, day=5, psp="vinti4", psp_rows=psp_rows,
                  currency="EUR")


def test_reconcile_per_currency_clean():
    txns = [_txn("a", 2500, "EUR"), _txn("b", 5000, "NGN")]
    psp_rows = [{"ref": "a", "amount_minor": 2500, "currency": "EUR"}]
    result = reconcile(txns, day=5, psp="vinti4", psp_rows=psp_rows,
                       currency="EUR")
    assert result["currency"] == "EUR"
    assert result["breaks"] == [] and result["txn_count"] == 1


def test_same_amount_two_currencies_is_not_a_duplicate():
    txns = [
        {"ref": "p1", "amount_minor": 5000, "payer_key": "P-9",
         "currency": "NGN", "day": 5},
        {"ref": "p2", "amount_minor": 5000, "payer_key": "P-9",
         "currency": "EUR", "day": 5},
    ]
    assert auto_refund_duplicates(txns, day=5) == []


def test_same_amount_same_currency_is_a_duplicate():
    txns = [
        {"ref": "p1", "amount_minor": 5000, "payer_key": "P-9",
         "currency": "NGN", "day": 5},
        {"ref": "p2", "amount_minor": 5000, "payer_key": "P-9",
         "currency": "NGN", "day": 5},
    ]
    refunds = auto_refund_duplicates(txns, day=5)
    assert len(refunds) == 1 and refunds[0]["duplicate_ref"] == "p2"
