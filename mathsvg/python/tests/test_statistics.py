from __future__ import annotations

import unittest

from mathsvg.python.analysis.statistics import (
    paired_bootstrap_interval,
    percentile,
    summarize,
)


class StatisticsTests(unittest.TestCase):
    def test_summary(self) -> None:
        result = summarize([1, 2, 3, 4, 5])
        self.assertEqual(result.count, 5)
        self.assertEqual(result.mean, 3.0)
        self.assertEqual(result.median, 3.0)
        self.assertAlmostEqual(result.standard_deviation, 1.5811388300841898)
        self.assertLess(result.ci95_low, result.mean)
        self.assertGreater(result.ci95_high, result.mean)

    def test_singleton_interval(self) -> None:
        result = summarize([7])
        self.assertEqual((result.ci95_low, result.ci95_high), (7.0, 7.0))

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([0.0, 10.0], 0.25), 2.5)

    def test_bootstrap_is_seeded(self) -> None:
        pairs = [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]

        def statistic(rows: list[tuple[float, float]]) -> float:
            return sum(right - left for left, right in rows) / len(rows)

        first = paired_bootstrap_interval(
            pairs, statistic, seed=1297748005, replicates=200
        )
        second = paired_bootstrap_interval(
            pairs, statistic, seed=1297748005, replicates=200
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
