from __future__ import annotations

import re


_SHA = re.compile(r"[0-9a-f]{64}")


def _keys(value: object, expected: set[str], name: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} fields are invalid")
    return value


def load_position_evidence(payload: object) -> dict:
    root = _keys(payload, {
        "schema_version", "corpus_id", "strategy", "quality_status", "quality_claim",
        "tools", "reproducible_build_sha256", "baselines", "records", "car_probe", "decision",
    }, "evidence")
    if (
        root["schema_version"] != 1 or root["corpus_id"] != "lvs-models-v1"
        or root["strategy"] != "meshopt-direct-position-v1"
        or root["quality_status"] != "unverified" or root["quality_claim"] is not None
    ):
        raise ValueError("evidence identity is invalid")
    tools = _keys(root["tools"], {
        "meshoptimizer", "bridge_abi", "bridge_sha256", "native_source_sha256", "runner_sha256",
        "direct_smd_serializer_sha256",
    }, "tools")
    if tools["bridge_abi"] != 3 or any(not _SHA.fullmatch(tools[name]) for name in ("bridge_sha256", "native_source_sha256", "runner_sha256", "direct_smd_serializer_sha256")):
        raise ValueError("tool identity is invalid")
    builds = root["reproducible_build_sha256"]
    if type(builds) is not list or len(builds) != 2 or len(set(builds)) != 1 or builds[0] != tools["bridge_sha256"]:
        raise ValueError("reproducible builds are invalid")
    baselines = _keys(root["baselines"], {"control_bytes", "meshopt_direct_v1_best_bytes", "blender_best_bytes"}, "baselines")
    if any(type(value) is not int or value <= 0 for value in baselines.values()):
        raise ValueError("baselines are invalid")
    records = root["records"]
    if type(records) is not list or [record.get("requested_ratio") for record in records if type(record) is dict] != [0.25, 0.4]:
        raise ValueError("ratio records are invalid")
    for index, raw in enumerate(records):
        record = _keys(raw, {
            "requested_ratio", "achieved_ratio", "triangles", "locks", "output_vertices",
            "compiled_vertices", "compiled_bytes", "metrics_sha256", "compiled_sha256",
        }, f"records[{index}]")
        if not (type(record["achieved_ratio"]) is float and 0 < record["achieved_ratio"] <= 1):
            raise ValueError("achieved ratio is invalid")
        for pair_name in ("triangles", "locks"):
            pair = record[pair_name]
            if type(pair) is not list or len(pair) != 2 or any(type(value) is not int or value <= 0 for value in pair):
                raise ValueError(f"{pair_name} is invalid")
        if record["triangles"][1] / record["triangles"][0] != record["achieved_ratio"]:
            raise ValueError("achieved ratio accounting is invalid")
        if any(type(record[name]) is not int or record[name] <= 0 for name in ("output_vertices", "compiled_vertices", "compiled_bytes")):
            raise ValueError("vertex/byte accounting is invalid")
        hashes = record["compiled_sha256"]
        expected = {"wheel.mdl", "wheel.vvd", "wheel.dx80.vtx", "wheel.dx90.vtx", "wheel.phy"}
        if type(hashes) is not dict or set(hashes) != expected or any(not _SHA.fullmatch(value) for value in hashes.values()):
            raise ValueError("compiled hashes are invalid")
        if not _SHA.fullmatch(record["metrics_sha256"]):
            raise ValueError("metrics hash is invalid")
    car = _keys(root["car_probe"], {
        "family", "requested_ratio", "body_achieved_ratio", "body_locks", "nonmanifold_edges",
        "triangles", "compiled_vertices", "compiled_bytes", "status", "reason",
    }, "car_probe")
    if car["status"] != "compiled_raw_structural_pass" or not car["reason"]:
        raise ValueError("car rejection is invalid")
    decision = _keys(root["decision"], {"winner", "pressure_set_run", "reason"}, "decision")
    if decision["winner"] is not False or decision["pressure_set_run"] is not False or not decision["reason"]:
        raise ValueError("decision is invalid")
    return root
