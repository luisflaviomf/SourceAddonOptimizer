from __future__ import annotations

import math
import unittest

from benchmarks.lvs_models.build_calibration_corpus_v1 import distribution


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


if __name__ == "__main__":
    unittest.main()
