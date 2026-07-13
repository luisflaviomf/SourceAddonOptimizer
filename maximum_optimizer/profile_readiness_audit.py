from __future__ import annotations

import copy
import hashlib
import json
import math
import re


AUDIT_METRICS = (
    "silhouette_iou",
    "rgb_mae",
    "edge_error",
    "surface_bidirectional_p95",
    "surface_max",
    "normal_angle_p95",
    "uv_error_p95",
    "skinning_error_p95",
)
AUDIT_CLASSES = ("round-rigid-v1", "general-body-detail-v1")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[/\\]")


def _exact(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} is invalid")
    return result


def _hash(value: object, label: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _derived_distribution(samples: list[dict]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for metric in AUDIT_METRICS:
        values = sorted(float(sample["metrics"][metric]) for sample in samples)
        result[metric] = {
            "count": len(values),
            "min": values[0],
            "median": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95),
            "max": values[-1],
        }
    return result


def canonical_profile_readiness_audit_hash(payload: object) -> str:
    if type(payload) is not dict:
        raise ValueError("profile readiness audit must be an object")
    canonical = copy.deepcopy(payload)
    canonical.pop("evidence_sha256", None)
    encoded = (json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metrics(value: object, label: str) -> dict:
    result = _exact(value, set(AUDIT_METRICS), label)
    for metric in AUDIT_METRICS:
        _number(result[metric], f"{label} {metric}")
    return result


def _samples(value: object, verdict: str, label: str) -> list[dict]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a list")
    result: list[dict] = []
    seen: set[str] = set()
    for position, raw in enumerate(value):
        item = _exact(raw, {
            "sample_id", "family_id", "candidate_id", "region", "verdict",
            "evidence_scope", "metrics",
        }, f"{label} sample {position}")
        if (
            type(item["sample_id"]) is not str
            or not item["sample_id"]
            or item["sample_id"] in seen
            or type(item["family_id"]) is not str
            or not item["family_id"]
            or type(item["candidate_id"]) is not str
            or not item["candidate_id"]
            or (item["region"] is not None and (
                type(item["region"]) is not str or not item["region"]
            ))
            or item["verdict"] != verdict
            or type(item["evidence_scope"]) is not str
            or not item["evidence_scope"]
        ):
            raise ValueError(f"{label} sample {position} is invalid")
        seen.add(item["sample_id"])
        _metrics(item["metrics"], f"{label} sample {position} metrics")
        result.append(item)
    return result


def _distribution(value: object, samples: list[dict], label: str) -> None:
    if not samples:
        if value != {}:
            raise ValueError(f"{label} distribution must be empty")
        return
    if value != _derived_distribution(samples):
        raise ValueError(f"{label} distribution is not derived from samples")


def _hypothesis(value: object, label: str) -> None:
    if value is None:
        return
    item = _exact(value, {
        "status", "authorizing", "approved", "margin_fraction", "upper_bounds",
        "separating_metrics", "limitations",
    }, label)
    if (
        item["status"] != "provisional-non-authorizing-hypothesis"
        or item["authorizing"] is not False
        or item["approved"] is not False
    ):
        raise ValueError(f"{label} must remain non-authorizing")
    _number(item["margin_fraction"], f"{label} margin")
    _metrics(item["upper_bounds"], f"{label} upper bounds")
    if (
        type(item["separating_metrics"]) is not list
        or any(metric not in AUDIT_METRICS for metric in item["separating_metrics"])
        or len(set(item["separating_metrics"])) != len(item["separating_metrics"])
        or type(item["limitations"]) is not list
        or not item["limitations"]
        or any(type(text) is not str or not text for text in item["limitations"])
    ):
        raise ValueError(f"{label} details are invalid")


def _scope(value: object, label: str) -> None:
    item = _exact(value, {
        "coverage", "accepted_samples", "rejected_samples", "auxiliary_samples",
        "accepted_distribution", "rejected_distribution", "auxiliary_distribution",
        "provisional_hypothesis",
    }, label)
    accepted = _samples(item["accepted_samples"], "accepted-manual", f"{label} accepted")
    rejected = _samples(item["rejected_samples"], "rejected-manual", f"{label} rejected")
    auxiliary = _samples(item["auxiliary_samples"], "auxiliary-unapproved", f"{label} auxiliary")
    coverage = _exact(item["coverage"], {
        "accepted_sample_count", "rejected_sample_count", "auxiliary_sample_count",
        "notes",
    }, f"{label} coverage")
    if (
        coverage["accepted_sample_count"] != len(accepted)
        or coverage["rejected_sample_count"] != len(rejected)
        or coverage["auxiliary_sample_count"] != len(auxiliary)
        or type(coverage["notes"]) is not list
        or any(type(text) is not str or not text for text in coverage["notes"])
    ):
        raise ValueError(f"{label} coverage is invalid")
    _distribution(item["accepted_distribution"], accepted, f"{label} accepted")
    _distribution(item["rejected_distribution"], rejected, f"{label} rejected")
    _distribution(item["auxiliary_distribution"], auxiliary, f"{label} auxiliary")
    _hypothesis(item["provisional_hypothesis"], f"{label} hypothesis")


def _reject_machine_paths(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "label" and (
                type(child) is not str or not child.startswith("research://")
            ):
                raise ValueError("external artifact must use a research label")
            _reject_machine_paths(child)
    elif isinstance(value, list):
        for child in value:
            _reject_machine_paths(child)
    elif isinstance(value, str) and (
        _DRIVE_PATH.match(value) is not None
        or value.startswith(("/", "\\\\"))
        or "\\" in value
    ):
        raise ValueError("audit contains an absolute or non-canonical machine path")


def parse_profile_readiness_audit(payload: object) -> dict:
    root = _exact(payload, {
        "schema_version", "strategy", "status", "calibrated", "authorizing",
        "scope", "source_artifacts", "metric_names", "classes",
        "noncomparable_observations", "implementation_hashes", "coverage_gaps",
        "required_fresh_matrix", "decision", "evidence_sha256",
    }, "profile readiness audit")
    if (
        root["schema_version"] != 1
        or root["strategy"] != "lvs-profile-readiness-audit-v1"
        or root["status"] != "profile-readiness-insufficient"
        or root["calibrated"] is not False
        or root["authorizing"] is not False
    ):
        raise ValueError("profile readiness audit must remain non-authorizing")
    if (
        type(root["evidence_sha256"]) is not str
        or root["evidence_sha256"] != canonical_profile_readiness_audit_hash(root)
    ):
        raise ValueError("profile readiness audit seal is invalid")
    scope = _exact(root["scope"], {
        "calibration_family_ids", "holdout_consulted", "audit_basis",
    }, "audit scope")
    if (
        scope["holdout_consulted"] is not False
        or type(scope["calibration_family_ids"]) is not list
        or len(scope["calibration_family_ids"]) != 5
        or len(set(scope["calibration_family_ids"])) != 5
        or type(scope["audit_basis"]) is not str
        or not scope["audit_basis"]
        or root["metric_names"] != list(AUDIT_METRICS)
    ):
        raise ValueError("audit scope is invalid")
    if type(root["source_artifacts"]) is not list or not root["source_artifacts"]:
        raise ValueError("source artifacts are invalid")
    for position, artifact in enumerate(root["source_artifacts"]):
        item = _exact(artifact, {
            "kind", "label", "file_sha256", "payload_sha256",
        }, f"source artifact {position}")
        if type(item["kind"]) is not str or not item["kind"]:
            raise ValueError(f"source artifact {position} is invalid")
        _hash(item["file_sha256"], f"source artifact {position} file hash")
        _hash(item["payload_sha256"], f"source artifact {position} payload hash")
    classes = _exact(root["classes"], set(AUDIT_CLASSES), "audit classes")
    for class_name in AUDIT_CLASSES:
        class_item = _exact(classes[class_name], {"whole", "focused"}, class_name)
        _scope(class_item["whole"], f"{class_name} whole")
        _scope(class_item["focused"], f"{class_name} focused")
    if (
        type(root["noncomparable_observations"]) is not list
        or not root["noncomparable_observations"]
    ):
        raise ValueError("noncomparable observations are invalid")
    for position, raw in enumerate(root["noncomparable_observations"]):
        item = _exact(raw, {
            "sample_id", "class", "scope", "verdict", "evidence_scope",
            "available_metrics", "missing_metrics", "reason",
        }, f"noncomparable observation {position}")
        available = item["available_metrics"]
        missing = item["missing_metrics"]
        if (
            item["class"] not in AUDIT_CLASSES
            or item["scope"] not in ("whole", "focused")
            or item["verdict"] != "rejected-manual-incomparable"
            or type(item["evidence_scope"]) is not str
            or not item["evidence_scope"]
            or type(available) is not dict
            or not available
            or any(metric not in AUDIT_METRICS for metric in available)
            or type(missing) is not list
            or set(available) | set(missing) != set(AUDIT_METRICS)
            or set(available) & set(missing)
            or type(item["reason"]) is not str
            or not item["reason"]
        ):
            raise ValueError(f"noncomparable observation {position} is invalid")
        for metric, number in available.items():
            _number(number, f"noncomparable observation {position} {metric}")
    implementations = _exact(root["implementation_hashes"], {
        "archived_calibration_v3", "current_at_audit",
    }, "implementation hashes")
    for group_name, group in implementations.items():
        values = _exact(group, {
            "visual_validation", "render_previews", "run_visual_states",
            "build_calibration_corpus",
        }, group_name)
        for name, digest in values.items():
            _hash(digest, f"{group_name} {name}")
    if (
        type(root["coverage_gaps"]) is not list
        or not root["coverage_gaps"]
        or any(type(item) is not str or not item for item in root["coverage_gaps"])
        or type(root["required_fresh_matrix"]) is not list
        or not root["required_fresh_matrix"]
    ):
        raise ValueError("audit coverage requirements are invalid")
    for position, raw in enumerate(root["required_fresh_matrix"]):
        item = _exact(raw, {
            "matrix_id", "class", "scope", "subjects", "state_or_target_contract",
            "repeat_contract", "evidence_required", "status", "authorizing",
        }, f"fresh matrix {position}")
        if (
            item["class"] not in AUDIT_CLASSES
            or item["scope"] not in ("whole", "focused")
            or type(item["subjects"]) is not list
            or not item["subjects"]
            or any(type(subject) is not str or not subject.startswith("research://")
                   for subject in item["subjects"])
            or item["status"] != "required-before-profile-calibration"
            or item["authorizing"] is not False
            or any(type(item[field]) is not str or not item[field] for field in (
                "matrix_id", "state_or_target_contract", "repeat_contract",
                "evidence_required",
            ))
        ):
            raise ValueError(f"fresh matrix {position} is invalid")
    decision = _exact(root["decision"], {
        "status", "authorizing", "reason_code", "reason",
    }, "audit decision")
    if (
        decision["status"] != "insufficient-for-profile-activation"
        or decision["authorizing"] is not False
        or decision["reason_code"] != "missing-focused-round-rigid-evidence"
        or type(decision["reason"]) is not str
        or not decision["reason"]
    ):
        raise ValueError("audit decision must remain non-authorizing")
    _reject_machine_paths(root)
    return copy.deepcopy(root)
