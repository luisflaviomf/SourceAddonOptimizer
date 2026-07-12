from __future__ import annotations

import hashlib
import json
import re

from .compiler_aware import compiler_proxy_bytes


_SHA = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _keys(value: object, expected: set[str], context: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{context} fields are invalid")
    return value


def _artifacts(value: object, context: str) -> tuple[dict, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{context} artifacts are invalid")
    result = []
    paths = set()
    for raw in value:
        item = _keys(raw, {"path", "size_bytes", "sha256"}, context)
        path = item["path"]
        if (type(path) is not str or not path or "\\" in path or path.startswith("/")
                or ":" in path or ".." in path.split("/") or path in paths):
            raise ValueError(f"{context} artifact path is not portable")
        if type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            raise ValueError(f"{context} artifact size is invalid")
        if type(item["sha256"]) is not str or _SHA.fullmatch(item["sha256"]) is None:
            raise ValueError(f"{context} artifact hash is invalid")
        paths.add(path)
        result.append(item)
    if [item["path"] for item in result] != sorted(paths):
        raise ValueError(f"{context} artifacts must be sorted")
    return tuple(result)


def parse_compiler_aware_evidence(raw: object) -> dict:
    root = _keys(raw, {
        "schema_version", "strategy", "toolchain", "records", "quality",
        "implementation", "scoring", "decision", "evidence_sha256",
    }, "evidence")
    if root["schema_version"] != 1 or root["strategy"] != "blender-adaptive-v1":
        raise ValueError("unsupported adaptive evidence")
    tools = _keys(root["toolchain"], {"blender", "studiomdl"}, "toolchain")
    for value in tools.values():
        item = _keys(value, {"version", "sha256"}, "tool")
        if type(item["version"]) is not str or not item["version"] or _SHA.fullmatch(item["sha256"]) is None:
            raise ValueError("tool provenance is invalid")
    implementation = _keys(root["implementation"], {"scripts"}, "implementation")
    scripts = implementation["scripts"]
    if type(scripts) is not dict or not scripts or any(
        type(name) is not str or not name or type(value) is not str or _SHA.fullmatch(value) is None
        for name, value in scripts.items()
    ):
        raise ValueError("implementation script hashes are invalid")
    scoring = _keys(root["scoring"], {"selector", "proxy_role"}, "scoring")
    if scoring != {
        "selector": "actual-studiomdl-compiled-bytes",
        "proxy_role": "diagnostic-only-confirmed-by-snapshot",
    }:
        raise ValueError("Task 7 scoring must select actual compiled bytes")
    if type(root["records"]) is not list or len(root["records"]) != 2:
        raise ValueError("adaptive evidence must contain two checkpoint records")
    family_ids = []
    for record_raw in root["records"]:
        record = _keys(record_raw, {
            "family_id", "ratio", "baseline", "candidate", "triangles", "proxy",
            "fallbacks", "qc", "candidate_metrics_sha256", "candidate_payload_sha256",
            "compile_log_sha256",
        }, "record")
        family_ids.append(record["family_id"])
        if any(_SHA.fullmatch(record[name]) is None for name in (
            "candidate_metrics_sha256", "candidate_payload_sha256", "compile_log_sha256"
        )):
            raise ValueError("record input/output hashes are invalid")
        if type(record["ratio"]) not in (int, float) or not 0 < record["ratio"] < 1:
            raise ValueError("record ratio is invalid")
        lanes = {}
        for lane in ("baseline", "candidate"):
            lane_value = _keys(record[lane], {"total_bytes", "artifacts"}, lane)
            artifacts = _artifacts(lane_value["artifacts"], lane)
            if lane_value["total_bytes"] != sum(item["size_bytes"] for item in artifacts):
                raise ValueError(f"{lane} total does not match artifacts")
            lanes[lane] = artifacts
        triangles = _keys(record["triangles"], {"before", "after"}, "triangles")
        if any(type(triangles[name]) is not int or triangles[name] <= 0 for name in triangles):
            raise ValueError("triangle counts are invalid")
        if triangles["after"] >= triangles["before"]:
            raise ValueError("candidate must reduce triangles")
        proxy = _keys(record["proxy"], {
            "compiled_vertices", "triangles", "predicted_bytes", "actual_payload_bytes",
            "absolute_error_bytes",
        }, "proxy")
        predicted = compiler_proxy_bytes(proxy["compiled_vertices"], proxy["triangles"])
        if proxy["triangles"] != triangles["after"] or proxy["predicted_bytes"] != predicted:
            raise ValueError("compiler proxy is inconsistent")
        candidate_by_path = {item["path"]: item for item in lanes["candidate"]}
        payload = sum(
            item["size_bytes"] for path, item in candidate_by_path.items()
            if path.endswith((".vvd", ".dx80.vtx", ".dx90.vtx"))
        )
        if (proxy["actual_payload_bytes"] != payload
                or proxy["absolute_error_bytes"] != abs(payload - predicted)):
            raise ValueError("compiler proxy snapshot does not match artifacts")
        if type(record["fallbacks"]) is not list:
            raise ValueError("fallbacks must be an array")
        for fallback_raw in record["fallbacks"]:
            fallback = _keys(fallback_raw, {
                "source", "reason", "source_sha256", "output_sha256", "size_bytes",
            }, "fallback")
            if (type(fallback["source"]) is not str or "\\" in fallback["source"]
                    or fallback["source"].startswith("/") or ":" in fallback["source"]
                    or type(fallback["reason"]) is not str or not fallback["reason"]
                    or fallback["source_sha256"] != fallback["output_sha256"]
                    or _SHA.fullmatch(fallback["source_sha256"]) is None
                    or type(fallback["size_bytes"]) is not int or fallback["size_bytes"] <= 0):
                raise ValueError("fallback is not byte-exact portable evidence")
        qc = _keys(record["qc"], {"status", "differing_fields"}, "qc")
        if qc["status"] != "pass" or qc["differing_fields"] != ["mesh_files"]:
            raise ValueError("QC evidence is not the expected optimized-source mapping")
    if family_ids != ["pontiac_transam_wheel", "dodge_charger"]:
        raise ValueError("checkpoint families or order changed")
    quality = _keys(root["quality"], {
        "status", "texture_status", "render_status", "reason",
    }, "quality")
    if quality["status"] != "unverified" or quality["render_status"] == "pass":
        raise ValueError("Task 7 quality must remain unverified")
    decision = _keys(root["decision"], {
        "winner", "reason", "pressure_families_run", "required_pressure_families",
    }, "decision")
    if decision["winner"] is not False or decision["pressure_families_run"] != 1 \
            or decision["required_pressure_families"] != 5:
        raise ValueError("Task 7 cannot authorize a winner")
    digest = root["evidence_sha256"]
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        raise ValueError("evidence digest is invalid")
    unsigned = dict(root)
    unsigned.pop("evidence_sha256")
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
        raise ValueError("evidence digest mismatch")
    return root
