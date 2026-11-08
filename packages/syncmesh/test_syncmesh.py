"""Tests for syncmesh — offline sync governance, all entity classes."""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _pkg in ("syncmesh", "emar", "nursing", "vitals"):
    sys.path.insert(0, os.path.join(_HERE, "..", _pkg))

from syncmesh import (BucketEvaluator, CheckpointLog, ConflictResolver,  # noqa: E402
                      DEFAULT_ENTITY_CLASSES, Error, Principal,
                      RetentionPolicy, SyncRuleRegistry,
                      UploadGate, VERSION, emit_powersync_yaml)
from emar import MAR  # noqa: E402
from nursing import Flowsheet  # noqa: E402


def _registry():
    reg = SyncRuleRegistry()
    reg.propose("r-ward-emar", {
        "entity_class": "emar-administration", "bucket": "ward_emar",
        "scopes": ["facility", "role"], "retention": "episode",
        "rule_version": 1}, by="cmio", day=1)
    reg.propose("r-ward-flowsheet", {
        "entity_class": "flowsheet-row", "bucket": "ward_flowsheets",
        "scopes": ["facility"], "retention": "rolling-30d",
        "rule_version": 1}, by="cmio", day=1)
    reg.ratify("r-ward-emar", by="cnio", day=1)
    reg.ratify("r-ward-flowsheet", by="cnio", day=1)
    return reg


def _nurse(facility="NG-LUTH"):
    return Principal("nurse-ada", facility=facility, roles={"nurse"})


class TestRegistry:
    def test_four_eyes_ratification(self):
        reg = SyncRuleRegistry()
        reg.propose("r-x", {"entity_class": "observation",
                            "bucket": "b", "scopes": ["facility"],
                            "retention": "rolling-30d",
                            "rule_version": 1}, by="a", day=1)
        with pytest.raises(Error, match="proposes never approves"):
            reg.ratify("r-x", by="a", day=1)
        reg.ratify("r-x", by="b", day=1)
        assert reg.live("r-x")["status"] == "live"

    def test_scopeless_rule_refuses(self):
        reg = SyncRuleRegistry()
        with pytest.raises(Error, match="no scopes"):
            reg.propose("r-open", {"entity_class": "observation",
                                   "bucket": "b", "scopes": [],
                                   "retention": "pinned",
                                   "rule_version": 1}, by="a", day=1)

    def test_unknown_entity_class_refuses(self):
        reg = SyncRuleRegistry()
        with pytest.raises(Error, match="entity class"):
            reg.propose("r-bad", {"entity_class": "vibes",
                                  "bucket": "b", "scopes": ["facility"],
                                  "retention": "pinned",
                                  "rule_version": 1}, by="a", day=1)

    def test_retire_carries_reason(self):
        reg = _registry()
        with pytest.raises(Error, match="reason"):
            reg.retire("r-ward-emar", by="cmio", day=2, reason="")
        reg.retire("r-ward-emar", by="cmio", day=2,
                   reason="ward pilot complete")
        with pytest.raises(Error, match="no live sync rule"):
            reg.live("r-ward-emar")


class TestBuckets:
    def test_buckets_come_from_live_rules(self):
        ev = BucketEvaluator(_registry())
        buckets = ev.buckets_for(_nurse())
        assert [b["bucket"] for b in buckets] == [
            "ward_emar", "ward_flowsheets"]

    def test_scope_evaluation_server_side(self):
        ev = BucketEvaluator(_registry())
        nurse = _nurse()
        own = {"facility": "NG-LUTH", "role_required": "nurse"}
        other_facility = {"facility": "NG-UCH", "role_required": "nurse"}
        wrong_role = {"facility": "NG-LUTH", "role_required": "surgeon"}
        assert ev.admits("r-ward-emar", nurse, own) is True
        assert ev.admits("r-ward-emar", nurse, other_facility) is False
        assert ev.admits("r-ward-emar", nurse, other_facility) is False
        assert ev.admits("r-ward-emar", nurse, own) is True
        assert ev.admits("r-ward-emar", nurse, wrong := dict(
            wrong_role)) is False

    def test_patient_consent_axis(self):
        reg = SyncRuleRegistry()
        reg.propose("r-consent", {
            "entity_class": "observation", "bucket": "shared_care",
            "scopes": ["patient-consent"], "retention": "rolling-90d",
            "rule_version": 1}, by="a", day=1)
        reg.ratify("r-consent", by="b", day=1)
        ev = BucketEvaluator(reg)
        p = Principal("dr-x", facility="F", consented_patients={"p-1"})
        assert ev.admits("r-consent", p, {"patient_ref": "p-1"}) is True
        assert ev.admits("r-consent", p, {"patient_ref": "p-2"}) is False


def _emar_validator(mutation, *, day):
    """Adapter: offline eMAR administrations replay through the real
    MAR engine — the same fail-closed code that judges them online."""
    mar = mutation["_engine"]  # test harness injects the live engine
    slot = mar.slots[mutation["payload"]["slot_id"]]
    try:
        slot.record(mutation["payload"]["state"],
                    by=mutation["payload"]["by"],
                    minute=mutation["payload"]["minute"],
                    patient_barcode=mutation["payload"].get(
                        "patient_barcode"),
                    medication_barcode=mutation["payload"].get(
                        "medication_barcode"))
        return {"verdict": "applied"}
    except Exception as exc:
        return {"verdict": "refused", "reason": str(exc)}


class TestUploadGate:
    def _gate(self):
        reg = _registry()
        gate = UploadGate(reg)
        mar = MAR("mar-1", "p-1", "adm-1", "wristband-p1")
        mar.add_order({"order_id": "ord-1", "patient_ref": "p-1",
                       "state": "dispensed", "drug_code": "NG-AMOX500",
                       "dose": "500mg", "frequency": "tds",
                       "medication_barcode": "med-amox"}, day=1)
        gate.register_validator(
            "emar-administration",
            lambda m, day: _emar_validator({**m, "_engine": mar},
                                           day=day))
        return gate, mar

    def _mutation(self, **payload_over):
        payload = {"slot_id": "mar-1-S1",
                   "state": "given", "by": "nurse-ada", "minute": 480,
                   "patient_barcode": "wristband-p1",
                   "medication_barcode": "med-amox",
                   "facility": "NG-LUTH", "role_required": "nurse"}
        payload.update(payload_over)
        return {"idempotency_key": "up-1",
                "entity_class": "emar-administration",
                "rule_id": "r-ward-emar", "base_sha256": "x" * 64,
                "op": "record", "payload": payload}

    def test_clean_upload_applies_through_engine(self):
        gate, mar = self._gate()
        verdict = gate.submit(self._mutation(), principal=_nurse(),
                              day=1)
        assert verdict["verdict"] == "applied"
        assert mar.slots["mar-1-S1"].state == "given"

    def test_engine_refusal_travels_with_reason(self):
        gate, mar = self._gate()
        # wrong medication barcode -> the eMAR engine refuses
        verdict = gate.submit(self._mutation(
            medication_barcode="med-wrong"), principal=_nurse(), day=1)
        assert verdict["verdict"] == "refused"
        assert "barcode" in verdict["reason"].lower()

    def test_idempotent_replay_returns_original_verdict(self):
        gate, mar = self._gate()
        v1 = gate.submit(self._mutation(), principal=_nurse(), day=1)
        v2 = gate.submit(self._mutation(), principal=_nurse(), day=1)
        assert v1["verdict"] == "applied"
        assert v2["verdict"] == "applied" and v2["replayed"] is True
        # the slot was recorded exactly once
        assert mar.slots["mar-1-S1"].state == "given"

    def test_missing_idempotency_key_refuses(self):
        gate, _ = self._gate()
        m = self._mutation()
        del m["idempotency_key"]
        with pytest.raises(Error, match="idempotency"):
            gate.submit(m, principal=_nurse(), day=1)

    def test_out_of_scope_upload_refuses(self):
        gate, _ = self._gate()
        verdict = gate.submit(
            self._mutation(facility="NG-UCH"), principal=_nurse(),
            day=1)
        assert verdict["verdict"] == "refused"
        assert "never in your bucket" in verdict["reason"]

    def test_retired_rule_refuses_upload(self):
        gate, _ = self._gate()
        gate.registry.retire("r-ward-emar", by="cmio", day=2,
                             reason="pilot complete")
        with pytest.raises(Error, match="no live sync rule"):
            gate.submit(self._mutation(), principal=_nurse(), day=2)

    def test_unregistered_validator_refuses(self):
        reg = _registry()
        gate = UploadGate(reg)
        with pytest.raises(Error, match="cannot judge"):
            gate.submit(self._mutation(), principal=_nurse(), day=1)


class TestConflicts:
    def test_policies_named_on_every_outcome(self):
        r = ConflictResolver()
        base = {"sha256": "a" * 64}
        stale = {"base_sha256": "b" * 64}
        assert r.resolve("observation", existing=base,
                         incoming=stale)["outcome"] == "both-kept"
        assert r.resolve("flowsheet-row", existing=base,
                         incoming=stale)["outcome"] == \
            "amendment-required"
        assert r.resolve("medication-order", existing=base,
                         incoming=stale)["outcome"] == "refused"
        assert r.resolve("teleconsulta-status", existing=base,
                         incoming=stale)["outcome"] == "incoming-wins"

    def test_clean_base_resolves_clean(self):
        r = ConflictResolver()
        same = "a" * 64
        out = r.resolve("medication-order",
                        existing={"sha256": same},
                        incoming={"base_sha256": same})
        assert out["outcome"] == "clean"

    def test_conflict_report_is_checksummed(self):
        gate, _ = TestUploadGate._gate(self)
        # force a conflict verdict via a custom validator
        gate.register_validator(
            "flowsheet-row",
            lambda m, day: {"verdict": "conflict",
                            "reason": "row changed under you"})
        m = {"idempotency_key": "up-c", "entity_class": "flowsheet-row",
             "rule_id": "r-ward-flowsheet", "base_sha256": "x" * 64,
             "op": "record",
             "payload": {"facility": "NG-LUTH"}}
        gate.submit(m, principal=_nurse(), day=1)
        report = gate.conflict_report()
        assert report["conflicts"] == 1
        assert report["by_policy"]["amend-not-edit"] == 1
        assert len(report["sha256"]) == 64


class TestRetention:
    def test_eviction_is_stated(self):
        rp = RetentionPolicy()
        records = [
            {"ref": "r1", "retention": "pinned"},
            {"ref": "r2", "retention": "episode", "episode_open": True},
            {"ref": "r3", "retention": "episode",
             "episode_open": False},
            {"ref": "r4", "retention": "rolling-30d",
             "last_touch_day": 10},
            {"ref": "r5", "retention": "rolling-30d",
             "last_touch_day": 95},
        ]
        kept, evicted = rp.evaluate(records, day=100)
        # r4 is untouched for 90 days (> rolling-30d) — evicted;
        # r5 was touched 5 days ago — kept.
        assert [r["ref"] for r in kept] == ["r1", "r2", "r5"]
        assert [r["ref"] for r in evicted] == ["r3", "r4"]
        assert all("reason" in e for e in rp.eviction_log)

    def test_unknown_retention_refuses(self):
        rp = RetentionPolicy()
        with pytest.raises(Error, match="retention"):
            rp.evaluate([{"ref": "x", "retention": "forever-ish",
                          "last_touch_day": 1}], day=2)


class TestCheckpoints:
    def test_hash_chain_verifies(self):
        log = CheckpointLog()
        log.append("b1", applied_keys=["up-1"], day=1)
        log.append("b2", applied_keys=["up-2", "up-3"], day=2)
        assert log.verify_chain() is True
        log.entries[0]["applied"] = ["up-9"]  # tamper
        assert log.verify_chain() is False

    def test_genesis_head(self):
        log = CheckpointLog()
        assert log.head() == CheckpointLog.GENESIS


class TestGenerator:
    def test_yaml_is_build_artifact_of_registry(self):
        yaml_text = emit_powersync_yaml(_registry())
        assert "GENERATED from syncmesh registry" in yaml_text
        assert "bucket_definitions:" in yaml_text
        assert "ward_emar:" in yaml_text
        assert "rule r-ward-emar v1" in yaml_text
        assert "retention episode" in yaml_text

    def test_yaml_omits_unratified_rules(self):
        reg = _registry()
        reg.propose("r-draft", {
            "entity_class": "observation", "bucket": "draft_bucket",
            "scopes": ["facility"], "retention": "pinned",
            "rule_version": 1}, by="a", day=2)
        assert "draft_bucket" not in emit_powersync_yaml(reg)

    def test_version_stamp(self):
        assert VERSION == "syncmesh@1.2.0"


class TestExporter:
    def test_deployment_registry_covers_every_entity_class(self):
        from export_powersync import DEPLOYMENT_RULES, build_registry
        reg = build_registry()
        assert len(DEPLOYMENT_RULES) == len(DEFAULT_ENTITY_CLASSES)
        classes = {r[1] for r in DEPLOYMENT_RULES}
        assert classes == set(DEFAULT_ENTITY_CLASSES)
        # every deployment rule is live (ratified, not proposed)
        for rule_id, entity_class, bucket, scopes, retention in \
                DEPLOYMENT_RULES:
            rule = reg.live(rule_id)  # raises if not ratified
            assert rule["status"] == "live"
            assert rule["entity_class"] == entity_class
            assert rule["bucket"] == bucket

    def test_export_is_deterministic(self):
        from export_powersync import build_registry
        a = emit_powersync_yaml(build_registry())
        b = emit_powersync_yaml(build_registry())
        assert a == b
        assert len(a) > 200
