from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from maximum_optimizer.compiler_aware_evidence import parse_compiler_aware_evidence


class CompilerAwareEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path("benchmarks/lvs_models/blender_adaptive_v1.json")
        self.payload = json.loads(self.path.read_text(encoding="utf-8"))

    def test_committed_evidence_is_portable_honest_and_self_consistent(self) -> None:
        evidence = parse_compiler_aware_evidence(self.payload)
        self.assertEqual(tuple(record["family_id"] for record in evidence["records"]),
                         ("pontiac_transam_wheel", "dodge_charger"))
        self.assertEqual(evidence["quality"]["status"], "unverified")
        self.assertRegex(evidence["quality"]["wheel_render_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(evidence["quality"]["charger_render_failure_log_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(evidence["decision"]["winner"])

    def test_rejects_false_winner_proxy_drift_and_nonexact_fallback(self) -> None:
        mutations = []
        winner = copy.deepcopy(self.payload)
        winner["decision"]["winner"] = True
        mutations.append(winner)
        proxy = copy.deepcopy(self.payload)
        proxy["records"][0]["proxy"]["predicted_bytes"] += 1
        mutations.append(proxy)
        fallback = copy.deepcopy(self.payload)
        fallback["records"][1]["fallbacks"][0]["output_sha256"] = "0" * 64
        mutations.append(fallback)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parse_compiler_aware_evidence(mutation)


if __name__ == "__main__":
    unittest.main()
