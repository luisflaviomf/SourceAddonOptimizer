import json
import importlib
from pathlib import Path
import unittest


class PositionEvidenceTests(unittest.TestCase):
    def test_committed_position_evidence_is_strict_and_unverified(self):
        module_path = Path("maximum_optimizer/position_evidence.py")
        self.assertTrue(module_path.is_file())
        evidence = importlib.import_module("maximum_optimizer.position_evidence")
        self.assertTrue(hasattr(evidence, "load_position_evidence"))
        path = Path("benchmarks/lvs_models/meshopt_direct_position_v1.json")
        payload = evidence.load_position_evidence(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(payload["quality_status"], "unverified")
        self.assertFalse(payload["decision"]["winner"])


if __name__ == "__main__":
    unittest.main()
