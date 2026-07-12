from __future__ import annotations

import copy
import unittest

from maximum_optimizer.calibration_evidence import (
    CALIBRATION_FAMILIES,
    CALIBRATION_METRICS,
    canonical_calibration_evidence_hash,
    parse_calibration_evidence,
)


def _configuration(name: str, scope: str) -> dict:
    return {
        "name": name,
        "scope": scope,
        "bodygroups": {},
        "reference_manifest_sha256": "a" * 64,
        "candidate_manifest_sha256": "b" * 64,
        "metrics": {metric: 0.01 for metric in CALIBRATION_METRICS},
        "missing_materials": {"reference": 0, "candidate": 0},
        "geometry_audit": {
            side: {
                "input_triangles": 100,
                "kept_triangles": 99,
                "filtered_degenerate_triangles": 1,
                "max_filtered_fraction": 0.01,
            }
            for side in ("reference", "candidate")
        },
        "top_regions": [
            {"source": "body.smd", "surface_bidirectional_p95": 0.01, "surface_max": 0.02}
        ],
    }


def _payload() -> dict:
    candidates = {
        "pontiac_transam_wheel": "r040",
        "dodge_charger": "r030",
        "toyota_supra": "r025",
        "nissan_skyline_gtr32": "r025",
        "dodge_monaco_police": "r040",
    }
    payload = {
        "schema_version": 1,
        "strategy": "lvs-calibration-corpus-v1",
        "status": "calibration-pending",
        "toolchain": {"blender": "c" * 64, "vtfcmd": "d" * 64},
        "implementation": {
            "render_previews.py": "e" * 64,
            "maximum_optimizer/visual_validation.py": "f" * 64,
            "benchmarks/lvs_models/run_visual_states.py": "1" * 64,
            "benchmarks/lvs_models/build_calibration_corpus_v1.py": "2" * 64,
        },
        "families": [
            {
                "family_id": family,
                "baseline": {
                    "lane": "aggregate-appearance-anchor-not-structural-baseline",
                    "candidate_id": "b050",
                    "compiled": {
                        "total_bytes": 1000,
                        "artifacts": [
                            {"path": f"model.{index}", "size_bytes": 200, "sha256": "3" * 64}
                            for index in range(5)
                        ],
                    },
                    "configurations": [_configuration(
                        "aggregate-appearance-anchor-not-structural-baseline:engine-default",
                        "aggregate-appearance-anchor-not-structural-baseline",
                    )],
                },
                "candidate": {
                    "lane": "strict-region-paired",
                    "candidate_id": candidates[family],
                    "compiled": {
                        "total_bytes": 800,
                        "artifacts": [
                            {"path": f"model.{index}", "size_bytes": 160, "sha256": "4" * 64}
                            for index in range(5)
                        ],
                    },
                    "configurations": [_configuration("engine-default", "strict-region-paired")],
                },
            }
            for family in CALIBRATION_FAMILIES
        ],
        "baseline_distribution": {
            metric: {"count": 5, "min": 0.0, "median": 0.01, "p95": 0.02, "max": 0.03}
            for metric in CALIBRATION_METRICS
        },
        "decision": {
            "winner": False,
            "status": "calibration-pending",
            "reason": "raw corpus distributions are not calibrated acceptance thresholds",
        },
    }
    for family in payload["families"]:
        family["alternatives"] = []
    wheel = payload["families"][0]
    alternative_lane = copy.deepcopy(wheel["candidate"])
    alternative_lane["candidate_id"] = "r035"
    wheel["alternatives"] = [{
        "lane": alternative_lane,
        "status": "rejected-research-alternative",
        "reason": "smaller than r040 but visually dominated r040 and showed manual wheel faceting",
    }]
    payload["evidence_sha256"] = canonical_calibration_evidence_hash(payload)
    return payload


class CalibrationEvidenceTests(unittest.TestCase):
    def test_schema_requires_five_fixed_families_and_honest_lanes(self) -> None:
        parsed = parse_calibration_evidence(_payload())
        self.assertEqual(
            tuple(item["family_id"] for item in parsed["families"]),
            CALIBRATION_FAMILIES,
        )
        self.assertFalse(parsed["decision"]["winner"])
        self.assertEqual(parsed["status"], "calibration-pending")
        self.assertEqual(
            parsed["families"][0]["alternatives"][0]["lane"]["candidate_id"],
            "r035",
        )
        self.assertTrue(all(
            not family["alternatives"] for family in parsed["families"][1:]
        ))

    def test_alternative_set_and_rejection_identity_are_fixed(self) -> None:
        for mutate in (
            lambda item: item["families"][0]["alternatives"].clear(),
            lambda item: item["families"][1]["alternatives"].append(
                copy.deepcopy(item["families"][0]["alternatives"][0])
            ),
            lambda item: item["families"][0]["alternatives"][0].__setitem__(
                "status", "winner"
            ),
        ):
            changed = copy.deepcopy(_payload())
            mutate(changed)
            changed["evidence_sha256"] = canonical_calibration_evidence_hash(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                parse_calibration_evidence(changed)

    def test_seal_rejects_metric_manifest_family_and_decision_mutations(self) -> None:
        mutations = []
        for mutate in (
            lambda item: item["families"][0]["candidate"]["configurations"][0]["metrics"].__setitem__("rgb_mae", 9.0),
            lambda item: item["families"][0]["baseline"]["configurations"][0].__setitem__("candidate_manifest_sha256", "0" * 64),
            lambda item: item["families"].reverse(),
            lambda item: item["decision"].__setitem__("winner", True),
        ):
            changed = copy.deepcopy(_payload())
            mutate(changed)
            mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                parse_calibration_evidence(changed)

    def test_resealed_invalid_distribution_or_missing_materials_fails_schema(self) -> None:
        for changed in (copy.deepcopy(_payload()), copy.deepcopy(_payload())):
            if changed is not None and changed["baseline_distribution"]["rgb_mae"]["count"] == 5:
                changed["baseline_distribution"]["rgb_mae"]["min"] = 1.0
                changed["baseline_distribution"]["rgb_mae"]["max"] = 0.0
            changed["evidence_sha256"] = canonical_calibration_evidence_hash(changed)
            with self.assertRaises(ValueError):
                parse_calibration_evidence(changed)
            break
        missing = copy.deepcopy(_payload())
        missing["families"][0]["candidate"]["configurations"][0]["missing_materials"]["candidate"] = 1
        missing["evidence_sha256"] = canonical_calibration_evidence_hash(missing)
        with self.assertRaisesRegex(ValueError, "missing materials"):
            parse_calibration_evidence(missing)


if __name__ == "__main__":
    unittest.main()
