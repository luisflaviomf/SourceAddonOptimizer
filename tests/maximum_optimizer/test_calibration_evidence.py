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
        "pontiac_transam_wheel": "r050",
        "dodge_charger": "r030",
        "toyota_supra": "r015",
        "nissan_skyline_gtr32": "r020",
        "dodge_monaco_police": "hybrid-stable",
    }
    payload = {
        "schema_version": 2,
        "strategy": "lvs-calibration-corpus-v2",
        "status": "calibration-pending",
        "toolchain": {"blender": "c" * 64, "vtfcmd": "d" * 64},
        "implementation": {
            "render_previews.py": "e" * 64,
            "maximum_optimizer/visual_validation.py": "f" * 64,
            "benchmarks/lvs_models/run_visual_states.py": "1" * 64,
            "benchmarks/lvs_models/build_calibration_corpus_v1.py": "2" * 64,
        },
        "external_artifacts": {
            "monaco_accepted_composite_v1": {
                "file_sha256": "6" * 64,
                "canonical_payload_sha256": "7" * 64,
            },
        },
        "families": [
            {
                "family_id": family,
                "byte_denominators": {
                    "shipped_original": {
                        "kind": "shipped-original-compiled-family-v1",
                        "compiled": {
                            "total_bytes": 1200,
                            "artifacts": [
                                {"path": f"model.{index}", "size_bytes": 240, "sha256": "5" * 64}
                                for index in range(5)
                            ],
                        },
                    },
                    "roundtrip_control": {
                        "kind": "strict-roundtrip-control-compiled-family-v1",
                        "compiled": {
                            "total_bytes": 1000,
                            "artifacts": [
                                {"path": f"model.{index}", "size_bytes": 200, "sha256": "3" * 64}
                                for index in range(5)
                            ],
                        },
                    },
                },
                "byte_comparison": {
                    "candidate_bytes": 800,
                    "versus_shipped_original": {
                        "denominator_kind": "shipped-original-compiled-family-v1",
                        "denominator_bytes": 1200,
                        "saved_bytes": 400,
                        "reduction_fraction": 1 / 3,
                    },
                    "versus_roundtrip_control": {
                        "denominator_kind": "strict-roundtrip-control-compiled-family-v1",
                        "denominator_bytes": 1000,
                        "saved_bytes": 200,
                        "reduction_fraction": 0.2,
                    },
                },
                "baseline": {
                    "lane": "strict-region-paired",
                    "candidate_id": "roundtrip-control",
                    "compiled": {
                        "total_bytes": 1000,
                        "artifacts": [
                            {"path": f"model.{index}", "size_bytes": 200, "sha256": "3" * 64}
                            for index in range(5)
                        ],
                    },
                    "configurations": [_configuration(
                        "engine-default",
                        "strict-region-paired",
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
            metric: {"count": 5, "min": 0.01, "median": 0.01, "p95": 0.01, "max": 0.01}
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
    wheel["alternatives"] = [
        {
            "candidate_id": candidate_id,
            "evidence_kind": evidence_kind,
            "status": "rejected-research-alternative",
            "reason": "visible detail or boundary regression",
            "evidence": {
                "artifact_path": f"research://wheel/{candidate_id}",
                "artifact_sha256": "89abc"[index] * 64,
                "payload_sha256": "cdef0"[index] * 64,
                "artifact_availability": (
                    "archived-visual-only" if index == 0
                    else (
                        "committed-raw-clay-and-compiled"
                        if candidate_id == "importance-r035"
                        else "archived-visual-and-compiled"
                    )
                ),
                "quality_scope": quality_scope,
                "compiled": (
                    None if index == 0
                    else copy.deepcopy(wheel["candidate"]["compiled"])
                ),
                "winner": False,
            },
        }
        for index, (candidate_id, evidence_kind, quality_scope) in enumerate((
            ("r035", "strict-visual-rejection", "strict-region-paired"),
            ("v4-r035", "strict-visual-rejection", "strict-region-paired"),
            ("v4-r045", "strict-visual-rejection", "strict-region-paired"),
            ("v4-r0475", "strict-visual-rejection", "strict-region-paired"),
            (
                "importance-r035",
                "uncalibrated-raw-clay-rejection",
                "rim1-bind-8-views",
            ),
        ))
    ]
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
            parsed["families"][0]["alternatives"][0]["candidate_id"],
            "r035",
        )
        self.assertEqual(parsed["families"][0]["candidate"]["candidate_id"], "r050")
        self.assertEqual(
            parsed["families"][-1]["candidate"]["candidate_id"], "hybrid-stable"
        )
        self.assertTrue(all(
            not family["alternatives"] for family in parsed["families"][1:]
        ))
        self.assertTrue(all(
            family["baseline"]["candidate_id"] == "roundtrip-control"
            and family["baseline"]["lane"] == "strict-region-paired"
            for family in parsed["families"]
        ))

    def test_dual_byte_denominators_are_typed_and_derived(self) -> None:
        parsed = parse_calibration_evidence(_payload())
        for family in parsed["families"]:
            denominators = family["byte_denominators"]
            self.assertEqual(
                denominators["shipped_original"]["kind"],
                "shipped-original-compiled-family-v1",
            )
            self.assertEqual(
                denominators["roundtrip_control"]["compiled"], family["baseline"]["compiled"]
            )
            self.assertEqual(
                family["byte_comparison"]["candidate_bytes"],
                family["candidate"]["compiled"]["total_bytes"],
            )

        for mutate in (
            lambda item: item["families"][0]["byte_denominators"]["shipped_original"].__setitem__(
                "kind", "roundtrip"
            ),
            lambda item: item["families"][0]["byte_comparison"]["versus_shipped_original"].__setitem__(
                "reduction_fraction", 0.99
            ),
            lambda item: item["families"][0]["byte_denominators"]["roundtrip_control"]["compiled"].__setitem__(
                "total_bytes", 999
            ),
        ):
            changed = copy.deepcopy(_payload())
            mutate(changed)
            changed["evidence_sha256"] = canonical_calibration_evidence_hash(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                parse_calibration_evidence(changed)

    def test_external_composite_hashes_are_sealed(self) -> None:
        changed = copy.deepcopy(_payload())
        changed["external_artifacts"]["monaco_accepted_composite_v1"]["file_sha256"] = "8" * 64
        with self.assertRaisesRegex(ValueError, "seal"):
            parse_calibration_evidence(changed)

    def test_b050_aggregate_cannot_be_reintroduced_as_calibration_baseline(self) -> None:
        changed = copy.deepcopy(_payload())
        baseline = changed["families"][0]["baseline"]
        baseline["candidate_id"] = "b050"
        baseline["lane"] = "aggregate-appearance-anchor-not-structural-baseline"
        baseline["configurations"][0]["name"] = (
            "aggregate-appearance-anchor-not-structural-baseline:engine-default"
        )
        baseline["configurations"][0]["scope"] = (
            "aggregate-appearance-anchor-not-structural-baseline"
        )
        changed["evidence_sha256"] = canonical_calibration_evidence_hash(changed)

        with self.assertRaises(ValueError):
            parse_calibration_evidence(changed)

    def test_alternative_set_and_rejection_identity_are_fixed(self) -> None:
        for mutate in (
            lambda item: item["families"][0]["alternatives"].clear(),
            lambda item: item["families"][1]["alternatives"].append(
                copy.deepcopy(item["families"][0]["alternatives"][0])
            ),
            lambda item: item["families"][0]["alternatives"][0].__setitem__(
                "status", "winner"
            ),
            lambda item: item["families"][0]["alternatives"][4]["evidence"].__setitem__(
                "quality_scope", "strict-region-paired"
            ),
            lambda item: item["families"][0]["alternatives"][0]["evidence"].__setitem__(
                "compiled", copy.deepcopy(item["families"][0]["candidate"]["compiled"])
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

    def test_resealed_ordered_but_false_distribution_is_rejected(self) -> None:
        changed = copy.deepcopy(_payload())
        changed["baseline_distribution"]["rgb_mae"] = {
            "count": 5,
            "min": 7.0,
            "median": 7.0,
            "p95": 7.0,
            "max": 7.0,
        }
        changed["evidence_sha256"] = canonical_calibration_evidence_hash(changed)

        with self.assertRaisesRegex(ValueError, "does not match baseline metrics"):
            parse_calibration_evidence(changed)


if __name__ == "__main__":
    unittest.main()
