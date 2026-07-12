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
    "pontiac_transam_wheel": "r050",
    "dodge_charger": "r030",
    "toyota_supra": "r015",
    "nissan_skyline_gtr32": "r020",
    "dodge_monaco_police": "hybrid-stable",
}
_ALTERNATIVES = {
    "pontiac_transam_wheel": (
        ("r035", "strict-visual-rejection"),
        ("v4-r035", "strict-visual-rejection"),
        ("v4-r045", "strict-visual-rejection"),
        ("v4-r0475", "strict-visual-rejection"),
        ("importance-r035", "uncalibrated-raw-clay-rejection"),
    ),
    "dodge_charger": (),
    "toyota_supra": (),
    "nissan_skyline_gtr32": (),
    "dodge_monaco_police": (),
}
_ALTERNATIVE_STATUS = "rejected-research-alternative"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_STATE_NAMES = {
    "pontiac_transam_wheel": (
        "engine-default", "bodygroup-rim-001-1569ec5b-1",
    ),
    "dodge_charger": (
        "engine-default", "bodygroup-steering_wheel-001-b81bc2a7-1",
        "bodygroup-hood-002-2314555c-1", "bodygroup-hood-002-2314555c-2",
        "bodygroup-trunk-003-3e341d2d-1",
    ),
    "toyota_supra": (
        "engine-default", "bodygroup-front_bumper-001-9b0d8061-1",
        "bodygroup-front_bumper-001-9b0d8061-2",
        "bodygroup-front_bumper-001-9b0d8061-3",
        "bodygroup-rear_bumper-002-611e4375-1",
    ),
    "nissan_skyline_gtr32": (
        "engine-default", "bodygroup-front_bumper-002-9b0d8061-1",
        "bodygroup-front_bumper-002-9b0d8061-2",
        "bodygroup-rear_fenders-004-21ab784a-1",
        "bodygroup-rear_fenders-004-21ab784a-2",
    ),
    "dodge_monaco_police": (
        "engine-default", "bodygroup-lightbar-001-605787d6-1",
        "bodygroup-lightbar-001-605787d6-2",
        "bodygroup-lightbar-001-605787d6-3",
        "bodygroup-spotlight-002-be46a22b-1",
    ),
}
_MONACO_EXTERNAL = {
    "file_sha256": "c3717c8dda5bf0c03eac22a3017d534c8374a125e785e28eba5f7de56f9f897f",
    "canonical_payload_sha256": "713c09b7b177bb6c949d696f20966251144887bebec7c68839b56f13b57fdbb9",
    "candidate_metrics_sha256": "4f5001804dfb5002915289dbb62cb2029eb5e1d76fe6c361e999be4e0431faa5",
}
EXPECTED_FAMILY_BINDING_SEALS = {
    "pontiac_transam_wheel": {
        "baseline": "d79e659975123b616ecb7b3942cfa1574a86f1c1aabb8288d05c6b40c49e69ae",
        "candidate": "f9fcfe65f4c596521315e8caa04d800b1c98f73962fc72f576f71457c246772f",
    },
    "dodge_charger": {
        "baseline": "31c8f3160f22fd2879510a3c7d598a0b53493773eeeee0f81a70f67160b3ee49",
        "candidate": "d538625dfc8f69300fec92d8f8e38e5dadf771dee9099cdd24d4f0663a45afca",
    },
    "toyota_supra": {
        "baseline": "bbe2953da126f54269587c5847395becabb0c79d07bdf88c32724ffc1d4b80bb",
        "candidate": "91a61f6f727fdc0e3c9869e848a62fe8caa7e5add29d232592fb2c7200350261",
    },
    "nissan_skyline_gtr32": {
        "baseline": "50658fefae9a123872c8bf6dfe8fa218d78d25e17dde07139b2314127627d49a",
        "candidate": "4a0c4f87b96de84b6a22cb1e1e9bd0bcf219ea72e2a237e48bab1756eb29bf33",
    },
    "dodge_monaco_police": {
        "baseline": "ba07ac488d71bf33b3cd60e1c47cf95022be307fabfab8ccfec3edce0b73b21a",
        "candidate": "f43c24d125265e91b2985b88ae1e9b70d2a52007abe28c89d2e56807d8c89cc2",
    },
}
EXPECTED_ALTERNATIVE_BINDING_SEALS = {
    "r035": "0924df022b01aaa70509eee6870538a4c5c53d5323a563b8fd79599651827c39",
    "v4-r035": "3ac02abd23be42b8bedcbaa461e2d3df4ef6ee48cfdbdb9c0af7208b41b7b522",
    "v4-r045": "d2054f483fff9803f0f440bd55698c5e621fa418a6034f4f0acbf4ab21fdc8ac",
    "v4-r0475": "a7ca4775bad1f4ab66048c9426937d3d987667bab6df6ea7b7359a13c95622d5",
    "importance-r035": "60e0b6af16e1b98b70cb69a1ee9457410a79cbd063cf75bd66862ffd422f5682",
}


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


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256((json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")).hexdigest()


def canonical_compiled_hash(compiled: object) -> str:
    return _canonical_hash(compiled)


def canonical_lane_binding_hash(lane: object) -> str:
    if type(lane) is not dict:
        raise ValueError("lane binding must be an object")
    return _canonical_hash({
        "lane": lane.get("lane"),
        "candidate_id": lane.get("candidate_id"),
        "raw_visual_states_sha256": lane.get("raw_visual_states_sha256"),
        "compiled": lane.get("compiled"),
        "source_pairs": [
            {
                "name": item.get("name"),
                "source_pairs": item.get("source_pairs"),
            }
            for item in lane.get("configurations", ())
        ],
        "provenance": lane.get("provenance"),
    })


def _configuration(value: object, lane: str, label: str) -> dict:
    item = _exact(value, {
        "name", "scope", "bodygroups", "reference_manifest_sha256",
        "candidate_manifest_sha256", "metrics", "missing_materials",
        "geometry_audit", "top_regions", "source_pairs",
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
    pairs = item["source_pairs"]
    if type(pairs) is not list or not pairs:
        raise ValueError(f"{label} source pairs are invalid")
    identities = set()
    for position, pair_value in enumerate(pairs):
        pair = _exact(pair_value, {
            "source_identity", "reference_sha256", "candidate_sha256"
        }, f"{label} source pair {position}")
        if (
            type(pair["source_identity"]) is not str or not pair["source_identity"]
            or pair["source_identity"].casefold() in identities
            or any(
                type(pair[field]) is not str or _HASH.fullmatch(pair[field]) is None
                for field in ("reference_sha256", "candidate_sha256")
            )
        ):
            raise ValueError(f"{label} source pair is invalid")
        identities.add(pair["source_identity"].casefold())
    return item


def _compiled(value: object, label: str) -> dict:
    compiled = _exact(value, {"total_bytes", "artifacts"}, label)
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
    return compiled


def _lane(
    value: object, expected_lane: str, expected_candidate: str,
    expected_states: tuple[str, ...], label: str,
) -> dict:
    lane = _exact(value, {
        "lane", "candidate_id", "compiled", "configurations",
        "raw_visual_states_sha256", "provenance", "lane_binding_sha256",
    }, label)
    if lane["lane"] != expected_lane or lane["candidate_id"] != expected_candidate:
        raise ValueError(f"{label} identity is invalid")
    _compiled(lane["compiled"], f"{label} compiled")
    if (
        type(lane["raw_visual_states_sha256"]) is not str
        or _HASH.fullmatch(lane["raw_visual_states_sha256"]) is None
    ):
        raise ValueError(f"{label} raw summary hash is invalid")
    provenance = _exact(lane["provenance"], {"kind", "artifacts"}, f"{label} provenance")
    if expected_candidate == "roundtrip-control":
        expected_kind = "roundtrip-control-record-v1"
        expected_artifact_kinds = ("control-record",)
    elif expected_candidate == "hybrid-stable":
        expected_kind = "accepted-composite-bundle-v1"
        expected_artifact_kinds = ("accepted-composite", "optimized-qc", "compile-summary")
    else:
        expected_kind = "optimization-compile-bundle-v1"
        expected_artifact_kinds = (
            "candidate-json", "candidate-metrics", "optimized-qc", "compile-summary"
        )
    if provenance["kind"] != expected_kind or type(provenance["artifacts"]) is not list:
        raise ValueError(f"{label} provenance identity is invalid")
    artifact_kinds = []
    for position, artifact_value in enumerate(provenance["artifacts"]):
        artifact = _exact(
            artifact_value, {"kind", "path", "sha256"},
            f"{label} provenance artifact {position}",
        )
        if (
            type(artifact["kind"]) is not str
            or type(artifact["path"]) is not str or not artifact["path"]
            or type(artifact["sha256"]) is not str
            or _HASH.fullmatch(artifact["sha256"]) is None
        ):
            raise ValueError(f"{label} provenance artifact is invalid")
        artifact_kinds.append(artifact["kind"])
    if tuple(artifact_kinds) != expected_artifact_kinds:
        raise ValueError(f"{label} provenance artifact set/order is invalid")
    configurations = lane["configurations"]
    if type(configurations) is not list or len(configurations) != len(expected_states):
        raise ValueError(f"{label} configurations are invalid")
    parsed = [
        _configuration(item, expected_lane, f"{label} configuration {position}")
        for position, item in enumerate(configurations)
    ]
    if len({item["name"] for item in parsed}) != len(parsed):
        raise ValueError(f"{label} configuration names are duplicated")
    if tuple(item["name"] for item in parsed) != expected_states:
        raise ValueError(f"{label} state matrix is invalid")
    if expected_lane.startswith("aggregate"):
        if len(parsed) != 1 or not parsed[0]["name"].startswith(
            "aggregate-appearance-anchor-not-structural-baseline:engine-default"
        ):
            raise ValueError(f"{label} aggregate scope is invalid")
    elif parsed[0]["name"] != "engine-default":
        raise ValueError(f"{label} must begin with engine-default")
    if (
        type(lane["lane_binding_sha256"]) is not str
        or lane["lane_binding_sha256"] != canonical_lane_binding_hash(lane)
    ):
        raise ValueError(f"{label} lane binding is invalid")
    return lane


def _byte_evidence(family: dict, label: str) -> None:
    denominators = _exact(
        family["byte_denominators"], {"shipped_original", "roundtrip_control"},
        f"{label} byte denominators",
    )
    expected_kinds = {
        "shipped_original": "shipped-original-compiled-family-v1",
        "roundtrip_control": "strict-roundtrip-control-compiled-family-v1",
    }
    parsed = {}
    for name, kind in expected_kinds.items():
        item = _exact(
            denominators[name], {"kind", "compiled"}, f"{label} {name} denominator"
        )
        if item["kind"] != kind:
            raise ValueError(f"{label} {name} denominator kind is invalid")
        parsed[name] = _compiled(item["compiled"], f"{label} {name} compiled")
    if parsed["roundtrip_control"] != family["baseline"]["compiled"]:
        raise ValueError(f"{label} roundtrip denominator does not match baseline")

    comparison = _exact(
        family["byte_comparison"], {
            "candidate_bytes", "versus_shipped_original", "versus_roundtrip_control"
        }, f"{label} byte comparison",
    )
    candidate_bytes = family["candidate"]["compiled"]["total_bytes"]
    if comparison["candidate_bytes"] != candidate_bytes:
        raise ValueError(f"{label} candidate byte total drift")
    for name, field in (
        ("shipped_original", "versus_shipped_original"),
        ("roundtrip_control", "versus_roundtrip_control"),
    ):
        item = _exact(comparison[field], {
            "denominator_kind", "denominator_bytes", "saved_bytes", "reduction_fraction"
        }, f"{label} {field}")
        denominator = parsed[name]["total_bytes"]
        saved = denominator - candidate_bytes
        if (
            item["denominator_kind"] != expected_kinds[name]
            or item["denominator_bytes"] != denominator
            or item["saved_bytes"] != saved
            or saved < 0
            or not math.isclose(
                _number(item["reduction_fraction"], f"{label} reduction fraction"),
                saved / denominator,
                rel_tol=0.0,
                abs_tol=1e-15,
            )
        ):
            raise ValueError(f"{label} {field} is not derived from typed denominator")


def _alternative(value: object, expected: tuple[str, str], label: str) -> dict:
    item = _exact(value, {
        "candidate_id", "evidence_kind", "status", "reason", "evidence"
    }, label)
    candidate_id, evidence_kind = expected
    if (
        item["candidate_id"] != candidate_id
        or item["evidence_kind"] != evidence_kind
        or item["status"] != _ALTERNATIVE_STATUS
        or type(item["reason"]) is not str
        or not item["reason"]
    ):
        raise ValueError(f"{label} identity is invalid")
    evidence = _exact(item["evidence"], {
        "artifact_path", "artifact_sha256", "payload_sha256", "artifact_availability",
        "quality_scope", "compiled", "compiled_sha256", "winner",
    }, f"{label} evidence")
    if (
        type(evidence["artifact_path"]) is not str or not evidence["artifact_path"]
        or type(evidence["artifact_sha256"]) is not str
        or _HASH.fullmatch(evidence["artifact_sha256"]) is None
        or type(evidence["payload_sha256"]) is not str
        or _HASH.fullmatch(evidence["payload_sha256"]) is None
        or evidence["winner"] is not False
    ):
        raise ValueError(f"{label} evidence identity is invalid")
    if evidence_kind == "strict-visual-rejection":
        if evidence["quality_scope"] != "strict-region-paired":
            raise ValueError(f"{label} strict scope is invalid")
        if candidate_id == "r035":
            if (
                evidence["artifact_availability"] != "archived-visual-only"
                or evidence["compiled"] is not None
                or evidence["compiled_sha256"] is not None
            ):
                raise ValueError(f"{label} archived-only evidence cannot claim bytes")
        elif evidence["artifact_availability"] != "archived-visual-and-compiled":
            raise ValueError(f"{label} artifact availability is invalid")
        else:
            _compiled(evidence["compiled"], f"{label} compiled")
    else:
        if (
            evidence["quality_scope"] != "rim1-bind-8-views"
            or evidence["artifact_availability"] != "committed-raw-clay-and-compiled"
        ):
            raise ValueError(f"{label} raw clay scope is invalid")
        _compiled(evidence["compiled"], f"{label} compiled")
    if evidence["compiled"] is not None and (
        type(evidence["compiled_sha256"]) is not str
        or evidence["compiled_sha256"] != canonical_compiled_hash(evidence["compiled"])
    ):
        raise ValueError(f"{label} compiled binding is invalid")
    if _canonical_hash(item) != EXPECTED_ALTERNATIVE_BINDING_SEALS[candidate_id]:
        raise ValueError(f"{label} differs from the trusted research snapshot")
    return item


def parse_calibration_evidence(payload: object) -> dict:
    root = _exact(payload, {
        "schema_version", "strategy", "status", "toolchain", "implementation",
        "external_artifacts", "families", "baseline_distribution", "decision",
        "evidence_sha256",
    }, "calibration evidence")
    if (
        root["schema_version"] != 2
        or root["strategy"] != "lvs-calibration-corpus-v2"
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
    external = _exact(
        root["external_artifacts"], {"monaco_accepted_composite_v1"},
        "external artifacts",
    )
    composite = _exact(external["monaco_accepted_composite_v1"], {
        "file_sha256", "canonical_payload_sha256", "candidate_metrics_sha256"
    }, "Monaco composite")
    if any(
        type(value) is not str or _HASH.fullmatch(value) is None
        for value in composite.values()
    ):
        raise ValueError("Monaco composite hashes are invalid")
    if composite != _MONACO_EXTERNAL:
        raise ValueError("Monaco composite seal is not the accepted artifact")
    families = root["families"]
    if type(families) is not list or tuple(
        item.get("family_id") if type(item) is dict else None for item in families
    ) != CALIBRATION_FAMILIES:
        raise ValueError("calibration family set/order is invalid")
    for family_id, family_value in zip(CALIBRATION_FAMILIES, families):
        family = _exact(
            family_value,
            {
                "family_id", "byte_denominators", "byte_comparison", "baseline",
                "candidate", "alternatives",
            },
            family_id,
        )
        baseline = _lane(
            family["baseline"],
            "strict-region-paired",
            "roundtrip-control",
            _STATE_NAMES[family_id],
            f"{family_id} baseline",
        )
        candidate = _lane(
            family["candidate"], "strict-region-paired", _CANDIDATES[family_id],
            _STATE_NAMES[family_id],
            f"{family_id} candidate",
        )
        trusted = EXPECTED_FAMILY_BINDING_SEALS[family_id]
        if (
            baseline["lane_binding_sha256"] != trusted["baseline"]
            or candidate["lane_binding_sha256"] != trusted["candidate"]
        ):
            raise ValueError(f"{family_id} differs from the trusted calibration snapshot")
        for baseline_config, candidate_config in zip(
            baseline["configurations"], candidate["configurations"]
        ):
            if baseline_config["bodygroups"] != candidate_config["bodygroups"]:
                raise ValueError(f"{family_id} baseline/candidate bodygroups differ")
            baseline_pairs = baseline_config["source_pairs"]
            candidate_pairs = candidate_config["source_pairs"]
            if tuple(
                (item["source_identity"], item["reference_sha256"])
                for item in baseline_pairs
            ) != tuple(
                (item["source_identity"], item["reference_sha256"])
                for item in candidate_pairs
            ):
                raise ValueError(f"{family_id} source pair references differ")
            if any(
                item["reference_sha256"] != item["candidate_sha256"]
                for item in baseline_pairs
            ):
                raise ValueError(f"{family_id} roundtrip source pairs are not exact")
        _byte_evidence(family, family_id)
        alternatives = family["alternatives"]
        expected_alternatives = _ALTERNATIVES[family_id]
        if type(alternatives) is not list or len(alternatives) != len(expected_alternatives):
            raise ValueError(f"{family_id} alternatives are invalid")
        for position, (alternative_value, expected) in enumerate(zip(
            alternatives, expected_alternatives
        )):
            _alternative(alternative_value, expected, f"{family_id} alternative {position}")
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
