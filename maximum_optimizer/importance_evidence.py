from __future__ import annotations

import re


_HASH = re.compile(r"^[0-9a-f]{64}$")


def _exact(value: object, keys: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} fields are invalid")
    return value


def _record(value: object, expected_strategy: str, label: str) -> dict:
    record = _exact(value, {
        "strategy", "ratio", "compiled", "candidate_sha256", "metrics_sha256",
        "optimize_log_sha256", "compile_log_sha256", "render_manifests", "raw_clay",
    }, label)
    if record["strategy"] != expected_strategy:
        raise ValueError(f"{label} strategy is invalid")
    if type(record["ratio"]) not in (int, float) or not 0 < record["ratio"] <= 1:
        raise ValueError(f"{label} ratio is invalid")
    for key in ("candidate_sha256", "metrics_sha256", "optimize_log_sha256", "compile_log_sha256"):
        if type(record[key]) is not str or _HASH.fullmatch(record[key]) is None:
            raise ValueError(f"{label} hash is invalid")
    manifests = _exact(record["render_manifests"], {"reference_sha256", "candidate_sha256"}, f"{label} manifests")
    if any(type(value) is not str or _HASH.fullmatch(value) is None for value in manifests.values()):
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
    if any(type(value) not in (int, float) or value < 0 for value in raw.values()):
        raise ValueError(f"{label} raw metric is invalid")
    return record


def parse_importance_evidence(payload: object) -> dict:
    root = _exact(payload, {
        "schema_version", "strategy", "family_id", "toolchain", "implementation",
        "baseline", "candidate", "quality", "decision",
    }, "importance evidence")
    if root["schema_version"] != 1 or root["strategy"] != "blender-importance-map-v1" \
            or root["family_id"] != "pontiac_transam_wheel":
        raise ValueError("importance evidence identity is invalid")
    for section in ("toolchain", "implementation"):
        values = root[section]
        if type(values) is not dict or not values or any(
            type(value) is not str or _HASH.fullmatch(value) is None for value in values.values()
        ):
            raise ValueError(f"{section} hashes are invalid")
    baseline = _record(root["baseline"], "blender-adaptive-v1", "baseline")
    candidate = _record(root["candidate"], "blender-importance-map-v1", "candidate")
    quality = _exact(root["quality"], {"status", "scope", "texture_status"}, "quality")
    if quality != {"status": "uncalibrated-raw-ranking-only", "scope": "rim1-bind-8-views", "texture_status": "clay-authoritative"}:
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
