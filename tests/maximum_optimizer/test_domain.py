import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from maximum_optimizer.domain import CandidateSpec, CompiledSizeSnapshot, SearchBudget


class DomainTests(unittest.TestCase):
    def test_candidate_is_immutable_and_has_stable_key(self):
        candidate = CandidateSpec("meshopt-r035", "meshoptimizer", 0.35, 0.01, "transfer-v1")
        self.assertEqual(candidate.cache_payload()["target_ratio"], 0.35)
        with self.assertRaises(FrozenInstanceError):
            candidate.target_ratio = 0.5

    def test_snapshot_rejects_inconsistent_total(self):
        with self.assertRaisesRegex(ValueError, "total_bytes"):
            CompiledSizeSnapshot(Path("models"), 7, {".mdl": 3}, {}, ())

    def test_budget_defaults_are_bounded(self):
        budget = SearchBudget.experimental_default()
        self.assertEqual(budget.max_candidates, 18)
        self.assertGreater(budget.min_ratio_step, 0)


if __name__ == "__main__":
    unittest.main()
