"""Tests for the syncmesh scale wave — durable verdicts, batching,
backpressure, rate limits, shard routing."""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _pkg in ("syncmesh",):
    sys.path.insert(0, os.path.join(_HERE, "..", _pkg))

from syncmesh import (Error, MemoryStore, Principal, RateLimiter,  # noqa: E402
                      ShardRouter, SqliteStore, SyncRuleRegistry,
                      UploadGate)


def _registry():
    reg = SyncRuleRegistry()
    reg.propose("r-ward-emar", {"entity_class": "emar-administration",
                                "bucket": "ward_emar",
                                "scopes": ["facility"],
                                "retention": "episode",
                                "rule_version": 1}, by="cmio", day=1)
    reg.ratify("r-ward-emar", by="cnio", day=1)
    return reg


def _principal():
    return Principal("nurse-ada", facility="NG-LUTH", roles={"nurse"})


def _mutation(key):
    return {"idempotency_key": key, "entity_class": "emar-administration",
            "rule_id": "r-ward-emar", "base_sha256": "0" * 64,
            "op": "record", "payload": {"facility": "NG-LUTH"}}


class TestDurableVerdicts:
    def test_sqlite_store_survives_restart(self, tmp_path):
        path = str(tmp_path / "verdicts.db")
        gate = UploadGate(_registry(), store=SqliteStore(path))
        gate.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        v1 = gate.submit(_mutation("up-1"), principal=_principal(), day=1)
        assert v1["verdict"] == "applied" and "replayed" not in v1
        # a new gate process over the same store — the restart
        gate2 = UploadGate(_registry(), store=SqliteStore(path))
        v2 = gate2.submit(_mutation("up-1"), principal=_principal(), day=2)
        assert v2["verdict"] == "applied" and v2["replayed"] is True
        assert len(gate2.applied) == 0  # never re-applied

    def test_memory_store_is_honestly_volatile(self):
        assert MemoryStore.durable is False
        assert SqliteStore.durable is True

    def test_ttl_pruning_is_stated(self, tmp_path):
        gate = UploadGate(_registry(),
                          store=SqliteStore(str(tmp_path / "v.db")),
                          verdict_ttl_days=30)
        gate.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        gate.submit(_mutation("old"), principal=_principal(), day=1)
        gate.submit(_mutation("new"), principal=_principal(), day=40)
        dropped = gate.prune_verdicts(day=50)  # window: day >= 20
        assert dropped == 1
        assert gate.store.get("old") is None
        assert gate.store.get("new") is not None
        assert "aged out" in gate.store_evictions[0]["reason"]

    def test_prune_without_ttl_refuses(self):
        gate = UploadGate(_registry())
        with pytest.raises(Error, match="declare it first"):
            gate.prune_verdicts(day=100)


class TestBatch:
    def _gate(self):
        gate = UploadGate(_registry())
        gate.register_validator(
            "emar-administration",
            lambda m, day: {"verdict": "applied"}
            if m["idempotency_key"] != "bad"
            else {"verdict": "refused", "reason": "engine says no"})
        return gate

    def test_batch_returns_one_verdict_per_mutation_in_order(self):
        gate = self._gate()
        out = gate.submit_batch(
            [_mutation("a"), _mutation("bad"), _mutation("c")],
            principal=_principal(), day=1)
        assert [v["verdict"] for v in out] == \
            ["applied", "refused", "applied"]
        assert [v["idempotency_key"] for v in out] == ["a", "bad", "c"]

    def test_batch_replays_do_not_double_apply(self):
        gate = self._gate()
        gate.submit_batch([_mutation("a")], principal=_principal(), day=1)
        out = gate.submit_batch([_mutation("a")], principal=_principal(),
                                day=1)
        assert out[0]["replayed"] is True
        assert len(gate.applied) == 1

    def test_empty_batch_is_empty(self):
        assert self._gate().submit_batch([], principal=_principal(),
                                         day=1) == []


class TestBackpressure:
    def test_saturation_refuses_and_does_not_poison_the_key(self):
        gate = UploadGate(_registry(), max_inflight=2)
        gate.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        gate.inflight = 2  # simulate a saturated gate
        with pytest.raises(Error, match="saturated"):
            gate.submit(_mutation("up-s"), principal=_principal(), day=1)
        # the key was never judged — a retry after the burst applies
        gate.inflight = 0
        v = gate.submit(_mutation("up-s"), principal=_principal(), day=1)
        assert v["verdict"] == "applied" and "replayed" not in v


class TestRateLimit:
    def test_budget_drains_then_refuses_loudly(self):
        rl = RateLimiter(capacity=3, refill_per_day=3)
        for _ in range(3):
            rl.consume("nurse-ada", at=1.0)
        with pytest.raises(Error, match="stated, not silent"):
            rl.consume("nurse-ada", at=1.0)

    def test_budget_refills_over_time(self):
        rl = RateLimiter(capacity=2, refill_per_day=2)
        rl.consume("nurse-ada", at=1.0)
        rl.consume("nurse-ada", at=1.0)
        with pytest.raises(Error):
            rl.consume("nurse-ada", at=1.4)
        rl.consume("nurse-ada", at=1.5)  # one token refilled

    def test_gate_uses_limiter(self):
        gate = UploadGate(_registry(),
                          limiter=RateLimiter(capacity=1,
                                              refill_per_day=1))
        gate.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        gate.submit(_mutation("a"), principal=_principal(), day=1)
        with pytest.raises(Error, match="rate limit"):
            gate.submit(_mutation("b"), principal=_principal(), day=1)


class TestSharding:
    def test_route_is_deterministic(self):
        r = ShardRouter(16)
        assert r.route("NG-LUTH") == r.route("NG-LUTH")
        assert 0 <= r.route("NG-LUTH") < 16

    def test_distribution_is_even(self):
        r = ShardRouter(16)
        counts = [0] * 16
        for i in range(8000):
            counts[r.route(f"FAC-{i}")] += 1
        assert max(counts) / min(counts) < 2.0

    def test_reshard_is_a_stated_epoch(self):
        r = ShardRouter(4)
        with pytest.raises(Error, match="reason"):
            r.reshard(8, by="ops", day=10, reason="")
        epoch = r.reshard(8, by="ops", day=10,
                          reason="pilot grew past one shard")
        assert epoch == 2 and r.shard_count == 8
        assert r.epochs[-1]["reason"] == "pilot grew past one shard"


class TestReports:
    def test_conflict_report_memoized_but_current(self):
        gate = UploadGate(_registry())
        gate.register_validator(
            "emar-administration",
            lambda m, day: {"verdict": "conflict", "reason": "x"})
        gate.submit(_mutation("c1"), principal=_principal(), day=1)
        r1 = gate.conflict_report()
        assert r1["conflicts"] == 1
        gate.submit(_mutation("c2"), principal=_principal(), day=1)
        r2 = gate.conflict_report()
        assert r2["conflicts"] == 2
        assert r1["sha256"] != r2["sha256"]
