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
                         ("pontiac_transam_wheel", "dodge_charger",
                          "dodge_monaco_police", "toyota_supra",
                          "nissan_skyline_gtr32", "vw_beetle", "vw_touareg"))
        by_family = {record["family_id"]: record for record in evidence["records"]}
        self.assertEqual(by_family["nissan_skyline_gtr32"]["status"], "compiled")
        self.assertGreater(
            by_family["nissan_skyline_gtr32"]["candidate"]["total_bytes"],
            by_family["nissan_skyline_gtr32"]["baseline"]["total_bytes"],
        )
        self.assertEqual(by_family["dodge_monaco_police"]["status"], "compiled")
        self.assertEqual(
            by_family["toyota_supra"]["fallbacks"][0]["reason"],
            "normal must be non-zero",
        )
        self.assertLess(
            by_family["toyota_supra"]["candidate"]["total_bytes"],
            by_family["toyota_supra"]["baseline"]["total_bytes"],
        )
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
        original = copy.deepcopy(self.payload)
        original["records"][2]["original"]["total_bytes"] += 1
        mutations.append(original)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parse_compiler_aware_evidence(mutation)


if __name__ == "__main__":
    unittest.main()
