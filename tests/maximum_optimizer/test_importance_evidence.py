from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from maximum_optimizer.importance_evidence import parse_importance_evidence


class ImportanceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(Path(
            "benchmarks/lvs_models/blender_importance_map_v1.json"
        ).read_text(encoding="utf-8"))

    def test_committed_experiment_is_smaller_but_honestly_rejected(self) -> None:
        evidence = parse_importance_evidence(self.payload)
        self.assertLess(evidence["candidate"]["compiled"]["total_bytes"],
                        evidence["baseline"]["compiled"]["total_bytes"])
        self.assertGreater(evidence["candidate"]["raw_clay"]["max_edge_error"],
                           evidence["baseline"]["raw_clay"]["max_edge_error"])
        self.assertFalse(evidence["decision"]["winner"])
        self.assertEqual(evidence["decision"]["status"], "rejected-visible-boundary-regression")

    def test_rejects_false_winner_or_compiled_total_drift(self) -> None:
        winner = copy.deepcopy(self.payload)
        winner["decision"]["winner"] = True
        drift = copy.deepcopy(self.payload)
        drift["candidate"]["compiled"]["total_bytes"] += 1
        for payload in (winner, drift):
            with self.subTest(), self.assertRaises(ValueError):
                parse_importance_evidence(payload)


if __name__ == "__main__":
    unittest.main()
