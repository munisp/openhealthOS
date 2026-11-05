"""test_vhc_wave2.py — the S/C-priority closures: proactive
notifications (FR-HLP-02), courtesy riders (S-029), departure
close-out (S-033), self-service extension cutoff (FR-SAL-06), manual
refund exceptions with dual approval (FR-PAY-04), idempotent partner
checkout (FR-SAL-04), duplicate-claim detection (FR-CLM-05).

Doctrines under test:
- The scheme talks first — once per (event, cover, day), never twice.
- A rider without a manifest is a favour; a conversion charges from
  the disruption day.
- A traveller departs once; exposure stops at departure.
- An exception refund never executes on one signature.
- A retried bind is the same sale, not a new one; the PAN never lands.
- The same claim twice in one day is a resubmission, not a new case.
"""

import pytest

from packages.travelpass.vhcover import (
    KeyRing, RefundExceptionQueue, VHCError, VHC_PROFILES,
    VHCCoverRegistry)
from packages.travelpass.vhclaims import ClaimsRegistry, ClaimsError
from packages.travelpass.vhnotify import (
    NotificationOutbox, NotifyError)
from packages.travelpass.vhsales import PartnerRegistry, SalesError
from packages.travelpass.vhsupport import ContentLayer


def _ring():
    ring = KeyRing()
    ring.register("k1", "sekrit", active_from=0)
    return ring


def _reg(ring=None):
    return VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                            key_ring=ring or _ring(), issuer="cv-vhc")


def _buy(reg, passport="P-1", name="Ada Lovelace", days=30, entry=10,
         day=1):
    q = reg.quote(days=days, age_years=35, day=day)
    return reg.purchase(quote_id=q["quote_id"], passport_ref=passport,
                        full_name=name, entry_day=entry, day=day,
                        key_id="k1")


def _outbox():
    sent = []
    return sent, NotificationOutbox(
        lambda ch, m: sent.append((ch, m)) or {"status": "accepted"})


# --- FR-HLP-02: proactive notifications --------------------------------

def test_unplugged_outbox_refuses():
    with pytest.raises(NotifyError, match="not a rail"):
        NotificationOutbox(None)


def test_emit_is_idempotent_per_event_ref_day():
    sent, ob = _outbox()
    first = ob.emit("purchase-confirmation", ref="VHC-1",
                    channel="email", lang="en", day=1)
    replay = ob.emit("purchase-confirmation", ref="VHC-1",
                     channel="email", lang="en", day=1)
    assert not first["replayed"] and replay["replayed"]
    assert replay["notification_id"] == first["notification_id"]
    assert len(sent) == 1  # a reminder sent twice is spam


def test_rail_failure_refuses_loudly():
    ob = NotificationOutbox(lambda ch, m: {"status": "failed"})
    with pytest.raises(NotifyError, match="refused by the rail"):
        ob.emit("purchase-confirmation", ref="VHC-1", channel="sms",
                lang="en", day=1)


def test_unknown_event_and_channel_refuse():
    _, ob = _outbox()
    with pytest.raises(NotifyError, match="unknown event"):
        ob.emit("marketing-blast", ref="VHC-1", channel="email",
                lang="en", day=1)
    with pytest.raises(NotifyError, match="unknown channel"):
        ob.emit("purchase-confirmation", ref="VHC-1",
                channel="pigeon", lang="en", day=1)


def test_schedule_fires_pretravel_expiry_and_nudge():
    sent, ob = _outbox()
    reg = _reg()
    cover = _buy(reg, entry=10)          # window 10..39
    profile = VHC_PROFILES["cv-vhc@1.0.0"]
    out = ob.evaluate_schedule(list(reg.covers.values()), day=3,
                               profile=profile)   # 10-3 == 7
    assert [e["event"] for e in out] == ["pre-travel-reminder"]
    out = ob.evaluate_schedule(list(reg.covers.values()), day=36,
                               profile=profile)   # 39-36 == 3
    assert [e["event"] for e in out] == ["expiry-warning"]
    out = ob.evaluate_schedule(list(reg.covers.values()), day=40,
                               profile=profile)   # in grace
    assert [e["event"] for e in out] == ["extension-nudge"]
    assert len(sent) == 3
    assert ob.report()["sent"] == 3


def test_schedule_silent_on_closed_trips():
    sent, ob = _outbox()
    reg = _reg()
    cover = _buy(reg, entry=10)
    reg.close_out(cover["ref"], day=20)  # departed
    out = ob.evaluate_schedule(list(reg.covers.values()), day=36,
                               profile=VHC_PROFILES["cv-vhc@1.0.0"])
    assert out == [] and sent == []  # the trip is closed — silence


def test_content_fallback_stated_in_message():
    sent = []
    content = ContentLayer()
    content.publish("v1", lang="en",
                    entries={"notify.expiry-warning":
                             "Your cover expires soon."}, by="editor")
    ob = NotificationOutbox(
        lambda ch, m: sent.append(m) or {"status": "accepted"},
        content=content)
    ob.emit("expiry-warning", ref="VHC-1", channel="email", lang="fr",
            day=36)
    msg = sent[0]
    assert msg["text_lang"] == "en" and "fallback_stated" in msg


# --- S-029: courtesy rider ----------------------------------------------

def test_courtesy_rider_issues_on_manifest_match():
    reg = _reg()
    rider = reg.courtesy_cover(passport_ref="P-9", full_name="Bana",
                               manifest_ref="MNF-77", day=20,
                               key_id="k1")
    assert rider["courtesy"] and rider["window"] == {"from": 20,
                                                     "to": 21}
    verdict = reg.verify(rider["qr"], day=20)
    assert verdict["verdict"] == "green"


def test_courtesy_rider_needs_a_manifest():
    reg = _reg()
    with pytest.raises(VHCError, match="manifest"):
        reg.courtesy_cover(passport_ref="P-9", full_name="Bana",
                           manifest_ref=None, day=20, key_id="k1")


def test_courtesy_converts_to_full_policy_contiguous():
    reg = _reg()
    rider = reg.courtesy_cover(passport_ref="P-9", full_name="Bana",
                               manifest_ref="MNF-77", day=20,
                               key_id="k1")
    cover = reg.convert_courtesy(rider["ref"], passport_ref="P-9",
                                 full_name="Bana", days=30,
                                 age_years=40, day=21, key_id="k1")
    assert cover["converted_from"] == rider["ref"]
    assert rider["converted_to"] == cover["ref"]
    # charged from the disruption day — contiguous, not double-charged
    assert cover["window"]["from"] == 20
    with pytest.raises(VHCError, match="converts once"):
        reg.convert_courtesy(rider["ref"], passport_ref="P-9",
                             full_name="Bana", days=30, age_years=40,
                             day=21, key_id="k1")
    paid = _buy(reg, passport="P-2", name="Cesaria")
    with pytest.raises(VHCError, match="only a courtesy rider"):
        reg.convert_courtesy(paid["ref"], passport_ref="P-2",
                             full_name="Cesaria", days=30, age_years=40,
                             day=21, key_id="k1")


# --- S-033: departure close-out -----------------------------------------

def test_close_out_stamps_departure_and_exposure():
    reg = _reg()
    cover = _buy(reg, entry=10)          # window 10..39
    rec = reg.close_out(cover["ref"], day=25)
    assert rec["departed_day"] == 25
    assert rec["exposure_days"] == 16    # days 10..25 actual
    with pytest.raises(VHCError, match="departs once"):
        reg.close_out(cover["ref"], day=26)
    with pytest.raises(VHCError, match="unknown ref"):
        reg.close_out("VHC-NOPE", day=26)


# --- FR-SAL-06: self-service extension cutoff ---------------------------

def test_self_service_extension_cutoff_at_day_30():
    reg = _reg()
    cover = _buy(reg, entry=10, days=30)     # window 10..39
    # day 20 of term: self-service fine
    reg.extend(cover["ref"], extra_days=30, day=20, key_id="k1",
               channel="self-service")
    assert cover["extensions"][0]["channel"] == "self-service"
    # day 45 > window.from(10) + 30: self-service refuses, desk extends
    with pytest.raises(VHCError, match="the desk takes it from here"):
        reg.extend(cover["ref"], extra_days=30, day=45, key_id="k1",
                   channel="self-service")
    reg.extend(cover["ref"], extra_days=30, day=45, key_id="k1",
               channel="desk")
    assert len(cover["extensions"]) == 2


# --- FR-PAY-04: refund exceptions queue ---------------------------------

def _queue_with_cover():
    reg = _reg()
    cover = _buy(reg)
    return reg, cover, RefundExceptionQueue(reg)


def test_exception_executes_on_two_distinct_approvals():
    reg, cover, q = _queue_with_cover()
    req = q.request(cover["ref"], reason="compassionate — hospital "
                    "billed the traveller twice", amount_minor=1000,
                    instrument="original-card", by="agent-1", day=5)
    assert req["state"] == "pending"
    q.approve(req["req_id"], by="super-1", day=5)
    assert req["state"] == "pending"   # one signature is not two
    q.approve(req["req_id"], by="super-2", day=5)
    assert req["state"] == "executed"
    posted = [p for p in reg.payments if p["kind"] == "refund-exception"]
    assert posted == [{"ref": cover["ref"], "kind": "refund-exception",
                       "minor": -1000, "day": 5}]
    assert "dual approval" in cover["refunds"][-1]["basis"]


def test_requester_cannot_approve_and_hands_sign_once():
    reg, cover, q = _queue_with_cover()
    req = q.request(cover["ref"], reason="x", amount_minor=100,
                    instrument="original-card", by="agent-1", day=5)
    with pytest.raises(VHCError, match="four-eyes"):
        q.approve(req["req_id"], by="agent-1", day=5)
    q.approve(req["req_id"], by="super-1", day=5)
    with pytest.raises(VHCError, match="signs once"):
        q.approve(req["req_id"], by="super-1", day=5)


def test_rejection_is_reasoned_and_final():
    reg, cover, q = _queue_with_cover()
    req = q.request(cover["ref"], reason="x", amount_minor=100,
                    instrument="original-card", by="agent-1", day=5)
    with pytest.raises(VHCError, match="without a reason"):
        q.reject(req["req_id"], by="super-1", reason="", day=5)
    q.reject(req["req_id"], by="super-1", reason="insufficient "
             "evidence", day=5)
    assert req["state"] == "rejected"
    with pytest.raises(VHCError, match="pending"):
        q.approve(req["req_id"], by="super-2", day=5)


# --- FR-SAL-04: idempotent partner checkout ------------------------------

def _partner_setup():
    reg = _reg()
    partners = PartnerRegistry()
    partners.register("tap", legal_name="TAP Air Portugal",
                      api_key="tap-key", day=0)
    return reg, partners


def test_retried_bind_returns_same_cover_no_second_charge():
    reg, partners = _partner_setup()
    q = partners.quote_for(reg, "tap", "tap-key", days=30,
                           age_years=35, day=1)
    first = partners.purchase_for(
        reg, "tap", "tap-key", quote_id=q["quote_id"],
        passport_ref="P-1", full_name="Ada", entry_day=10, day=1,
        key_id="k1", idem_key="booking-123",
        payment_redirect_ref="REDIR-abc")
    replay = partners.purchase_for(
        reg, "tap", "tap-key", quote_id=q["quote_id"],
        passport_ref="P-1", full_name="Ada", entry_day=10, day=1,
        key_id="k1", idem_key="booking-123",
        payment_redirect_ref="REDIR-abc".replace("REDIR", "REDIR"))
    assert replay["ref"] == first["ref"] and replay["idempotent_replay"]
    premiums = [p for p in reg.payments if p["kind"] == "premium"]
    assert len(premiums) == 1  # the network's doubt is not a charge


def test_pan_never_lands():
    reg, partners = _partner_setup()
    q = partners.quote_for(reg, "tap", "tap-key", days=30,
                           age_years=35, day=1)
    with pytest.raises(SalesError, match="PAN never lands"):
        partners.purchase_for(
            reg, "tap", "tap-key", quote_id=q["quote_id"],
            passport_ref="P-1", full_name="Ada", entry_day=10, day=1,
            key_id="k1", payment_redirect_ref="4539878801234567")


def test_checkout_status_states_not_found():
    reg, partners = _partner_setup()
    missing = partners.checkout_status("tap", "tap-key",
                                       idem_key="never-seen")
    assert missing["state"] == "not-found" and "stated" in missing[
        "note"]
    q = partners.quote_for(reg, "tap", "tap-key", days=30,
                           age_years=35, day=1)
    cover = partners.purchase_for(
        reg, "tap", "tap-key", quote_id=q["quote_id"],
        passport_ref="P-1", full_name="Ada", entry_day=10, day=1,
        key_id="k1", idem_key="booking-9")
    found = partners.checkout_status("tap", "tap-key",
                                     idem_key="booking-9")
    assert found == {"state": "bound", "ref": cover["ref"]}


# --- FR-CLM-05: duplicate-claim detection --------------------------------

def test_exact_duplicate_intake_refused():
    reg = ClaimsRegistry()
    reg.intake(channel="phone", cover_ref="VHC-1",
               claimant_name="Ada", summary="GI distress", now_h=10,
               day=5)
    with pytest.raises(ClaimsError, match="resubmission"):
        reg.intake(channel="phone", cover_ref="VHC-1",
                   claimant_name="Ada", summary="GI distress", now_h=11,
                   day=5)


def test_open_case_same_cover_flags_suspect():
    reg = ClaimsRegistry()
    first = reg.intake(channel="phone", cover_ref="VHC-1",
                       claimant_name="Ada", summary="GI distress",
                       now_h=10, day=5)
    second = reg.intake(channel="portal", cover_ref="VHC-1",
                        claimant_name="Ada", summary="follow-up visit",
                        now_h=12, day=7)
    assert second["duplicate_suspect_of"] == first["case_id"]
    assert reg.report()["duplicate_suspects"] == 1


def test_new_claim_after_closure_is_not_suspect():
    reg = ClaimsRegistry()
    first = reg.intake(channel="phone", cover_ref="VHC-1",
                       claimant_name="Ada", summary="GI distress",
                       now_h=10, day=5)
    first["state"] = "closed"  # resolved elsewhere
    second = reg.intake(channel="portal", cover_ref="VHC-1",
                        claimant_name="Ada", summary="new episode",
                        now_h=12, day=20)
    assert second["duplicate_suspect_of"] is None
