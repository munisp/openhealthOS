"""Tests for the offline-sync demo seeder — determinism and gates."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _pkg in ("syncmesh", "emar", "localepack"):
    sys.path.insert(0, os.path.join(_HERE, "..", _pkg))

from demo_sync import VERSION, export_all, seed_sync  # noqa: E402


class TestSyncDemo:
    def test_determinism_byte_for_byte(self):
        a, b = seed_sync("ng"), seed_sync("ng")
        assert a == b
        assert a["sha256"] == b["sha256"]

    def test_both_locales_seed(self):
        for code in ("ng", "cv"):
            p = seed_sync(code)
            assert p["locale"] == code
            assert p["sha256"]

    def test_the_three_verdicts_fire(self):
        p = seed_sync("cv")
        kinds = {v["key"]: v["verdict"] for v in p["verdicts"]}
        assert kinds["up-1"] == "applied"
        assert kinds["up-2"] == "refused"
        assert kinds["up-3"] == "conflict"

    def test_refusal_carries_the_engine_reason(self):
        p = seed_sync("ng")
        refused = [v for v in p["verdicts"] if v["verdict"] == "refused"]
        assert len(refused) == 1
        assert "barcode" in refused[0]["reason"].lower()

    def test_replay_is_flagged_not_reapplied(self):
        p = seed_sync("ng")
        replays = [v for v in p["verdicts"] if v.get("replayed")]
        assert len(replays) == 1
        assert replays[0]["verdict"] == "applied"
        # the dose slot was recorded exactly once
        assert p["mar_state"]["mar-1-S1"] == "given"

    def test_conflict_names_its_policy(self):
        p = seed_sync("cv")
        conflict = [v for v in p["verdicts"] if v["verdict"] == "conflict"]
        assert conflict[0]["policy"] == "amend-not-edit"
        assert p["conflict_report"]["by_policy"]["amend-not-edit"] == 1

    def test_checkpoint_chain_verifies(self):
        p = seed_sync("ng")
        assert p["checkpoint"]["chain_ok"] is True
        assert p["checkpoint"]["batches"] == 2
        assert len(p["checkpoint"]["head"]) == 64

    def test_retention_evictions_carry_reasons(self):
        p = seed_sync("ng")
        assert p["retention"]["evicted"]
        assert all(e["reason"] for e in p["retention"]["evicted"])

    def test_export_all_writes_both_locales(self, tmp_path):
        assert export_all(str(tmp_path)) is True
        for code in ("ng", "cv"):
            assert os.path.exists(os.path.join(
                str(tmp_path), f"sync.{code}.json"))

    def test_version_stamp(self):
        assert VERSION == "syncdemo@1.0.0"
