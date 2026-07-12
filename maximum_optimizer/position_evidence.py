from __future__ import annotations

import math
from pathlib import PurePosixPath
import re

from .direct_evidence import canonical_digest, canonical_value_digest

_SHA = re.compile(r"[0-9a-f]{64}")
_KINDS = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".phy")


def _keys(value: object, expected: set[str], name: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} fields are invalid")
    return value


def _artifact(value: object, name: str) -> dict:
    item = _keys(value, {"path", "size_bytes", "sha256"}, name)
    path = item["path"]
    if type(path) is not str or not path or "\\" in path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise ValueError(f"{name} path is not portable")
    if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0 or type(item["sha256"]) is not str or not _SHA.fullmatch(item["sha256"]):
        raise ValueError(f"{name} size/hash is invalid")
    return item


def _manifest(value: object, name: str, *, minimum: int = 1) -> list[dict]:
    if type(value) is not list or len(value) < minimum:
        raise ValueError(f"{name} manifest is incomplete")
    result = [_artifact(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len({item["path"] for item in result}) != len(result):
        raise ValueError(f"{name} paths are duplicated")
    return result


def load_position_evidence(payload: object) -> dict:
    root = _keys(payload, {
        "schema_version", "corpus_id", "strategy", "quality_status", "quality_claim", "tools",
        "build_attestation", "sources", "wheel_records", "blender_baseline", "charger_probe",
        "decision", "evidence_sha256",
    }, "evidence")
    if root["schema_version"] != 2 or root["corpus_id"] != "lvs-models-v1" or root["strategy"] != "meshopt-direct-position-v1" or root["quality_status"] != "unverified" or root["quality_claim"] is not None:
        raise ValueError("evidence identity is invalid")
    if root["evidence_sha256"] != canonical_digest(root):
        raise ValueError("evidence digest is invalid")
    tools = _keys(root["tools"], {"bridge", "runner", "serializer", "mesh_attributes", "meshoptimizer_version", "bridge_abi"}, "tools")
    for name in ("bridge", "runner", "serializer", "mesh_attributes"):
        _artifact(tools[name], f"tools.{name}")
    if tools["bridge_abi"] != 3 or tools["meshoptimizer_version"] != "v1.2 9d9890c73011d75920af614485296d1e03e95448":
        raise ValueError("tool versions are invalid")
    build = _keys(root["build_attestation"], {"records", "outputs_equal", "native_inputs", "native_digest", "vendor_inputs", "vendor_digest"}, "build")
    if type(build["records"]) is not list or len(build["records"]) != 2 or build["outputs_equal"] is not True:
        raise ValueError("build records are incomplete")
    dll_hashes = set()
    record_ids = set()
    for index, raw in enumerate(build["records"]):
        record = _keys(raw, {"record_id", "log", "dll"}, f"build.records[{index}]")
        record_ids.add(record["record_id"])
        _artifact(record["log"], f"build.records[{index}].log")
        dll_hashes.add(_artifact(record["dll"], f"build.records[{index}].dll")["sha256"])
    if len(record_ids) != 2 or len(dll_hashes) != 1 or next(iter(dll_hashes)) != tools["bridge"]["sha256"]:
        raise ValueError("independent build outputs are invalid")
    native = _manifest(build["native_inputs"], "native_inputs", minimum=3)
    vendor = _manifest(build["vendor_inputs"], "vendor_inputs", minimum=20)
    if build["native_digest"] != canonical_value_digest(native) or build["vendor_digest"] != canonical_value_digest(vendor):
        raise ValueError("build input digest is invalid")
    sources = _keys(root["sources"], {"wheel", "charger"}, "sources")
    _manifest(sources["wheel"], "sources.wheel", minimum=6)
    _manifest(sources["charger"], "sources.charger", minimum=20)
    records = root["wheel_records"]
    if type(records) is not list or [item.get("requested_ratio") for item in records if type(item) is dict] != [0.25, 0.4]:
        raise ValueError("wheel ratio records are invalid")
    shared_control = None
    for index, raw in enumerate(records):
        record = _keys(raw, {"requested_ratio", "metrics", "direct_smd", "candidate", "control", "counts"}, f"wheel[{index}]")
        _artifact(record["metrics"], f"wheel[{index}].metrics")
        _manifest(record["direct_smd"], f"wheel[{index}].direct_smd", minimum=3)
        candidate = _manifest(record["candidate"], f"wheel[{index}].candidate", minimum=5)
        control = _manifest(record["control"], f"wheel[{index}].control", minimum=5)
        if any(not item["path"].endswith(kind) for item, kind in zip(candidate, _KINDS)) or any(not item["path"].endswith(kind) for item, kind in zip(control, _KINDS)):
            raise ValueError("compiled artifact ordering is invalid")
        control_signature = tuple((item["size_bytes"], item["sha256"]) for item in control)
        if shared_control is not None and control_signature != shared_control:
            raise ValueError("control artifacts drift across ratios")
        shared_control = control_signature
        counts = _keys(record["counts"], {"triangles_before", "triangles_after", "locked_vertices", "wedge_vertices", "output_vertices", "compiled_vertices", "compiled_bytes"}, f"wheel[{index}].counts")
        if any(type(counts[name]) is not int or counts[name] <= 0 for name in ("triangles_before", "triangles_after", "wedge_vertices", "output_vertices", "compiled_vertices", "compiled_bytes")) or type(counts["locked_vertices"]) is not int or counts["locked_vertices"] < 0:
            raise ValueError("wheel counts are invalid")
        if counts["triangles_after"] >= counts["triangles_before"] or counts["locked_vertices"] > counts["wedge_vertices"] or counts["compiled_bytes"] != sum(item["size_bytes"] for item in candidate):
            raise ValueError("wheel count/byte relationships are invalid")
        vvd = next(item for item in candidate if item["path"].endswith(".vvd"))
        if counts["compiled_vertices"] != (vvd["size_bytes"] - 64) // 64:
            raise ValueError("compiled vertex accounting is invalid")
    blender = _keys(root["blender_baseline"], {"available", "total_bytes", "reason"}, "blender")
    if blender["available"] is not False or blender["total_bytes"] != 633089 or not blender["reason"]:
        raise ValueError("unavailable Blender baseline is misrepresented")
    charger = _keys(root["charger_probe"], {"status", "metrics_available", "direct_smd_available", "compiled_available", "failure", "last_imported_triangle"}, "charger")
    if charger["status"] != "rejected_before_candidate_generation" or any(charger[name] is not False for name in ("metrics_available", "direct_smd_available", "compiled_available")) or type(charger["last_imported_triangle"]) is not int or charger["last_imported_triangle"] < 0 or not charger["failure"]:
        raise ValueError("Charger failure is misrepresented")
    decision = _keys(root["decision"], {"winner", "pressure_set_run", "reason"}, "decision")
    if decision["winner"] is not False or decision["pressure_set_run"] is not False or not decision["reason"]:
        raise ValueError("decision is invalid")
    return root
