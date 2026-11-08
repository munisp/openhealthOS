"""Tests for healthcred — verifiable health credentials."""

import pytest

from healthcred import (CredentialWallet, Error, PREFIX, Verifier,
                        subject_hash, VERSION)

RING = {"k1": b"secret-one", "k2": b"secret-two"}
SALT = "cv-nha-2026"


@pytest.fixture
def wallet():
    return CredentialWallet(RING, "k1", issuer="CV-HAN", salt=SALT)


@pytest.fixture
def cred(wallet):
    return wallet.mint("S-0001", "p-ada", "vaccination",
                       "yellow-fever dose 1 complete",
                       valid_until=3650, issued_day=10, by="CV-HAN")


class TestMinting:
    def test_mint_produces_v2_tag(self, cred):
        parts = cred["tag"].split(".")
        assert parts[0] == PREFIX and len(parts) == 8
        assert cred["version"] == VERSION

    def test_only_issuer_mints(self, wallet):
        with pytest.raises(Error, match="issuer"):
            wallet.mint("S-9", "p-x", "vaccination", "s",
                        valid_until=100, issued_day=1, by="intruder")

    def test_serial_unique(self, wallet, cred):
        with pytest.raises(Error, match="already minted"):
            wallet.mint("S-0001", "p-x", "vaccination", "s",
                        valid_until=100, issued_day=1, by="CV-HAN")

    def test_unknown_type_refuses(self, wallet):
        with pytest.raises(Error, match="credential type"):
            wallet.mint("S-2", "p-x", "diploma", "s",
                        valid_until=100, issued_day=1, by="CV-HAN")

    def test_expiry_must_follow_issue(self, wallet):
        with pytest.raises(Error, match="never expires"):
            wallet.mint("S-3", "p-x", "vaccination", "s",
                        valid_until=5, issued_day=10, by="CV-HAN")

    def test_subject_hash_requires_salt(self):
        with pytest.raises(Error, match="salt"):
            subject_hash("p-x", "")


class TestVerification:
    def test_valid_offline_verify(self, cred):
        v = Verifier(RING)
        out = v.verify(cred["tag"], verifier="border-post", day=100)
        assert out["cred_type"] == "vaccination"
        assert out["serial"] == "S-0001"
        assert out["subject_checked"] is None

    def test_subject_check_with_salt(self, cred):
        v = Verifier(RING, salt=SALT)
        out = v.verify(cred["tag"], verifier="clinic", day=100,
                       subject_ref="p-ada")
        assert out["subject_checked"] is True

    def test_subject_mismatch_refuses(self, cred):
        v = Verifier(RING, salt=SALT)
        with pytest.raises(Error, match="does not belong"):
            v.verify(cred["tag"], verifier="clinic", day=100,
                     subject_ref="p-intruder")

    def test_subject_check_without_salt_refuses(self, cred):
        v = Verifier(RING)
        with pytest.raises(Error, match="no salt"):
            v.verify(cred["tag"], verifier="clinic", day=100,
                     subject_ref="p-ada")

    def test_revoked_refuses(self, cred):
        body = cred["tag"].rsplit(".", 1)[0]
        v = Verifier(RING, revoked={body})
        with pytest.raises(Exception, match="tag revoked"):
            v.verify(cred["tag"], verifier="border-post", day=100)

    def test_expired_refuses(self, wallet):
        c = wallet.mint("S-5", "p-x", "test-result", "covid neg",
                        valid_until=50, issued_day=10, by="CV-HAN")
        v = Verifier(RING)
        with pytest.raises(Error, match="expired"):
            v.verify(c["tag"], verifier="border-post", day=51)

    def test_forged_tag_refuses(self, cred):
        v = Verifier(RING)
        forged = cred["tag"][:-1] + ("0" if cred["tag"][-1] != "0"
                                     else "1")
        with pytest.raises(Exception, match="bad signature"):
            v.verify(forged, verifier="border-post", day=100)

    def test_unknown_key_refuses(self, cred):
        v = Verifier({"k9": b"other"})
        with pytest.raises(Exception, match="unknown key"):
            v.verify(cred["tag"], verifier="border-post", day=100)


class TestAudit:
    def test_every_attempt_audited(self, cred):
        v = Verifier(RING)
        v.verify(cred["tag"], verifier="a", day=100)
        with pytest.raises(Error, match="expired"):
            v.verify(cred["tag"], verifier="b", day=9999)
        a = v.audit()
        assert a["verifications"] == 2 and a["refused"] == 1
        assert len(a["sha256"]) == 64
