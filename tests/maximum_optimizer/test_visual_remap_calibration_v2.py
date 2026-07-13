from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


H = tuple(f"{index:064x}" for index in range(1, 128))
REGIONS = tuple(f"r-{letter * 64}" for letter in "abcde")
FAMILIES = (
    "pontiac_transam_wheel",
    "dodge_charger",
    "toyota_supra",
    "nissan_skyline_gtr32",
    "dodge_monaco_police",
)
METRICS = (
    "silhouette_iou", "rgb_mae", "edge_error",
    "surface_bidirectional_p95", "surface_max", "normal_angle_p95",
    "uv_error_p95", "skinning_error_p95",
)
SOURCE_BUNDLE = {
    "commit_sha": "4d843382f199d20c89e1dc01f073a3676518aef6",
    "file_sha256": "f9d007427c7ce661f6ef46a34a25c0524c48e515b0981c4e6ad6ecf2a1fadb57",
    "canonical_evidence_seal": "c7c0bfcf00714f44972ce72c78f9b5935c592f5d51e1d903b191a2824ca2fc16",
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _sealed(value: dict, field: str) -> dict:
    result = copy.deepcopy(value)
    result[field] = _digest({key: item for key, item in result.items() if key != field})
    return result


def _metrics(value: float) -> dict[str, float]:
    return {metric: (0.0 if metric == "skinning_error_p95" else value) for metric in METRICS}


def _inventory(kind: str, marker: int) -> dict:
    value = {
        "denominator_kind": kind,
        "total_bytes": 300,
        "artifacts": [
            {"path": "models/example.mdl", "size_bytes": 100, "sha256": H[marker]},
            {"path": "models/example.vvd", "size_bytes": 200, "sha256": H[marker + 1]},
        ],
        "inventory_sha256": "0" * 64,
    }
    return _sealed(value, "inventory_sha256")


def _selector(marker: int) -> dict:
    ranking = [
        {
            "region_key": REGIONS[index],
            "state_index": index,
            "state_name": f"state-{index}",
            "pose": "bind",
            "normalized_surface_p95": 0.9 - index * 0.1,
            "normalized_surface_max": 0.8 - index * 0.1,
            "risk": 0.9 - index * 0.1,
        }
        for index in range(5)
    ]
    value = {
        "selector_id": "exact-source-union-all-regions-v1",
        "basis": "exact-source-union-all-regions",
        "reference_candidate_id": "strict-roundtrip-control",
        "reference_source_snapshot_sha256": H[marker],
        "exact_source_regions": [
            {
                "region_key": region,
                "state_index": index,
                "state_name": f"state-{index}",
                "pose": "bind",
                "source_region_sha256": H[marker + index + 1],
            }
            for index, region in enumerate(REGIONS)
        ],
        "eligible_ranking": ranking,
        "selected_region_keys": list(REGIONS),
        "selector_input_sha256": "0" * 64,
    }
    return _sealed(value, "selector_input_sha256")


def _run(scope: str, marker: int, *, repeat: bool = False) -> dict:
    offset = 8 if repeat else 0
    return {
        "scope": scope,
        "reference_manifest_sha256": H[marker + offset],
        "candidate_manifest_sha256": H[marker + offset + 1],
        "source_snapshot_sha256": H[marker + 2],
        "candidate_snapshot_sha256": H[marker + 3],
        "pixel_bundle_sha256": H[marker + 4],
        "metrics": _metrics(0.1),
    }


def _focused_region(region: str, marker: int, verdict: str) -> dict:
    first = _run("focused_region", marker)
    repeat = _run("focused_region", marker, repeat=True)
    repeat["source_snapshot_sha256"] = first["source_snapshot_sha256"]
    repeat["candidate_snapshot_sha256"] = first["candidate_snapshot_sha256"]
    repeat["pixel_bundle_sha256"] = first["pixel_bundle_sha256"]
    if verdict == "failed":
        first["metrics"]["edge_error"] = 2.0
        repeat["metrics"]["edge_error"] = 2.0
    return {"region_key": region, "verdict": verdict, "first": first, "repeat": repeat}


def _record(role: str, marker: int) -> dict:
    first = _run("whole_configuration", marker)
    repeat = _run("whole_configuration", marker, repeat=True)
    repeat["source_snapshot_sha256"] = first["source_snapshot_sha256"]
    repeat["candidate_snapshot_sha256"] = first["candidate_snapshot_sha256"]
    repeat["pixel_bundle_sha256"] = first["pixel_bundle_sha256"]
    verdicts = ("passed", "passed", "passed") if role == "positive_final" else (
        "failed", "passed", "passed",
    )
    return {
        "role": role,
        "candidate_id": f"{role}-candidate",
        "toolchain_sha256": H[120],
        "source_manifest_sha256": H[marker + 5],
        "candidate_manifest_sha256": H[marker + 6],
        "candidate_compiled": _inventory("candidate-compiled-family-v1", marker + 20),
        "changed_region_keys": list(REGIONS[:3]),
        "exact_fallback_region_keys": [] if role == "nearest_negative" else [REGIONS[4]],
        "rejected_region_options": [],
        "whole": {"first": first, "repeat": repeat},
        "focused": {
            "changed_region_keys": list(REGIONS[:3]),
            "regions": [
                _focused_region(region, marker + 30 + index * 12, verdicts[index])
                for index, region in enumerate(REGIONS[:3])
            ],
        },
        "unchanged_equality_proofs": [
            {
                "region_key": region,
                "proof_kind": "decoded-region-byte-equality-v1",
                "source_region_sha256": H[marker + 7 + index],
                "candidate_region_sha256": H[marker + 7 + index],
            }
            for index, region in enumerate(REGIONS[3:])
        ],
    }


def _family(family_id: str, marker: int) -> dict:
    family_class = "round-rigid-v1" if family_id == FAMILIES[0] else "general-body-detail-v1"
    pose_coverage = (
        {"mode": "rigid-bind-only", "animations_available": False, "poses": ["bind"]}
        if family_class == "round-rigid-v1"
        else {"mode": "skinned-animation-covered", "animations_available": True, "poses": ["bind", "drive"]}
    )
    return {
        "family_id": family_id,
        "family_class": family_class,
        "status": "complete",
        "toolchain_sha256": H[120],
        "selector": _selector(marker),
        "pose_coverage": pose_coverage,
        "denominators": {
            "shipped_original": _inventory("shipped-original-compiled-family-v1", marker + 2),
            "recompiled_roundtrip": _inventory("strict-roundtrip-control-compiled-family-v1", marker + 4),
        },
        "records": [_record("positive_final", marker + 10), _record("nearest_negative", marker + 11)],
    }


def _payload() -> dict:
    components = {
        "renderer_sha256": H[110],
        "visual_comparator_sha256": H[111],
        "visual_state_runner_sha256": H[112],
        "optimizer_sha256": H[113],
        "blender_sha256": H[114],
        "vtfcmd_sha256": H[115],
        "studiomdl_sha256": H[116],
    }
    toolchain = {
        "toolchain_id": "maximum-current-final-toolchain-v1",
        "components": components,
        "toolchain_sha256": H[120],
    }
    toolchain["toolchain_sha256"] = _digest({
        "toolchain_id": toolchain["toolchain_id"], "components": components,
    })
    families = [_family(family, 1 + index * 10) for index, family in enumerate(FAMILIES)]
    for family in families:
        family["toolchain_sha256"] = toolchain["toolchain_sha256"]
        for record in family["records"]:
            record["toolchain_sha256"] = toolchain["toolchain_sha256"]
    value = {
        "schema_version": 2,
        "strategy": "lvs-visual-remap-calibration-v2",
        "status": "calibration-complete-non-authorizing",
        "authorizing": False,
        "holdout_consulted": False,
        "source_bundle": copy.deepcopy(SOURCE_BUNDLE),
        "toolchain": toolchain,
        "gate_hypotheses": {
            "status": "provisional-non-authorizing",
            "authorizing": False,
            "whole_configuration": _metrics(1.0),
            "focused_region": _metrics(1.0),
        },
        "families": families,
        "separation": {
            "complete": True,
            "positive_record_count": 5,
            "negative_record_count": 5,
            "unseparated_families": [],
        },
        "decision": {
            "status": "ready-for-independent-profile-review",
            "authorizing": False,
            "profile_approval_created": False,
        },
        "evidence_sha256": "0" * 64,
    }
    return _sealed(value, "evidence_sha256")


class VisualRemapCalibrationV2Tests(unittest.TestCase):
    def test_complete_current_toolchain_evidence_loads_non_authorizing(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            load_visual_remap_calibration,
        )

        payload = _payload()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            parsed = load_visual_remap_calibration(
                path, expected_evidence_sha256=payload["evidence_sha256"],
            )

        self.assertFalse(parsed["authorizing"])
        self.assertFalse(parsed["holdout_consulted"])
        self.assertEqual(len(parsed["families"]), 5)
        self.assertEqual(parsed["families"][0]["selector"]["selected_region_keys"], list(REGIONS))

    def test_selector_must_cover_exact_source_union_with_risk_used_only_for_order(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        mutations = (
            lambda item: item["families"][0]["selector"].__setitem__("basis", "optimized-surrogate"),
            lambda item: item["families"][0]["selector"]["exact_source_regions"].pop(),
            lambda item: item["families"][0]["selector"]["eligible_ranking"].reverse(),
            lambda item: item["families"][0]["selector"].__setitem__(
                "selected_region_keys", list(REGIONS[:3]),
            ),
        )
        original = _payload()
        for mutate in mutations:
            changed = copy.deepcopy(original)
            mutate(changed)
            selector = changed["families"][0]["selector"]
            selector["selector_input_sha256"] = _digest({
                key: value for key, value in selector.items() if key != "selector_input_sha256"
            })
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

    def test_every_record_is_bound_to_one_toolchain_and_resealed_swap_fails(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        original = _payload()
        changed = copy.deepcopy(original)
        changed["families"][2]["records"][0]["toolchain_sha256"] = H[109]
        changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
        with self.assertRaisesRegex(ValueError, "toolchain"):
            parse_visual_remap_calibration(
                changed, expected_evidence_sha256=changed["evidence_sha256"],
            )

        resealed = copy.deepcopy(original)
        resealed["families"][2]["records"][0]["candidate_manifest_sha256"] = H[109]
        resealed["evidence_sha256"] = canonical_visual_remap_calibration_hash(resealed)
        with self.assertRaisesRegex(ValueError, "expected evidence"):
            parse_visual_remap_calibration(
                resealed, expected_evidence_sha256=original["evidence_sha256"],
            )

    def test_scope_mix_and_incomplete_changed_or_unchanged_coverage_fail_closed(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        mutations = (
            lambda item: item["families"][1]["records"][0]["whole"]["first"].__setitem__(
                "scope", "focused_region",
            ),
            lambda item: item["families"][1]["records"][0].pop("focused"),
            lambda item: item["families"][1]["records"][0]["focused"]["regions"].pop(),
            lambda item: item["families"][1]["records"][0]["focused"]["regions"][0].__setitem__(
                "region_key", REGIONS[4],
            ),
            lambda item: item["families"][1]["records"][0]["unchanged_equality_proofs"].pop(),
            lambda item: item["families"][1]["records"][0]["unchanged_equality_proofs"][0].__setitem__(
                "candidate_region_sha256", H[109],
            ),
        )
        for mutate in mutations:
            changed = _payload()
            mutate(changed)
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

    def test_repeats_bind_source_candidate_and_decoded_pixels(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        for field in ("source_snapshot_sha256", "candidate_snapshot_sha256", "pixel_bundle_sha256"):
            changed = _payload()
            changed["families"][3]["records"][0]["focused"]["regions"][0]["repeat"][field] = H[109]
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "repeat"):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

    def test_denominator_kinds_inventory_hashes_and_source_bundle_are_exact(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        mutations = (
            lambda item: item["families"][0]["denominators"]["shipped_original"].__setitem__(
                "denominator_kind", "strict-roundtrip-control-compiled-family-v1",
            ),
            lambda item: item["families"][0]["denominators"]["recompiled_roundtrip"]["artifacts"][0].__setitem__(
                "sha256", H[109],
            ),
            lambda item: item["source_bundle"].__setitem__("file_sha256", H[109]),
        )
        for mutate in mutations:
            changed = _payload()
            mutate(changed)
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

    def test_skinned_family_without_animation_coverage_is_fail_closed(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        changed = _payload()
        changed["families"][1]["pose_coverage"] = {
            "mode": "unsupported-skinned-without-animation",
            "animations_available": False,
            "poses": ["bind"],
        }
        changed["families"][1]["status"] = "unsupported-no-animation-coverage"
        changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
        with self.assertRaisesRegex(ValueError, "unsupported pose coverage"):
            parse_visual_remap_calibration(
                changed, expected_evidence_sha256=changed["evidence_sha256"],
            )

    def test_positive_must_pass_all_changed_regions_and_negative_must_separate(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        positive = _payload()
        positive["families"][0]["records"][0]["focused"]["regions"][0]["verdict"] = "failed"
        positive["evidence_sha256"] = canonical_visual_remap_calibration_hash(positive)
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_visual_remap_calibration(
                positive, expected_evidence_sha256=positive["evidence_sha256"],
            )

        negative = _payload()
        failed = negative["families"][0]["records"][1]["focused"]["regions"][0]
        failed["verdict"] = "passed"
        failed["first"]["metrics"] = _metrics(0.1)
        failed["repeat"]["metrics"] = _metrics(0.1)
        negative["evidence_sha256"] = canonical_visual_remap_calibration_hash(negative)
        with self.assertRaisesRegex(ValueError, "negative"):
            parse_visual_remap_calibration(
                negative, expected_evidence_sha256=negative["evidence_sha256"],
            )

    def test_unknown_or_unrenderable_option_is_rejected_and_exact_fallback_is_explicit(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        valid = _payload()
        valid["families"][0]["records"][0]["rejected_region_options"] = [{
            "region_key": REGIONS[3],
            "option_id": "candidate-unknown",
            "reason": "unrenderable",
            "disposition": "rejected",
        }]
        valid["evidence_sha256"] = canonical_visual_remap_calibration_hash(valid)
        parse_visual_remap_calibration(valid, expected_evidence_sha256=valid["evidence_sha256"])

        for mutate in (
            lambda item: item["families"][0]["records"][0]["rejected_region_options"].append({
                "region_key": REGIONS[3], "option_id": "bad", "reason": "unrenderable",
                "disposition": "selected",
            }),
            lambda item: item["families"][0]["records"][0]["exact_fallback_region_keys"].append(
                REGIONS[0],
            ),
        ):
            changed = _payload()
            mutate(changed)
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

    def test_authority_holdout_and_old_schema_fail_closed_without_touching_v3_loader(self) -> None:
        from maximum_optimizer.visual_remap_calibration import (
            canonical_visual_remap_calibration_hash,
            parse_visual_remap_calibration,
        )

        for field in ("authorizing", "holdout_consulted"):
            changed = _payload()
            changed[field] = True
            changed["evidence_sha256"] = canonical_visual_remap_calibration_hash(changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_visual_remap_calibration(
                    changed, expected_evidence_sha256=changed["evidence_sha256"],
                )

        old = _payload()
        old["schema_version"] = 1
        old["evidence_sha256"] = canonical_visual_remap_calibration_hash(old)
        with self.assertRaisesRegex(ValueError, "schema"):
            parse_visual_remap_calibration(
                old, expected_evidence_sha256=old["evidence_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
