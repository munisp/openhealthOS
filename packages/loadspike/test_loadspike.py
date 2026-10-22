"""CI-sized correctness gates for the loadspike benches. Small N so the
suite stays fast; __main__ runs carry the real volume. Timing is never
asserted — only that numbers were produced and invariants held."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_stream  # noqa: E402
import bench_lakehouse  # noqa: E402


class TestLoadspike(unittest.TestCase):
    def test_stream_bench_correct_at_ci_size(self):
        r = bench_stream.bench(n=5_000)
        self.assertEqual(r["events"], 5_000)
        self.assertGreater(r["produce_eps"], 0)
        self.assertGreater(r["consume_eps"], 0)

    def test_lakehouse_bench_correct_at_ci_size(self):
        r = bench_lakehouse.bench(n=3_000)
        self.assertEqual(sum(r["silver_partitions"].values()), 3_000)
        self.assertGreater(r["build_eps"], 0)


if __name__ == "__main__":
    unittest.main()
