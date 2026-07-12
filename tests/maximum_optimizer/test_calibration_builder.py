from __future__ import annotations

import math
import unittest
from pathlib import Path

from benchmarks.lvs_models.build_calibration_corpus_v1 import distribution
from benchmarks.lvs_models.run_visual_states import short_texture_cache_root


class CalibrationBuilderTests(unittest.TestCase):
    def test_distribution_is_deterministic_and_uses_interpolated_p95(self) -> None:
        self.assertEqual(distribution([4, 0, 2, 1, 3]), {
            "count": 5,
            "min": 0.0,
            "median": 2.0,
            "p95": 3.8,
            "max": 4.0,
        })

    def test_distribution_rejects_empty_negative_or_nonfinite_values(self) -> None:
        for values in ([], [-1], [math.inf], [math.nan]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                distribution(values)

    def test_visual_runner_cache_is_short_and_deterministic(self) -> None:
        first = short_texture_cache_root(Path("C:/very/long/run/path"))
        second = short_texture_cache_root(Path("C:/very/long/run/path"))
        self.assertEqual(first, second)
        self.assertEqual(first.parent.name, "maximum-vtf-cache")
        self.assertEqual(len(first.name), 16)


if __name__ == "__main__":
    unittest.main()
