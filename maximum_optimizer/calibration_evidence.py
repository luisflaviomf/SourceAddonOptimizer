from __future__ import annotations

import copy
import hashlib
import json
import math
import re


CALIBRATION_FAMILIES = (
    "pontiac_transam_wheel",
    "dodge_charger",
    "toyota_supra",
    "nissan_skyline_gtr32",
    "dodge_monaco_police",
)
CALIBRATION_METRICS = (
    "silhouette_iou",
    "rgb_mae",
    "edge_error",
    "surface_bidirectional_p95",
    "surface_max",
    "normal_angle_p95",
    "uv_error_p95",
    "skinning_error_p95",
)
_CANDIDATES = {
    "pontiac_transam_wheel": "r040",
    "dodge_charger": "r030",
    "toyota_supra": "r025",
    "nissan_skyline_gtr32": "r025",
    "dodge_monaco_police": "r040",
}
_ALTERNATIVES = {
    "pontiac_transam_wheel": ("r035",),
    "dodge_charger": (),
    "toyota_supra": (),
    "nissan_skyline_gtr32": (),
    "dodge_monaco_police": (),
}
_ALTERNATIVE_STATUS = "rejected-research-alternative"
_ALTERNATIVE_REASON = (
    "smaller than r040 but visually dominated r040 and showed manual wheel faceting"
)
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _exact(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} is invalid")
    return number


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _derived_distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": _percentile(ordered, 0.5),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def canonical_calibration_evidence_hash(payload: object) -> str:
    if type(payload) is not dict:
        raise ValueError("calibration evidence must be an object")
    canonical = copy.deepcopy(payload)
    canonical.pop("evidence_sha256", None)
    encoded = (json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _configuration(value: object, lane: str, label: str) -> dict:
    item = _exact(value, {
        "name", "scope", "bodygroups", "reference_manifest_sha256",
        "candidate_manifest_sha256", "metrics", "missing_materials",
        "geometry_audit", "top_regions",
    }, label)
    if type(item["name"]) is not str or not item["name"]:
        raise ValueError(f"{label} name is invalid")
    required_scope = (
        "aggregate-appearance-anchor-not-structural-baseline"
        if lane == "aggregate-appearance-anchor-not-structural-baseline"
        else "strict-region-paired"
    )
    if item["scope"] != required_scope:
        raise ValueError(f"{label} scope is invalid")
    if type(item["bodygroups"]) is not dict or any(
        type(name) is not str or not name or type(index) is not int or index < 0
        for name, index in item["bodygroups"].items()
    ):
        raise ValueError(f"{label} bodygroups are invalid")
    for field in ("reference_manifest_sha256", "candidate_manifest_sha256"):
        if type(item[field]) is not str or _HASH.fullmatch(item[field]) is None:
            raise ValueError(f"{label} manifest hash is invalid")
    metrics = _exact(item["metrics"], set(CALIBRATION_METRICS), f"{label} metrics")
    for metric, value in metrics.items():
        _number(value, f"{label} {metric}")
    missing = _exact(item["missing_materials"], {"reference", "candidate"}, f"{label} missing materials")
    if any(type(value) is not int or value != 0 for value in missing.values()):
        raise ValueError(f"{label} has missing materials")
    audits = _exact(item["geometry_audit"], {"reference", "candidate"}, f"{label} geometry audit")
    for side, audit_value in audits.items():
        audit = _exact(audit_value, {
            "input_triangles", "kept_triangles", "filtered_degenerate_triangles",
            "max_filtered_fraction",
        }, f"{label} {side} audit")
        counts = tuple(audit[key] for key in (
            "input_triangles", "kept_triangles", "filtered_degenerate_triangles"
        ))
        if (
            any(type(value) is not int or value < 0 for value in counts)
            or counts[1] <= 0 or counts[1] + counts[2] != counts[0]
            or _number(audit["max_filtered_fraction"], f"{label} filtered fraction") > 0.05
        ):
            raise ValueError(f"{label} geometry audit is invalid")
    regions = item["top_regions"]
    if type(regions) is not list or len(regions) > 3:
        raise ValueError(f"{label} top regions are invalid")
    for position, region_value in enumerate(regions):
        region = _exact(region_value, {
            "source", "surface_bidirectional_p95", "surface_max"
        }, f"{label} region {position}")
        if type(region["source"]) is not str or not region["source"]:
            raise ValueError(f"{label} region source is invalid")
        _number(region["surface_bidirectional_p95"], f"{label} region p95")
        _number(region["surface_max"], f"{label} region max")
    return item


def _lane(value: object, expected_lane: str, expected_candidate: str, label: str) -> dict:
    lane = _exact(value, {
        "lane", "candidate_id", "compiled", "configurations"
    }, label)
    if lane["lane"] != expected_lane or lane["candidate_id"] != expected_candidate:
        raise ValueError(f"{label} identity is invalid")
    compiled = _exact(lane["compiled"], {"total_bytes", "artifacts"}, f"{label} compiled")
    artifacts = compiled["artifacts"]
    if type(artifacts) is not list or not artifacts:
        raise ValueError(f"{label} compiled artifacts are invalid")
    total = 0
    paths = set()
    for position, artifact_value in enumerate(artifacts):
        artifact = _exact(artifact_value, {"path", "size_bytes", "sha256"}, f"{label} artifact {position}")
        if (
            type(artifact["path"]) is not str or not artifact["path"]
            or artifact["path"] in paths
            or type(artifact["size_bytes"]) is not int or artifact["size_bytes"] <= 0
            or type(artifact["sha256"]) is not str or _HASH.fullmatch(artifact["sha256"]) is None
        ):
            raise ValueError(f"{label} compiled artifact is invalid")
        paths.add(artifact["path"])
        total += artifact["size_bytes"]
    if compiled["total_bytes"] != total:
        raise ValueError(f"{label} compiled total drift")
    configurations = lane["configurations"]
    if type(configurations) is not list or not configurations or len(configurations) > 5:
        raise ValueError(f"{label} configurations are invalid")
    parsed = [
        _configuration(item, expected_lane, f"{label} configuration {position}")
        for position, item in enumerate(configurations)
    ]
    if len({item["name"] for item in parsed}) != len(parsed):
        raise ValueError(f"{label} configuration names are duplicated")
    if expected_lane.startswith("aggregate"):
        if len(parsed) != 1 or not parsed[0]["name"].startswith(
            "aggregate-appearance-anchor-not-structural-baseline:engine-default"
        ):
            raise ValueError(f"{label} aggregate scope is invalid")
    elif parsed[0]["name"] != "engine-default":
        raise ValueError(f"{label} must begin with engine-default")
    return lane


def parse_calibration_evidence(payload: object) -> dict:
    root = _exact(payload, {
        "schema_version", "strategy", "status", "toolchain", "implementation",
        "families", "baseline_distribution", "decision", "evidence_sha256",
    }, "calibration evidence")
    if (
        root["schema_version"] != 1
        or root["strategy"] != "lvs-calibration-corpus-v1"
        or root["status"] != "calibration-pending"
    ):
        raise ValueError("calibration evidence identity is invalid")
    if (
        type(root["evidence_sha256"]) is not str
        or root["evidence_sha256"] != canonical_calibration_evidence_hash(root)
    ):
        raise ValueError("calibration evidence seal is invalid")
    for section in ("toolchain", "implementation"):
        values = root[section]
        if type(values) is not dict or not values or any(
            type(value) is not str or _HASH.fullmatch(value) is None
            for value in values.values()
        ):
            raise ValueError(f"{section} hashes are invalid")
    families = root["families"]
    if type(families) is not list or tuple(
        item.get("family_id") if type(item) is dict else None for item in families
    ) != CALIBRATION_FAMILIES:
        raise ValueError("calibration family set/order is invalid")
    for family_id, family_value in zip(CALIBRATION_FAMILIES, families):
        family = _exact(
            family_value,
            {"family_id", "baseline", "candidate", "alternatives"},
            family_id,
        )
        _lane(
            family["baseline"],
            "strict-region-paired",
            "roundtrip-control",
            f"{family_id} baseline",
        )
        _lane(
            family["candidate"], "strict-region-paired", _CANDIDATES[family_id],
            f"{family_id} candidate",
        )
        alternatives = family["alternatives"]
        expected_alternatives = _ALTERNATIVES[family_id]
        if type(alternatives) is not list or len(alternatives) != len(expected_alternatives):
            raise ValueError(f"{family_id} alternatives are invalid")
        for position, (alternative_value, candidate_id) in enumerate(zip(
            alternatives, expected_alternatives
        )):
            alternative = _exact(
                alternative_value, {"lane", "status", "reason"},
                f"{family_id} alternative {position}",
            )
            if (
                alternative["status"] != _ALTERNATIVE_STATUS
                or alternative["reason"] != _ALTERNATIVE_REASON
            ):
                raise ValueError(f"{family_id} alternative decision is invalid")
            _lane(
                alternative["lane"], "strict-region-paired", candidate_id,
                f"{family_id} alternative {position} lane",
            )
    distributions = _exact(
        root["baseline_distribution"], set(CALIBRATION_METRICS),
        "baseline distribution",
    )
    for metric, distribution_value in distributions.items():
        distribution = _exact(distribution_value, {
            "count", "min", "median", "p95", "max"
        }, f"{metric} distribution")
        if distribution["count"] != len(CALIBRATION_FAMILIES):
            raise ValueError(f"{metric} distribution count is invalid")
        ordered = tuple(_number(distribution[key], f"{metric} {key}") for key in (
            "min", "median", "p95", "max"
        ))
        if ordered != tuple(sorted(ordered)):
            raise ValueError(f"{metric} distribution order is invalid")
        expected = _derived_distribution([
            _number(
                family["baseline"]["configurations"][0]["metrics"][metric],
                f"{family['family_id']} baseline {metric}",
            )
            for family in families
        ])
        if distribution != expected:
            raise ValueError(f"{metric} distribution does not match baseline metrics")
    decision = _exact(root["decision"], {"winner", "status", "reason"}, "decision")
    if (
        decision["winner"] is not False
        or decision["status"] != "calibration-pending"
        or decision["reason"] != "raw corpus distributions are not calibrated acceptance thresholds"
    ):
        raise ValueError("calibration decision is invalid")
    return root
