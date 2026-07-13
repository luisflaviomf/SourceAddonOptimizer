"""Fail-closed loader for non-authorizing visual-remap calibration evidence.

This contract is intentionally separate from the frozen profile/holdout evidence
loaders.  Passing it means that a calibration bundle is internally consistent;
it never grants permission to activate a Maximum profile.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


_SHA256_LENGTH = 64
_EXPECTED_FAMILIES = {
    "pontiac_transam_wheel",
    "dodge_charger",
    "toyota_supra",
    "nissan_skyline_gtr32",
    "dodge_monaco_police",
}
_SOURCE_BUNDLE = {
    "commit_sha": "4d843382f199d20c89e1dc01f073a3676518aef6",
    "file_sha256": "f9d007427c7ce661f6ef46a34a25c0524c48e515b0981c4e6ad6ecf2a1fadb57",
    "canonical_evidence_seal": "c7c0bfcf00714f44972ce72c78f9b5935c592f5d51e1d903b191a2824ca2fc16",
}
_METRICS = {
    "silhouette_iou",
    "rgb_mae",
    "edge_error",
    "surface_bidirectional_p95",
    "surface_max",
    "normal_angle_p95",
    "uv_error_p95",
    "skinning_error_p95",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def canonical_visual_remap_calibration_hash(payload: Mapping[str, Any]) -> str:
    """Hash a bundle while excluding its self-referential evidence seal."""

    return _hash({key: value for key, value in payload.items() if key != "evidence_sha256"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    _require(isinstance(value, list), f"{name} must be a list")
    return value


def _sha(value: object, name: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value),
        f"{name} must be a lowercase SHA-256",
    )
    return value


def _metrics(value: object, name: str) -> Mapping[str, Any]:
    metrics = _mapping(value, name)
    _require(set(metrics) == _METRICS, f"{name} has incomplete metric coverage")
    for metric, raw in metrics.items():
        _require(
            isinstance(raw, (int, float)) and not isinstance(raw, bool)
            and math.isfinite(float(raw)) and float(raw) >= 0.0,
            f"{name}.{metric} must be a finite non-negative number",
        )
    return metrics


def _validate_inventory(value: object, expected_kind: str, name: str) -> None:
    inventory = _mapping(value, name)
    _require(inventory.get("denominator_kind") == expected_kind, f"{name} denominator kind mismatch")
    artifacts = _list(inventory.get("artifacts"), f"{name}.artifacts")
    _require(bool(artifacts), f"{name}.artifacts must not be empty")
    paths: list[str] = []
    total = 0
    for index, raw_artifact in enumerate(artifacts):
        artifact = _mapping(raw_artifact, f"{name}.artifacts[{index}]")
        path = artifact.get("path")
        size = artifact.get("size_bytes")
        _require(isinstance(path, str) and path and "\\" not in path, f"{name} has invalid artifact path")
        _require(isinstance(size, int) and not isinstance(size, bool) and size >= 0, f"{name} has invalid size")
        _sha(artifact.get("sha256"), f"{name}.artifacts[{index}].sha256")
        paths.append(path)
        total += size
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), f"{name}.artifacts must be sorted and unique")
    _require(inventory.get("total_bytes") == total, f"{name}.total_bytes mismatch")
    expected = _hash({key: item for key, item in inventory.items() if key != "inventory_sha256"})
    _require(_sha(inventory.get("inventory_sha256"), f"{name}.inventory_sha256") == expected, f"{name} inventory hash mismatch")


def _validate_selector(value: object, name: str) -> list[str]:
    selector = _mapping(value, name)
    _require(selector.get("selector_id") == "exact-source-union-all-regions-v1", f"{name} selector id mismatch")
    _require(selector.get("basis") == "exact-source-union-all-regions", f"{name} must use the exact source union")
    _require(selector.get("reference_candidate_id") == "strict-roundtrip-control", f"{name} reference candidate mismatch")
    _sha(selector.get("reference_source_snapshot_sha256"), f"{name}.reference_source_snapshot_sha256")
    source_regions = _list(selector.get("exact_source_regions"), f"{name}.exact_source_regions")
    _require(bool(source_regions), f"{name} exact source union must not be empty")
    source_keys: list[str] = []
    for index, raw_region in enumerate(source_regions):
        region = _mapping(raw_region, f"{name}.exact_source_regions[{index}]")
        key = region.get("region_key")
        _require(isinstance(key, str) and key, f"{name} exact source region key is invalid")
        _require(isinstance(region.get("state_index"), int), f"{name} exact source state index is invalid")
        _require(isinstance(region.get("state_name"), str) and region["state_name"], f"{name} exact source state name is invalid")
        _require(isinstance(region.get("pose"), str) and region["pose"], f"{name} exact source pose is invalid")
        _sha(region.get("source_region_sha256"), f"{name}.exact_source_regions[{index}].source_region_sha256")
        source_keys.append(key)
    _require(len(source_keys) == len(set(source_keys)), f"{name} exact source union contains duplicates")
    ranking = _list(selector.get("eligible_ranking"), f"{name}.eligible_ranking")
    _require(len(ranking) == len(source_keys), f"{name} risk order does not cover the exact source union")
    decorated: list[tuple[float, float, float, str]] = []
    keys: list[str] = []
    for index, raw_region in enumerate(ranking):
        region = _mapping(raw_region, f"{name}.eligible_ranking[{index}]")
        key = region.get("region_key")
        _require(isinstance(key, str) and key, f"{name} region key is invalid")
        keys.append(key)
        numeric: list[float] = []
        for field in ("risk", "normalized_surface_p95", "normalized_surface_max"):
            raw = region.get(field)
            _require(isinstance(raw, (int, float)) and math.isfinite(float(raw)), f"{name}.{field} is invalid")
            numeric.append(float(raw))
        decorated.append((-numeric[0], -numeric[1], -numeric[2], key))
    _require(len(keys) == len(set(keys)), f"{name} region keys must be unique")
    _require(set(keys) == set(source_keys), f"{name} risk order differs from exact source union")
    _require(decorated == sorted(decorated), f"{name} ranking is not in canonical risk order")
    selected = _list(selector.get("selected_region_keys"), f"{name}.selected_region_keys")
    _require(selected == keys, f"{name} must select every exact-source region; risk may only order them")
    expected = _hash({key: item for key, item in selector.items() if key != "selector_input_sha256"})
    _require(_sha(selector.get("selector_input_sha256"), f"{name}.selector_input_sha256") == expected, f"{name} selector input hash mismatch")
    return selected


def _validate_run(value: object, scope: str, name: str) -> Mapping[str, Any]:
    run = _mapping(value, name)
    _require(run.get("scope") == scope, f"{name} scope mismatch")
    for field in (
        "reference_manifest_sha256", "candidate_manifest_sha256",
        "source_snapshot_sha256", "candidate_snapshot_sha256", "pixel_bundle_sha256",
    ):
        _sha(run.get(field), f"{name}.{field}")
    _metrics(run.get("metrics"), f"{name}.metrics")
    return run


def _validate_repeat(pair: object, scope: str, name: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    runs = _mapping(pair, name)
    first = _validate_run(runs.get("first"), scope, f"{name}.first")
    repeat = _validate_run(runs.get("repeat"), scope, f"{name}.repeat")
    for field in ("source_snapshot_sha256", "candidate_snapshot_sha256", "pixel_bundle_sha256"):
        _require(first[field] == repeat[field], f"{name} repeat {field} mismatch")
    return first, repeat


def _exceeds_cap(metrics: Mapping[str, Any], caps: Mapping[str, Any]) -> bool:
    # silhouette_iou is represented as an error-like delta by this evidence
    # contract, so every metric is fail-closed when it exceeds its cap.
    return any(float(metrics[key]) > float(caps[key]) for key in _METRICS)


def _validate_pose_coverage(family: Mapping[str, Any], name: str) -> None:
    coverage = _mapping(family.get("pose_coverage"), f"{name}.pose_coverage")
    poses = _list(coverage.get("poses"), f"{name}.pose_coverage.poses")
    if family.get("family_class") == "round-rigid-v1":
        _require(
            coverage.get("mode") == "rigid-bind-only"
            and coverage.get("animations_available") is False
            and poses == ["bind"],
            f"{name} unsupported pose coverage",
        )
        return
    _require(
        coverage.get("mode") == "skinned-animation-covered"
        and coverage.get("animations_available") is True
        and "bind" in poses and len(set(poses) - {"bind"}) >= 1,
        f"{name} unsupported pose coverage",
    )


def _validate_record(
    value: object,
    expected_role: str,
    selected: list[str],
    toolchain_sha256: str,
    focused_caps: Mapping[str, Any],
    name: str,
) -> None:
    record = _mapping(value, name)
    _require(record.get("role") == expected_role, f"{name} role mismatch")
    _require(isinstance(record.get("candidate_id"), str) and record["candidate_id"], f"{name} candidate id missing")
    _require(record.get("toolchain_sha256") == toolchain_sha256, f"{name} toolchain mismatch")
    _sha(record.get("source_manifest_sha256"), f"{name}.source_manifest_sha256")
    _sha(record.get("candidate_manifest_sha256"), f"{name}.candidate_manifest_sha256")
    _validate_inventory(record.get("candidate_compiled"), "candidate-compiled-family-v1", f"{name}.candidate_compiled")
    changed = _list(record.get("changed_region_keys"), f"{name}.changed_region_keys")
    _require(len(changed) == len(set(changed)) and set(changed) <= set(selected), f"{name} changed regions are invalid")
    fallbacks = _list(record.get("exact_fallback_region_keys"), f"{name}.exact_fallback_region_keys")
    _require(len(fallbacks) == len(set(fallbacks)) and set(fallbacks) <= set(selected), f"{name} exact fallbacks are invalid")
    rejected = _list(record.get("rejected_region_options"), f"{name}.rejected_region_options")
    for index, raw_rejection in enumerate(rejected):
        rejection = _mapping(raw_rejection, f"{name}.rejected_region_options[{index}]")
        _require(rejection.get("region_key") in selected, f"{name} rejected option has unknown region")
        _require(isinstance(rejection.get("option_id"), str) and rejection["option_id"], f"{name} rejected option id missing")
        _require(rejection.get("reason") in {"unknown", "unrenderable"}, f"{name} rejected option reason is invalid")
        _require(rejection.get("disposition") == "rejected", f"{name} unknown/unrenderable option must be rejected")
    _validate_repeat(record.get("whole"), "whole_configuration", f"{name}.whole")

    focused = _mapping(record.get("focused"), f"{name}.focused")
    _require(focused.get("changed_region_keys") == changed, f"{name} focused changed-region binding mismatch")
    regions = _list(focused.get("regions"), f"{name}.focused.regions")
    _require(len(regions) == len(changed), f"{name} focused changed-region coverage is incomplete")
    seen: list[str] = []
    failed_with_exceedance = False
    for index, raw_region in enumerate(regions):
        region = _mapping(raw_region, f"{name}.focused.regions[{index}]")
        key = region.get("region_key")
        seen.append(key)
        first, repeat = _validate_repeat(region, "focused_region", f"{name}.focused.regions[{index}]")
        verdict = region.get("verdict")
        _require(verdict in {"passed", "failed"}, f"{name} focused verdict is invalid")
        if expected_role == "positive_final":
            _require(verdict == "passed", f"{name} positive record has a failed focused region")
        elif verdict == "failed":
            _require(
                _exceeds_cap(first["metrics"], focused_caps)
                and _exceeds_cap(repeat["metrics"], focused_caps),
                f"{name} negative failure does not exceed focused limits",
            )
            failed_with_exceedance = True
    _require(seen == changed, f"{name} focused regions do not match changed-region order")
    unchanged = _list(record.get("unchanged_equality_proofs"), f"{name}.unchanged_equality_proofs")
    unchanged_keys: list[str] = []
    for index, raw_proof in enumerate(unchanged):
        proof = _mapping(raw_proof, f"{name}.unchanged_equality_proofs[{index}]")
        key = proof.get("region_key")
        _require(key in selected, f"{name} equality proof has unknown region")
        _require(proof.get("proof_kind") == "decoded-region-byte-equality-v1", f"{name} equality proof kind mismatch")
        source_sha = _sha(proof.get("source_region_sha256"), f"{name}.unchanged_equality_proofs[{index}].source_region_sha256")
        candidate_sha = _sha(proof.get("candidate_region_sha256"), f"{name}.unchanged_equality_proofs[{index}].candidate_region_sha256")
        _require(source_sha == candidate_sha, f"{name} unchanged equality proof mismatch")
        unchanged_keys.append(key)
    _require(len(unchanged_keys) == len(set(unchanged_keys)), f"{name} duplicate unchanged equality proof")
    _require(set(changed).isdisjoint(unchanged_keys), f"{name} region cannot be both changed and unchanged")
    _require(set(changed) | set(unchanged_keys) == set(selected), f"{name} changed/unchanged coverage is incomplete")
    _require(set(fallbacks) <= set(unchanged_keys) and set(fallbacks).isdisjoint(changed), f"{name} exact fallback lacks unchanged equality proof")
    if expected_role == "nearest_negative":
        _require(failed_with_exceedance, f"{name} negative record does not demonstrate separation")


def parse_visual_remap_calibration(
    payload: object,
    *,
    expected_evidence_sha256: str,
) -> dict[str, Any]:
    """Validate and return a v2 calibration bundle, or fail closed."""

    root = _mapping(payload, "calibration evidence")
    _sha(expected_evidence_sha256, "expected evidence SHA-256")
    _require(root.get("schema_version") == 2, "unsupported calibration evidence schema")
    _require(root.get("strategy") == "lvs-visual-remap-calibration-v2", "unexpected calibration strategy")
    _require(root.get("status") == "calibration-complete-non-authorizing", "calibration is incomplete")
    _require(root.get("authorizing") is False, "calibration evidence must remain non-authorizing")
    _require(root.get("holdout_consulted") is False, "holdout must not be consulted during calibration")
    _require(root.get("source_bundle") == _SOURCE_BUNDLE, "source bundle binding mismatch")
    actual = canonical_visual_remap_calibration_hash(root)
    _require(root.get("evidence_sha256") == actual, "calibration evidence self hash mismatch")
    _require(actual == expected_evidence_sha256, "expected evidence SHA-256 mismatch")

    toolchain = _mapping(root.get("toolchain"), "toolchain")
    components = _mapping(toolchain.get("components"), "toolchain.components")
    required_components = {
        "renderer_sha256", "visual_comparator_sha256", "visual_state_runner_sha256",
        "optimizer_sha256", "blender_sha256", "vtfcmd_sha256", "studiomdl_sha256",
    }
    _require(set(components) == required_components, "toolchain component coverage mismatch")
    for key, value in components.items():
        _sha(value, f"toolchain.components.{key}")
    toolchain_id = toolchain.get("toolchain_id")
    _require(toolchain_id == "maximum-current-final-toolchain-v1", "toolchain id mismatch")
    toolchain_sha256 = _sha(toolchain.get("toolchain_sha256"), "toolchain.toolchain_sha256")
    _require(toolchain_sha256 == _hash({"toolchain_id": toolchain_id, "components": components}), "toolchain hash mismatch")

    gates = _mapping(root.get("gate_hypotheses"), "gate_hypotheses")
    _require(gates.get("status") == "provisional-non-authorizing" and gates.get("authorizing") is False, "gate hypotheses must remain provisional")
    _metrics(gates.get("whole_configuration"), "gate_hypotheses.whole_configuration")
    focused_caps = _metrics(gates.get("focused_region"), "gate_hypotheses.focused_region")

    families = _list(root.get("families"), "families")
    _require(len(families) == len(_EXPECTED_FAMILIES), "family coverage is incomplete")
    family_ids: list[str] = []
    for index, raw_family in enumerate(families):
        family = _mapping(raw_family, f"families[{index}]")
        family_id = family.get("family_id")
        _require(isinstance(family_id, str), f"families[{index}].family_id is invalid")
        family_ids.append(family_id)
        _require(family.get("toolchain_sha256") == toolchain_sha256, f"families[{index}] toolchain mismatch")
        selected = _validate_selector(family.get("selector"), f"families[{index}].selector")
        _validate_pose_coverage(family, f"families[{index}]")
        _require(family.get("status") == "complete", f"families[{index}] is incomplete")
        denominators = _mapping(family.get("denominators"), f"families[{index}].denominators")
        _require(set(denominators) == {"shipped_original", "recompiled_roundtrip"}, f"families[{index}] denominator coverage mismatch")
        _validate_inventory(denominators["shipped_original"], "shipped-original-compiled-family-v1", f"families[{index}].denominators.shipped_original")
        _validate_inventory(denominators["recompiled_roundtrip"], "strict-roundtrip-control-compiled-family-v1", f"families[{index}].denominators.recompiled_roundtrip")
        records = _list(family.get("records"), f"families[{index}].records")
        _require(len(records) == 2, f"families[{index}] must have one positive and one negative")
        _validate_record(records[0], "positive_final", selected, toolchain_sha256, focused_caps, f"families[{index}].records[0]")
        _validate_record(records[1], "nearest_negative", selected, toolchain_sha256, focused_caps, f"families[{index}].records[1]")
    _require(set(family_ids) == _EXPECTED_FAMILIES and len(family_ids) == len(set(family_ids)), "unexpected or duplicate calibration families")

    separation = _mapping(root.get("separation"), "separation")
    _require(
        separation.get("complete") is True
        and separation.get("positive_record_count") == len(_EXPECTED_FAMILIES)
        and separation.get("negative_record_count") == len(_EXPECTED_FAMILIES)
        and separation.get("unseparated_families") == [],
        "calibration separation is incomplete",
    )
    decision = _mapping(root.get("decision"), "decision")
    _require(
        decision.get("status") == "ready-for-independent-profile-review"
        and decision.get("authorizing") is False
        and decision.get("profile_approval_created") is False,
        "calibration decision must remain non-authorizing",
    )
    return dict(root)


def load_visual_remap_calibration(
    path: str | Path,
    *,
    expected_evidence_sha256: str,
) -> dict[str, Any]:
    """Load a calibration JSON file and validate all evidence bindings."""

    evidence_path = Path(path)
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load calibration evidence: {exc}") from exc
    return parse_visual_remap_calibration(
        raw, expected_evidence_sha256=expected_evidence_sha256,
    )
