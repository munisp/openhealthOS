"""Tests for vhdashx — the growth/risk payload exporter.

Same doctrine as the rest of the travelpass suite: determinism is a
contract, unpriced is stated, templates refuse, and the checksum is
the contract on every payload.
"""

import hashlib
import json

import pytest

from vhcredit import CreditError
from vhcohorts import CohortError
from vhroam import RoamError, price_roam
from vhdashx import (GROWTH_VERSION, export_growth, growth_payload,
                     seed_growth)


def _verify_stamp(payload):
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return digest == payload["sha256"]


def test_seed_is_deterministic():
    a = growth_payload(seed_growth())
    b = growth_payload(seed_growth())
    assert a == b
    assert a["sha256"] == b["sha256"]
    assert _verify_stamp(a)


def test_payload_version_and_sections():
    p = growth_payload(seed_growth())
    assert p["version"] == GROWTH_VERSION
    for section in ("sponsors", "credit", "cohorts", "roam", "fraud",
                    "treasury", "profiles"):
        assert section in p


def test_sponsors_priced_through_engine():
    p = growth_payload(seed_growth())
    assert p["sponsors"]["sponsorships"] == 12
    assert p["sponsors"]["destination_currency"] == "NGN"
    ccys = {b["currency"] for b in p["sponsors"]["by_currency"]}
    assert ccys == {"USD", "GHS", "KES", "ZAR"}
    assert sum(b["count"] for b in p["sponsors"]["by_currency"]) == 12


def test_credit_book_states_and_boundaries():
    book = seed_growth()
    p = growth_payload(book)
    assert p["credit"]["plans"] == 8
    assert p["credit"]["by_state"].get("settled") == 5
    assert p["credit"]["by_state"].get("paused") == 3
    # a paused plan states its vested boundary — never a silent lapse
    paused = [pl for pl in book["credit"].plans.values()
              if pl.paused_at_day is not None]
    assert paused and all(pl.paused_at_day == pl.vested_days()
                          for pl in paused)
    # overpayment refuses even on a live plan
    live = [pl for pl in book["credit"].plans.values()
            if pl.paused_at_day is None and pl.paid_minor < pl.total_minor]
    if live:
        with pytest.raises(CreditError):
            live[0].pay(live[0].total_minor, day=9, ref="X")


def test_cohort_census_frozen_and_invoice_stamped():
    book = seed_growth()
    p = growth_payload(book)
    assert p["cohorts"]["members"] == 40
    assert p["cohorts"]["invoice_total_minor"] == 40 * 250000
    with pytest.raises(CohortError):
        book["cohorts"].add_member("unilag-sim-2026",
                                   member_ref="MBR-SIM-9999",
                                   full_name="Late-Sim", day=2)


def test_roam_prices_ratified_members_and_refuses_templates():
    book = seed_growth()
    p = growth_payload(book)
    assert p["roam"]["sample_verdict"] == "amber"   # strictest wins
    assert {ln["country"] for ln in p["roam"]["sample_segments"]} == \
        {"ng", "cv"}
    # a template member refuses to price — unratified stays unsellable
    with pytest.raises(Exception) as exc:
        price_roam(book["roam"], segments={"gh": 3}, age_years=30)
    assert exc.type.__name__ in ("RoamError", "CoverError")


def test_fraud_ring_detected_with_evidence():
    p = growth_payload(seed_growth())
    assert p["fraud"]["claims_scanned"] == 19
    assert p["fraud"]["rings"] == 1
    row = p["fraud"]["ring_rows"][0]
    assert row["provider"] == "clinic-sim-7"
    assert row["claimants"] == 3
    assert len(row["evidence_sha256"]) == 64


def test_treasury_unpriced_is_stated_never_zeroed():
    p = growth_payload(seed_growth())
    unpriced = p["treasury"]["unpriced"]
    assert len(unpriced) == 1
    assert unpriced[0]["currency"] == "GBP"
    assert unpriced[0]["eur_minor"] is None
    # priced corridors still total honestly
    assert p["treasury"]["total_priced_eur_minor"] > 0


def test_profiles_split_ratified_vs_template():
    p = growth_payload(seed_growth())
    assert set(p["profiles"]["ratified"]) == {"cv-vhc@1.0.0",
                                              "ng-vhc@1.0.0"}
    assert len(p["profiles"]["templates"]) == 12


def test_export_writes_parseable_payload(tmp_path):
    path = export_growth(seed_growth(), str(tmp_path))
    with open(path) as fh:
        payload = json.load(fh)
    assert payload["version"] == GROWTH_VERSION
    assert _verify_stamp(payload)
