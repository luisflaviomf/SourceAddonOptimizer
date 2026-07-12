from __future__ import annotations

import hashlib
import json
import re


_SHA = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FAMILIES = ("dodge_charger", "toyota_supra", "nissan_skyline_gtr32")
_RATIOS = (0.35, 0.30, 0.25)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _keys(value: object, expected: set[str], context: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{context} fields are invalid")
    return value


def _artifacts(value: object, context: str) -> tuple[dict, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{context} artifacts are invalid")
    result = []
    seen = set()
    for raw in value:
        item = _keys(raw, {"path", "size_bytes", "sha256"}, context)
        path = item["path"]
        if (
            type(path) is not str
            or not path
            or "\\" in path
            or path.startswith("/")
            or ":" in path
            or ".." in path.split("/")
            or path in seen
        ):
            raise ValueError(f"{context} artifact path is not portable")
        if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0:
            raise ValueError(f"{context} artifact size is invalid")
        if type(item["sha256"]) is not str or _SHA.fullmatch(item["sha256"]) is None:
            raise ValueError(f"{context} artifact hash is invalid")
        seen.add(path)
        result.append(item)
    if [item["path"] for item in result] != sorted(seen):
        raise ValueError(f"{context} artifacts must be sorted")
    return tuple(result)


def _lane(value: object, context: str) -> tuple[dict, ...]:
    lane = _keys(value, {"total_bytes", "artifacts"}, context)
    artifacts = _artifacts(lane["artifacts"], context)
    if lane["total_bytes"] != sum(item["size_bytes"] for item in artifacts):
        raise ValueError(f"{context} total does not match artifacts")
    return artifacts


def parse_ratio_sweep_evidence(raw: object) -> dict:
    root = _keys(
        raw,
        {
            "schema_version",
            "strategy",
            "implementation",
            "toolchain",
            "families",
            "quality",
            "evidence_sha256",
        },
        "evidence",
    )
    if root["schema_version"] != 1 or root["strategy"] != "blender-adaptive-ratio-sweep-v1":
        raise ValueError("unsupported ratio sweep evidence")
    implementation = _keys(root["implementation"], {"source_commit", "scripts"}, "implementation")
    if (
        type(implementation["source_commit"]) is not str
        or _GIT_COMMIT.fullmatch(implementation["source_commit"]) is None
        or type(implementation["scripts"]) is not dict
        or not implementation["scripts"]
        or any(
            type(name) is not str
            or not name
            or type(digest) is not str
            or _SHA.fullmatch(digest) is None
            for name, digest in implementation["scripts"].items()
        )
    ):
        raise ValueError("implementation provenance is invalid")
    tools = _keys(root["toolchain"], {"blender", "studiomdl"}, "toolchain")
    for raw_tool in tools.values():
        tool = _keys(raw_tool, {"version", "sha256"}, "tool")
        if type(tool["version"]) is not str or not tool["version"] or _SHA.fullmatch(tool["sha256"]) is None:
            raise ValueError("tool provenance is invalid")
    if type(root["families"]) is not list or len(root["families"]) != len(_FAMILIES):
        raise ValueError("ratio sweep families are invalid")
    family_ids = []
    for family_raw in root["families"]:
        family = _keys(family_raw, {"family_id", "baselines", "attempts", "decision"}, "family")
        family_ids.append(family["family_id"])
        baselines = _keys(family["baselines"], {"original", "b050", "r040"}, "baselines")
        for name, lane in baselines.items():
            _lane(lane, f"baseline {name}")
        if type(family["attempts"]) is not list or len(family["attempts"]) != 3:
            raise ValueError("each family must contain the three planned attempts")
        totals = {}
        for expected_ratio, attempt_raw in zip(_RATIOS, family["attempts"]):
            attempt = _keys(
                attempt_raw,
                {
                    "ratio",
                    "candidate",
                    "triangles",
                    "fallback_summary",
                    "fallbacks",
                    "qc",
                    "candidate_metrics_sha256",
                    "candidate_payload_sha256",
                    "compile_log_sha256",
                    "compile_summary_sha256",
                },
                "attempt",
            )
            if type(attempt["ratio"]) not in (int, float) or attempt["ratio"] != expected_ratio:
                raise ValueError("attempt ratios/order changed")
            _lane(attempt["candidate"], "candidate")
            totals[float(attempt["ratio"])] = attempt["candidate"]["total_bytes"]
            triangles = _keys(attempt["triangles"], {"before", "after"}, "triangles")
            if (
                type(triangles["before"]) is not int
                or type(triangles["after"]) is not int
                or not 0 < triangles["after"] < triangles["before"]
            ):
                raise ValueError("triangle evidence is invalid")
            for name in (
                "candidate_metrics_sha256",
                "candidate_payload_sha256",
                "compile_log_sha256",
                "compile_summary_sha256",
            ):
                if type(attempt[name]) is not str or _SHA.fullmatch(attempt[name]) is None:
                    raise ValueError("attempt provenance hash is invalid")
            if type(attempt["fallbacks"]) is not list:
                raise ValueError("fallbacks must be an array")
            fallback_bytes = 0
            fallback_paths = []
            for fallback_raw in attempt["fallbacks"]:
                fallback = _keys(
                    fallback_raw,
                    {"source", "reason", "size_bytes", "source_sha256", "output_sha256"},
                    "fallback",
                )
                path = fallback["source"]
                if (
                    type(path) is not str
                    or not path
                    or "\\" in path
                    or path.startswith("/")
                    or ":" in path
                    or ".." in path.split("/")
                    or type(fallback["reason"]) is not str
                    or not fallback["reason"]
                    or type(fallback["size_bytes"]) is not int
                    or fallback["size_bytes"] <= 0
                    or fallback["source_sha256"] != fallback["output_sha256"]
                    or _SHA.fullmatch(fallback["source_sha256"]) is None
                ):
                    raise ValueError("fallback is not byte-exact portable evidence")
                fallback_paths.append(path)
                fallback_bytes += fallback["size_bytes"]
            if fallback_paths != sorted(fallback_paths) or len(set(fallback_paths)) != len(fallback_paths):
                raise ValueError("fallbacks must be uniquely sorted")
            summary = _keys(attempt["fallback_summary"], {"count", "bytes"}, "fallback summary")
            if summary != {"count": len(attempt["fallbacks"]), "bytes": fallback_bytes}:
                raise ValueError("fallback summary is inconsistent")
            qc = _keys(attempt["qc"], {"status", "differing_fields"}, "qc")
            if qc != {"status": "pass", "differing_fields": ["mesh_files"]}:
                raise ValueError("QC fingerprint did not pass")
        decision = _keys(family["decision"], {"ratio", "total_bytes", "reason"}, "decision")
        best_ratio, best_bytes = min(totals.items(), key=lambda item: (item[1], item[0]))
        if (
            decision["ratio"] != best_ratio
            or decision["total_bytes"] != best_bytes
            or type(decision["reason"]) is not str
            or not decision["reason"]
        ):
            raise ValueError("decision is not the actual StudioMDL byte minimum")
    if tuple(family_ids) != _FAMILIES:
        raise ValueError("ratio sweep family order changed")
    quality = _keys(root["quality"], {"status", "visual_gate", "reason"}, "quality")
    if (
        quality["status"] != "unverified"
        or quality["visual_gate"] != "separate-pending"
        or type(quality["reason"]) is not str
        or not quality["reason"]
    ):
        raise ValueError("ratio sweep cannot claim visual quality")
    digest = root["evidence_sha256"]
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        raise ValueError("evidence digest is invalid")
    unsigned = dict(root)
    unsigned.pop("evidence_sha256")
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
        raise ValueError("evidence digest mismatch")
    return root
