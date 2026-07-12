from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from maximum_optimizer.ratio_sweep_evidence import parse_ratio_sweep_evidence


def reseal(payload: dict) -> dict:
    payload.pop("evidence_sha256", None)
    canonical = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    payload["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


class AdaptiveRatioSweepEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            Path("benchmarks/lvs_models/blender_adaptive_ratio_sweep_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def test_committed_sweep_is_portable_and_selects_actual_byte_minima(self) -> None:
        evidence = parse_ratio_sweep_evidence(self.payload)
        by_family = {item["family_id"]: item for item in evidence["families"]}
        self.assertEqual(
            evidence["provenance"]["run_base_commit"],
            "b9bf711c5706d057d895defb4d967cfdd91d53e2",
        )
        self.assertIn("benchmarks/lvs_models/blender_adaptive_v1.json", evidence["provenance"]["inputs"])
        self.assertEqual(len(evidence["excluded_runs"]), 1)
        self.assertEqual(evidence["excluded_runs"][0]["available_hashes"], {})
        self.assertTrue(evidence["excluded_runs"][0]["path_reused_after_clean"])
        self.assertEqual(by_family["dodge_charger"]["decision"]["minimum_bytes_ratio"], 0.30)
        self.assertEqual(by_family["toyota_supra"]["decision"]["minimum_bytes_ratio"], 0.25)
        self.assertEqual(by_family["nissan_skyline_gtr32"]["decision"]["minimum_bytes_ratio"], 0.25)
        self.assertEqual(by_family["dodge_charger"]["decision"]["sampled_pareto_ratios"], [0.30])
        self.assertEqual(by_family["toyota_supra"]["decision"]["sampled_pareto_ratios"], [0.35, 0.25])
        self.assertEqual(by_family["nissan_skyline_gtr32"]["decision"]["sampled_pareto_ratios"], [0.35, 0.25])
        self.assertEqual(
            [item["candidate"]["total_bytes"] for item in by_family["dodge_charger"]["attempts"]],
            [12_143_971, 10_896_955, 11_071_545],
        )
        self.assertEqual(
            [item["fallback_summary"]["count"] for item in by_family["dodge_charger"]["attempts"]],
            [2, 2, 3],
        )
        self.assertEqual(evidence["quality"]["status"], "unverified")
        for family in evidence["families"]:
            for attempt in family["attempts"]:
                self.assertEqual(
                    {item["path"].split(".", 1)[1] for item in attempt["candidate"]["artifacts"]},
                    {"mdl", "vvd", "dx80.vtx", "dx90.vtx", "phy"},
                )
                self.assertEqual(attempt["qc"]["status"], "pass")
                self.assertRegex(attempt["qc"]["source"]["sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(attempt["qc"]["optimized"]["fingerprint_sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(
                    attempt["provenance_files"]["compile_log"]["availability"],
                    "external-not-committed",
                )

    def test_rejects_tampered_digest_nonexact_fallback_and_false_minimum(self) -> None:
        mutations = []
        digest = copy.deepcopy(self.payload)
        digest["families"][0]["attempts"][0]["candidate"]["total_bytes"] += 1
        mutations.append(digest)
        fallback = copy.deepcopy(self.payload)
        fallback["families"][0]["attempts"][0]["fallbacks"][0]["output_sha256"] = "0" * 64
        mutations.append(reseal(fallback))
        decision = copy.deepcopy(self.payload)
        decision["families"][0]["decision"]["ratio"] = 0.35
        mutations.append(reseal(decision))
        quality = copy.deepcopy(self.payload)
        quality["quality"]["status"] = "pass"
        mutations.append(reseal(quality))
        sidecar = copy.deepcopy(self.payload)
        sidecar["families"][0]["attempts"][0]["candidate"]["artifacts"].pop()
        sidecar["families"][0]["attempts"][0]["candidate"]["total_bytes"] = sum(
            item["size_bytes"]
            for item in sidecar["families"][0]["attempts"][0]["candidate"]["artifacts"]
        )
        mutations.append(reseal(sidecar))
        qc = copy.deepcopy(self.payload)
        qc["families"][0]["attempts"][0]["qc"]["source"]["fingerprint"]["model_name"] = "tampered"
        mutations.append(reseal(qc))
        excluded = copy.deepcopy(self.payload)
        excluded["excluded_runs"][0]["run_id"] = excluded["families"][0]["attempts"][0]["run_id"]
        mutations.append(reseal(excluded))
        runtime = copy.deepcopy(self.payload)
        runtime["provenance"]["execution_snapshot"]["runtime_scripts"]["maximum_optimizer/qc_graph.py"]["checkout_sha256"] = "0" * 64
        mutations.append(reseal(runtime))
        baseline_input = copy.deepcopy(self.payload)
        baseline_input["provenance"]["inputs"]["benchmarks/lvs_models/blender_adaptive_v1.json"] = "0" * 64
        mutations.append(reseal(baseline_input))
        for mutation in mutations:
            with self.subTest(), self.assertRaises(ValueError):
                parse_ratio_sweep_evidence(mutation)


if __name__ == "__main__":
    unittest.main()
