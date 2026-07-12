from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any

SHA_RE = re.compile(r"[0-9a-f]{64}")
RATIOS = (0.85, 0.70, 0.55, 0.40, 0.25)
SOURCES = ("wh.smd", "wh1.smd", "wh2.smd")
KINDS = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".phy")


def canonical_digest(payload: dict[str, Any]) -> str:
    clean = dict(payload)
    clean.pop("evidence_sha256", None)
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def seal_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["evidence_sha256"] = canonical_digest(result)
    return result


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} schema is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if type(value) is not str or SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def _path(value: object, label: str) -> str:
    if type(value) is not str or not value or "\\" in value or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
        raise ValueError(f"{label} is not a portable relative path")
    return value


def _artifact(value: object, expected_kind: str, label: str) -> dict[str, Any]:
    item = _exact(value, {"path", "kind", "size_bytes", "sha256"}, label)
    if item["kind"] != expected_kind or type(item["size_bytes"]) is not int or item["size_bytes"] <= 0:
        raise ValueError(f"{label} kind or size is invalid")
    _path(item["path"], f"{label}.path")
    _sha(item["sha256"], f"{label}.sha256")
    return item


def load_meshopt_direct_evidence(payload: object) -> dict[str, Any]:
    root = _exact(payload, {"schema_version", "corpus_id", "strategy", "quality_status", "quality_claim", "settings", "tools", "sources", "records", "decision", "evidence_sha256"}, "evidence")
    if root["schema_version"] != 2 or root["corpus_id"] != "lvs-models-v1" or root["strategy"] != "meshopt-direct-v1" or root["quality_status"] != "unverified" or root["quality_claim"] is not None:
        raise ValueError("evidence identity or quality state is invalid")
    settings = _exact(root["settings"], {"ratios", "update_vertices", "transfer"}, "settings")
    if tuple(settings["ratios"]) != RATIOS or settings["update_vertices"] is not False or settings["transfer"] != "direct-v1":
        raise ValueError("strategy settings are invalid")
    tools = _exact(root["tools"], {"blender", "studiomdl", "meshopt_bridge", "runner"}, "tools")
    for name, item in tools.items():
        item = _exact(item, {"version", "size_bytes", "sha256"}, f"tools.{name}")
        if type(item["version"]) is not str or not item["version"] or type(item["size_bytes"]) is not int or item["size_bytes"] <= 0:
            raise ValueError(f"tools.{name} metadata is invalid")
        _sha(item["sha256"], f"tools.{name}.sha256")
    sources = root["sources"]
    if type(sources) is not list or tuple(item.get("path") for item in sources if type(item) is dict) != SOURCES:
        raise ValueError("source set/order is invalid")
    for index, item in enumerate(sources):
        item = _exact(item, {"path", "size_bytes", "sha256"}, f"sources[{index}]")
        _path(item["path"], f"sources[{index}].path"); _sha(item["sha256"], f"sources[{index}].sha256")
        if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0: raise ValueError("source size is invalid")
    records = root["records"]
    if type(records) is not list or len(records) != len(RATIOS): raise ValueError("ratio records are incomplete")
    control_manifest = None
    for ri, (record, ratio) in enumerate(zip(records, RATIOS)):
        record = _exact(record, {"candidate_id", "ratio", "smd", "compiled", "candidate_total_bytes", "control_total_bytes", "delta_bytes"}, f"records[{ri}]")
        if record["ratio"] != ratio or record["candidate_id"] != f"meshopt-direct-r{int(ratio * 100):03d}": raise ValueError("ratio record identity is invalid")
        smd = record["smd"]
        if type(smd) is not list or tuple(item.get("source") for item in smd if type(item) is dict) != SOURCES: raise ValueError("SMD evidence set/order is invalid")
        for si, item in enumerate(smd):
            item = _exact(item, {"source", "source_sha256", "raw_path", "raw_sha256", "restored_path", "restored_sha256", "triangles_before", "triangles_after", "source_vertices", "wedge_vertices", "output_vertices", "locked_vertices", "locked_percentage", "achieved_ratio"}, f"records[{ri}].smd[{si}]")
            tag = f"{int(ratio * 100):03d}"
            stem = PurePosixPath(item["source"]).stem + "_opt.smd"
            if item["raw_path"] != f"candidate-r{tag}/maximum_direct_raw/{stem}" or item["restored_path"] != f"candidate-r{tag}/output/{stem}": raise ValueError("SMD stage path is not canonical")
            for field in ("source_sha256", "raw_sha256", "restored_sha256"): _sha(item[field], field)
            if item["source_sha256"] != sources[si]["sha256"]: raise ValueError("SMD source hash is not bound to source manifest")
            if item["raw_sha256"] == item["restored_sha256"]: raise ValueError("raw and restored SMD hashes must identify distinct stages")
            for field in ("triangles_before", "triangles_after", "source_vertices", "wedge_vertices", "output_vertices", "locked_vertices"):
                if type(item[field]) is not int or item[field] < 0: raise ValueError("SMD integer metric is invalid")
            for field in ("locked_percentage", "achieved_ratio"):
                if type(item[field]) not in (int, float) or not math.isfinite(item[field]) or not 0 <= item[field] <= 1: raise ValueError("SMD ratio metric is invalid")
            if item["output_vertices"] > item["wedge_vertices"] or item["triangles_after"] > item["triangles_before"]: raise ValueError("SMD monotonic metric is invalid")
        compiled = _exact(record["compiled"], {"candidate", "control"}, "compiled")
        for lane in ("candidate", "control"):
            if type(compiled[lane]) is not list or len(compiled[lane]) != len(KINDS): raise ValueError("compiled artifacts incomplete")
            for ai, kind in enumerate(KINDS):
                artifact = _artifact(compiled[lane][ai], kind, f"compiled.{lane}[{ai}]")
                tag = f"{int(ratio * 100):03d}"
                expected_path = f"{'candidate-r' + tag if lane == 'candidate' else 'control'}/models/diggercars/pontiac_transam3/wheel{kind}"
                if artifact["path"] != expected_path: raise ValueError("compiled artifact path is not canonical")
        current_control = tuple((x["path"], x["size_bytes"], x["sha256"]) for x in compiled["control"])
        if control_manifest is None: control_manifest = current_control
        elif current_control != control_manifest: raise ValueError("control artifacts drift across ratios")
        candidate_total = sum(x["size_bytes"] for x in compiled["candidate"])
        control_total = sum(x["size_bytes"] for x in compiled["control"])
        if (record["candidate_total_bytes"], record["control_total_bytes"], record["delta_bytes"]) != (candidate_total, control_total, candidate_total-control_total): raise ValueError("compiled byte accounting is invalid")
    decision = _exact(root["decision"], {"best_ratio", "best_candidate_bytes", "best_blender_bytes", "winner", "reason"}, "decision")
    if decision["best_ratio"] != 0.25 or decision["best_candidate_bytes"] != min(r["candidate_total_bytes"] for r in records) or decision["winner"] is not False or type(decision["reason"]) is not str:
        raise ValueError("decision is invalid")
    _sha(root["evidence_sha256"], "evidence_sha256")
    if root["evidence_sha256"] != canonical_digest(root): raise ValueError("evidence digest mismatch")
    return root
