"""test_syncscale2.py — ceiling-closure wave tests (syncmesh@1.2.0).

Covers the four mechanisms that answer the honest ceiling notes:
- DbapiStore        — the Postgres-ready verdict store on the same seam
- WalCoalescer      — group commit; THE FLUSH IS THE ACK
- CheckpointCache   — read fan-out for checkpoint polling
- ReplicationSlotGuard — WAL-pin discipline with stated backpressure
"""

import os
import sqlite3
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from syncmesh import (CheckpointCache, DbapiStore, Error,  # noqa: E402
                      Principal, ReplicationSlotGuard, SyncRuleRegistry,
                      UploadGate, WalCoalescer)


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


# ── DbapiStore ───────────────────────────────────────────────────

class TestDbapiStore:
    """sqlite3 in qmark mode stands in for psycopg in format mode —
    the SQL is the same, only the placeholder differs, and the
    paramstyle is declared, never guessed."""

    def _store(self):
        return DbapiStore(sqlite3.connect(":memory:"), paramstyle="qmark")

    def test_durable_flag_is_honest(self):
        assert self._store().durable is True

    def test_put_get_roundtrip(self):
        s = self._store()
        s.put("k1", {"verdict": "applied", "seq": 7}, day=100)
        got = s.get("k1")
        assert got == {"verdict": "applied", "seq": 7}

    def test_missing_key_returns_none(self):
        assert self._store().get("absent") is None

    def test_upsert_replaces_not_duplicates(self):
        s = self._store()
        s.put("k1", {"verdict": "applied", "seq": 7}, day=100)
        s.put("k1", {"verdict": "replayed", "seq": 7}, day=100)
        assert len(s) == 1
        assert s.get("k1")["verdict"] == "replayed"

    def test_prune_is_stated(self):
        s = self._store()
        for i in range(10):
            s.put(f"k{i}", {"verdict": "applied"}, day=90 + i)
        evicted = s.prune(before_day=95)
        assert evicted == 5 and len(s) == 5

    def test_len(self):
        s = self._store()
        assert len(s) == 0
        s.put("k1", {"v": 1}, day=1)
        assert len(s) == 1

    def test_unknown_paramstyle_refused(self):
        with pytest.raises(Error, match="declared, not guessed"):
            DbapiStore(sqlite3.connect(":memory:"), paramstyle="named")

    def test_gate_survives_process_restart(self):
        """The whole point of the seam: verdicts outlive the gate."""
        conn = sqlite3.connect(":memory:")
        g1 = UploadGate(_registry(),
                        store=DbapiStore(conn, paramstyle="qmark"))
        g1.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        v1 = g1.submit(_mutation("up-1"), principal=_principal(), day=1)
        assert v1["verdict"] == "applied" and "replayed" not in v1
        # a NEW gate process on the SAME connection — the replay is
        # served from the durable store, not from memory
        g2 = UploadGate(_registry(),
                        store=DbapiStore(conn, paramstyle="qmark"))
        g2.register_validator(
            "emar-administration", lambda m, day: {"verdict": "applied"})
        v2 = g2.submit(_mutation("up-1"), principal=_principal(), day=2)
        assert v2["verdict"] == "applied" and v2["replayed"] is True
        assert len(g2.applied) == 0  # never re-applied


# ── WalCoalescer ────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class TestWalCoalescer:
    def _coalescer(self, sink, **kw):
        kw.setdefault("clock", FakeClock())
        return WalCoalescer(sink, **kw)

    def test_flush_is_the_ack(self):
        """No ack before the flush — submit returns None while the
        buffer holds the mutation; the ack list arrives WITH the flush."""
        flushed = []
        c = self._coalescer(lambda b: flushed.append(list(b)) or b,
                            max_count=2)
        assert c.submit({"n": 1}) is None          # buffered, not acked
        acks = c.submit({"n": 2})                  # trips the count
        assert acks == [{"n": 1}, {"n": 2}]
        assert flushed == [[{"n": 1}, {"n": 2}]]

    def test_count_threshold_flushes(self):
        flushed = []
        c = self._coalescer(lambda b: flushed.append(len(b)) or b,
                            max_count=3)
        for i in range(7):
            c.submit({"n": i})
        assert flushed == [3, 3]                   # two group commits
        assert c.stats()["buffered"] == 1

    def test_bytes_threshold_flushes(self):
        flushed = []
        c = self._coalescer(lambda b: flushed.append(len(b)) or b,
                            max_count=10**9, max_bytes=40)
        c.submit({"payload": "a" * 20})
        c.submit({"payload": "b" * 20})            # crosses 40 bytes
        assert flushed == [2]

    def test_window_closes_stale_buffer(self):
        flushed = []
        clock = FakeClock()
        c = WalCoalescer(lambda b: flushed.append(len(b)) or b,
                         max_count=100, window_seconds=0.005,
                         clock=clock)
        c.submit({"n": 1})
        assert c.tick() is None                    # window still open
        clock.advance(0.006)
        acks = c.tick()                            # window closed
        assert acks == [{"n": 1}] and flushed == [1]
        assert c.stats()["buffered"] == 0

    def test_quiet_ward_not_held_hostage(self):
        """A trickle of writes still flushes on the window."""
        flushed = []
        clock = FakeClock()
        c = WalCoalescer(lambda b: flushed.append(len(b)) or b,
                         window_seconds=0.005, clock=clock)
        c.submit({"n": 1})
        clock.advance(1.0)
        c.tick()
        assert flushed == [1]

    def test_empty_flush_is_a_noop(self):
        c = self._coalescer(lambda b: b)
        assert c.flush() == [] and c.stats()["flushes"] == 0

    def test_thresholds_are_declared(self):
        with pytest.raises(Error, match="declared positive"):
            WalCoalescer(lambda b: b, max_count=0)
        with pytest.raises(Error, match="declared positive"):
            WalCoalescer(lambda b: b, window_seconds=0)

    def test_group_commit_ratio(self):
        """1,000 mutations through max_count=500 → exactly 2 WAL
        flushes, not 1,000. That is the ceiling moving."""
        flushed = []
        c = self._coalescer(lambda b: flushed.append(len(b)) or b,
                            max_count=500)
        for i in range(1000):
            c.submit({"n": i})
        assert flushed == [500, 500] and c.stats()["flushes"] == 2


# ── CheckpointCache ─────────────────────────────────────────────

class TestCheckpointCache:
    def test_miss_then_hit(self):
        c = CheckpointCache()
        assert c.get("ward:a", fingerprint="fp1") is None
        c.put("ward:a", fingerprint="fp1", snapshot={"head": 9})
        assert c.get("ward:a", fingerprint="fp1") == {"head": 9}
        assert c.stats() == {"hits": 1, "misses": 1, "hit_ratio": 0.5}

    def test_stale_fingerprint_misses(self):
        """A checkpoint snapshot is served only against its own head —
        a moved head is a miss, never a stale read."""
        c = CheckpointCache()
        c.put("ward:a", fingerprint="fp1", snapshot={"head": 9})
        assert c.get("ward:a", fingerprint="fp2") is None
        assert c.stats()["misses"] == 1

    def test_invalidate_on_append(self):
        c = CheckpointCache()
        c.put("ward:a", fingerprint="fp1", snapshot={"head": 9})
        c.invalidate("ward:a")
        assert c.get("ward:a", fingerprint="fp1") is None

    def test_hit_ratio_is_reported_not_assumed(self):
        c = CheckpointCache()
        c.put("b", fingerprint="f", snapshot={})
        for _ in range(3):
            c.get("b", fingerprint="f")
        c.get("b", fingerprint="moved")
        assert c.stats()["hit_ratio"] == 0.75

    def test_empty_cache_stats_are_honest(self):
        assert CheckpointCache().stats() == {
            "hits": 0, "misses": 0, "hit_ratio": None}

    def test_polling_storm_is_carried_by_the_cache(self):
        """10,000 polls between writes: one miss + 9,999 hits — the
        primary answers a checkpoint poll once per write, not once
        per device per poll."""
        c = CheckpointCache()
        assert c.get("ward:a", fingerprint="fp1") is None  # the one miss
        c.put("ward:a", fingerprint="fp1", snapshot={"head": 9})
        for _ in range(9999):
            c.get("ward:a", fingerprint="fp1")
        s = c.stats()
        assert s["hits"] == 9999 and s["misses"] == 1
        assert s["hit_ratio"] == 0.9999


# ── ReplicationSlotGuard ────────────────────────────────────────

class TestReplicationSlotGuard:
    def test_within_budget_admits(self):
        g = ReplicationSlotGuard(1 << 20, reader=lambda slot: 1024)
        out = g.admit("powersync_1")
        assert out == {"admitted": True, "lag_bytes": 1024}
        assert g.refusals == []

    def test_beyond_budget_refuses_and_states(self):
        g = ReplicationSlotGuard(100, reader=lambda slot: 4096)
        out = g.admit("powersync_1", day=42)
        assert out["admitted"] is False and out["lag_bytes"] == 4096
        assert "catch-up" in out["reason"]
        r = g.refusals[-1]
        assert r["slot"] == "powersync_1" and r["day"] == 42
        assert r["budget_bytes"] == 100

    def test_budget_is_declared(self):
        with pytest.raises(Error, match="declared positive"):
            ReplicationSlotGuard(0, reader=lambda s: 0)

    def test_lag_moves_with_the_reader(self):
        """The guard answers from the reader each time — a draining
        slot is admitted again without any state to reset."""
        lags = [4096, 10]
        g = ReplicationSlotGuard(100, reader=lambda slot: lags.pop(0))
        assert g.admit("s")["admitted"] is False
        assert g.admit("s")["admitted"] is True
