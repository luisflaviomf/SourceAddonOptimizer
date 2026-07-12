import json
import importlib
from pathlib import Path
import unittest
from copy import deepcopy

from benchmarks.lvs_models.build_meshopt_direct_position_v1 import build
from maximum_optimizer.direct_evidence import seal_evidence


class PositionEvidenceTests(unittest.TestCase):
    def test_committed_position_evidence_is_strict_and_unverified(self):
        module_path = Path("maximum_optimizer/position_evidence.py")
        self.assertTrue(module_path.is_file())
        evidence = importlib.import_module("maximum_optimizer.position_evidence")
        self.assertTrue(hasattr(evidence, "load_position_evidence"))
        path = Path("benchmarks/lvs_models/meshopt_direct_position_v1.json")
        payload = evidence.load_position_evidence(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(build(), payload)
        self.assertEqual(payload["quality_status"], "unverified")
        self.assertFalse(payload["decision"]["winner"])
        for mutation in ("locked", "bytes", "charger", "digest"):
            hostile = deepcopy(payload)
            if mutation == "locked":
                hostile["wheel_records"][0]["counts"]["locked_vertices"] = hostile["wheel_records"][0]["counts"]["wedge_vertices"] + 1
            elif mutation == "bytes":
                hostile["wheel_records"][0]["counts"]["compiled_bytes"] += 1
            elif mutation == "charger":
                hostile["charger_probe"]["compiled_available"] = True
            else:
                hostile["evidence_sha256"] = "0" * 64
            if mutation != "digest":
                hostile = seal_evidence(hostile)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                evidence.load_position_evidence(hostile)


if __name__ == "__main__":
    unittest.main()
