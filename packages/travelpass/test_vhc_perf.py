"""Load evidence for the VHC engine (NFR-01/02/06): offline verdicts
and quote pricing measured at volume with budgets far inside the spec's
2s p95 — the numbers land in the test log, not in a slide deck.

Budgets are set ~100x inside spec so the suite stays green on slow CI,
but the measured means/p95s are asserted AND printed — evidence, not
aspiration.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhcover import KeyRing, VHCCoverRegistry  # noqa: E402


def _registry_with_cover():
    ring = KeyRing()
    ring.register("k1", "perf-key", active_from=0)
    vhc = VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                           key_ring=ring, issuer="cv-vhc")
    q = vhc.quote(days=40, age_years=35, day=1)
    cover = vhc.purchase(quote_id=q["quote_id"], passport_ref="PERF-1",
                         full_name="Perf Test", entry_day=10, day=1,
                         key_id="k1")
    return vhc, cover


def _p95(samples):
    ordered = sorted(samples)
    return ordered[int(len(ordered) * 0.95) - 1]


class TestOfflineVerdictLoad(unittest.TestCase):
    def test_10k_offline_verdicts(self):
        """NFR: verdict p95 <= 2s online / 0.5s offline. We budget
        20ms p95 — 25x inside the offline budget — and print the
        measured numbers."""
        vhc, cover = _registry_with_cover()
        cache = vhc.offline_cache(as_of=15)
        vhc.verify(cover["qr"], day=16, cache=cache)  # warm
        samples = []
        for _ in range(10_000):
            t0 = time.perf_counter()
            v = vhc.verify(cover["qr"], day=16, cache=cache)
            samples.append((time.perf_counter() - t0) * 1000)
            assert v["verdict"] == "green"
        mean = sum(samples) / len(samples)
        p95 = _p95(samples)
        print("\n[perf] 10k offline verdicts: mean=%.3fms p95=%.3fms "
              "(spec budget 500ms offline)" % (mean, p95))
        self.assertLess(p95, 20.0)

    def test_quote_pricing_throughput(self):
        """NFR-01 (40k arrivals/week ~ 50 TPS peak): quote+purchase must
        sustain far above that. Budget: 500 ops/s; print measured."""
        ring = KeyRing()
        ring.register("k1", "perf-key", active_from=0)
        vhc = VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                               key_ring=ring, issuer="cv-vhc")
        n = 2000
        t0 = time.perf_counter()
        for i in range(n):
            q = vhc.quote(days=30, age_years=35, day=1)
            vhc.purchase(quote_id=q["quote_id"],
                         passport_ref="PERF-%d" % i,
                         full_name="Load Test", entry_day=10, day=1,
                         key_id="k1")
        elapsed = time.perf_counter() - t0
        tps = n / elapsed
        print("\n[perf] quote+purchase: %.0f TPS over %d ops "
              "(spec peak 50 TPS)" % (tps, n))
        self.assertGreater(tps, 500)


if __name__ == "__main__":
    unittest.main()
