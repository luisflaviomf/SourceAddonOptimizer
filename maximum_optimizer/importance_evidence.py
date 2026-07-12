from __future__ import annotations

import copy
import hashlib
import json
import re


_HASH = re.compile(r"^[0-9a-f]{64}$")


def _exact(value: object, keys: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} fields are invalid")
    return value


def canonical_importance_evidence_hash(payload: object) -> str:
    if type(payload) is not dict:
        raise ValueError("importance evidence must be an object")
    canonical = copy.deepcopy(payload)
    canonical.pop("evidence_sha256", None)
    encoded = (json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record(value: object, expected_strategy: str, label: str) -> dict:
    record = _exact(value, {
        "strategy", "ratio", "compiled", "candidate_sha256", "metrics_sha256",
        "optimize_log_sha256", "compile_log_sha256", "render", "raw_clay",
    }, label)
    if record["strategy"] != expected_strategy or record["ratio"] != 0.35:
        raise ValueError(f"{label} is not the controlled r0.35 ablation")
    for key in ("candidate_sha256", "metrics_sha256", "optimize_log_sha256", "compile_log_sha256"):
        if type(record[key]) is not str or _HASH.fullmatch(record[key]) is None:
            raise ValueError(f"{label} hash is invalid")
    render = _exact(record["render"], {
        "reference_manifest_sha256", "reference_image_set_sha256",
        "candidate_manifest_sha256", "candidate_image_set_sha256",
    }, f"{label} render")
    if any(type(item) is not str or _HASH.fullmatch(item) is None for item in render.values()):
        raise ValueError(f"{label} render hash is invalid")
    compiled = _exact(record["compiled"], {"total_bytes", "artifacts"}, f"{label} compiled")
    artifacts = compiled["artifacts"]
    if type(artifacts) is not list or len(artifacts) != 5:
        raise ValueError(f"{label} artifacts are invalid")
    total = 0
    names = set()
    for artifact in artifacts:
        item = _exact(artifact, {"path", "size_bytes", "sha256"}, f"{label} artifact")
        if (type(item["path"]) is not str or item["path"] in names
                or type(item["size_bytes"]) is not int or item["size_bytes"] <= 0
                or type(item["sha256"]) is not str or _HASH.fullmatch(item["sha256"]) is None):
            raise ValueError(f"{label} artifact is invalid")
        names.add(item["path"])
        total += item["size_bytes"]
    if compiled["total_bytes"] != total:
        raise ValueError(f"{label} compiled total drift")
    raw = _exact(record["raw_clay"], {
        "average_silhouette_error", "max_silhouette_error",
        "average_edge_error", "max_edge_error", "average_rgb_mae", "max_rgb_mae",
    }, f"{label} raw clay")
    if any(type(item) not in (int, float) or item < 0 for item in raw.values()):
        raise ValueError(f"{label} raw metric is invalid")
    return record


def parse_importance_evidence(payload: object) -> dict:
    root = _exact(payload, {
        "schema_version", "strategy", "family_id", "toolchain", "implementation",
        "baseline", "candidate", "quality", "decision", "evidence_sha256",
    }, "importance evidence")
    if (root["schema_version"] != 2 or root["strategy"] != "blender-importance-map-v1"
            or root["family_id"] != "pontiac_transam_wheel"):
        raise ValueError("importance evidence identity is invalid")
    if (type(root["evidence_sha256"]) is not str
            or root["evidence_sha256"] != canonical_importance_evidence_hash(root)):
        raise ValueError("importance evidence seal is invalid")
    required_implementation = {
        "batch_optimize_maximum.py", "maximum_optimizer/importance_map.py",
        "maximum_optimizer/search.py", "render_previews.py",
        "maximum_optimizer/visual_validation.py",
        "benchmarks/lvs_models/build_blender_importance_map_v1.py",
    }
    for section in ("toolchain", "implementation"):
        values = root[section]
        if (type(values) is not dict or not values
                or any(type(item) is not str or _HASH.fullmatch(item) is None for item in values.values())):
            raise ValueError(f"{section} hashes are invalid")
    if set(root["implementation"]) != required_implementation:
        raise ValueError("implementation coverage is invalid")
    baseline = _record(root["baseline"], "blender-adaptive-v1", "baseline")
    candidate = _record(root["candidate"], "blender-importance-map-v1", "candidate")
    if (baseline["render"]["reference_manifest_sha256"]
            != candidate["render"]["reference_manifest_sha256"]
            or baseline["render"]["reference_image_set_sha256"]
            != candidate["render"]["reference_image_set_sha256"]):
        raise ValueError("controlled ablation must use one identical reference render")
    quality = _exact(root["quality"], {
        "status", "scope", "texture_status", "container_status", "render_determinism",
    }, "quality")
    determinism = _exact(quality["render_determinism"], {
        "status", "repeat_reference_image_set_sha256", "repeat_candidate_image_set_sha256",
    }, "render determinism")
    if (quality["status"] != "uncalibrated-raw-ranking-only"
            or quality["scope"] != "rim1-bind-8-views"
            or quality["texture_status"] != "clay-authoritative"
            or quality["container_status"] != "png-bytes-vary-but-decoded-pixels-are-identical"
            or determinism["status"] != "decoded-rgba-identical-across-repeat"
            or determinism["repeat_reference_image_set_sha256"]
                != candidate["render"]["reference_image_set_sha256"]
            or determinism["repeat_candidate_image_set_sha256"]
                != candidate["render"]["candidate_image_set_sha256"]):
        raise ValueError("quality disclosure is invalid")
    decision = _exact(root["decision"], {"winner", "status", "reason"}, "decision")
    boundary_regressed = (
        candidate["raw_clay"]["max_edge_error"] > baseline["raw_clay"]["max_edge_error"]
        or candidate["raw_clay"]["max_silhouette_error"] > baseline["raw_clay"]["max_silhouette_error"]
    )
    if (decision["winner"] is not False
            or decision["status"] != "rejected-visible-boundary-regression"
            or not boundary_regressed
            or candidate["compiled"]["total_bytes"] >= baseline["compiled"]["total_bytes"]):
        raise ValueError("importance decision is inconsistent")
    return root
