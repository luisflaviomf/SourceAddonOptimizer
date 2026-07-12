from __future__ import annotations

import math
from pathlib import PurePosixPath
import re

from .direct_evidence import canonical_digest

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
        raise ValueError(f"{name} path is not relative")
    if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0 or type(item["sha256"]) is not str or not _SHA.fullmatch(item["sha256"]):
        raise ValueError(f"{name} size/hash is invalid")
    return item


def _manifest(value: object, name: str, *, count: int) -> list[dict]:
    if type(value) is not list or len(value) != count:
        raise ValueError(f"{name} manifest is incomplete")
    result = [_artifact(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len({item["path"] for item in result}) != len(result):
        raise ValueError(f"{name} paths are duplicated")
    return result


def load_position_evidence(payload: object) -> dict:
    root = _keys(payload, {
        "schema_version", "record_kind", "corpus_id", "strategy", "evidence_scope",
        "quality_status", "quality_claim", "checked_in_metrics", "local_external_evidence",
        "blender_baseline", "charger_probe", "decision", "evidence_sha256",
    }, "evidence")
    if (
        root["schema_version"] != 3
        or root["record_kind"] not in {"hermetic_test_fixture", "local_experiment"}
        or root["corpus_id"] != "lvs-models-v1"
        or root["strategy"] != "meshopt-direct-position-v1"
        or root["evidence_scope"] != "local_external_evidence"
        or root["quality_status"] != "unverified"
        or root["quality_claim"] is not None
    ):
        raise ValueError("evidence identity is invalid")
    if root["evidence_sha256"] != canonical_digest(root):
        raise ValueError("evidence digest is invalid")

    metrics = _keys(root["checked_in_metrics"], {"records"}, "checked_in_metrics")
    records = metrics["records"]
    if type(records) is not list or [item.get("requested_ratio") for item in records if type(item) is dict] != [0.25, 0.4]:
        raise ValueError("metric ratio records are invalid")
    for index, raw in enumerate(records):
        record = _keys(raw, {"requested_ratio", "achieved_ratio", "counts", "sources"}, f"metrics[{index}]")
        counts = _keys(record["counts"], {"triangles_before", "triangles_after", "locked_vertices", "wedge_vertices", "output_vertices"}, f"metrics[{index}].counts")
        if any(type(counts[name]) is not int or counts[name] <= 0 for name in ("triangles_before", "triangles_after", "wedge_vertices", "output_vertices")) or type(counts["locked_vertices"]) is not int or counts["locked_vertices"] < 0:
            raise ValueError("metric counts are invalid")
        if counts["triangles_after"] >= counts["triangles_before"] or counts["locked_vertices"] > counts["wedge_vertices"]:
            raise ValueError("metric count relationships are invalid")
        if type(record["achieved_ratio"]) is not float or not math.isclose(record["achieved_ratio"], counts["triangles_after"] / counts["triangles_before"], rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("achieved ratio is invalid")
        sources = record["sources"]
        if type(sources) is not list or not sources:
            raise ValueError("source metrics are missing")
        totals = {name: 0 for name in counts}
        names = set()
        for source_index, raw_source in enumerate(sources):
            source = _keys(raw_source, {"source", *counts.keys()}, f"metrics[{index}].sources[{source_index}]")
            if type(source["source"]) is not str or not source["source"].endswith(".smd") or source["source"] in names:
                raise ValueError("source metric identity is invalid")
            names.add(source["source"])
            for name in counts:
                if type(source[name]) is not int or source[name] < 0:
                    raise ValueError("source metric count is invalid")
                totals[name] += source[name]
        if totals != counts:
            raise ValueError("aggregate metrics do not match checked-in source metrics")

    external = _keys(root["local_external_evidence"], {
        "available_in_checkout", "artifact_label", "reason", "tooling_source_snapshot_available",
        "bridge_sha256", "wheel_records",
    }, "local_external_evidence")
    if external["available_in_checkout"] is not False or external["artifact_label"] != "local_external_evidence" or external["tooling_source_snapshot_available"] is not False or not external["reason"] or type(external["bridge_sha256"]) is not str or not _SHA.fullmatch(external["bridge_sha256"]):
        raise ValueError("external evidence disclosure is invalid")
    wheel = external["wheel_records"]
    if type(wheel) is not list or [item.get("requested_ratio") for item in wheel if type(item) is dict] != [0.25, 0.4]:
        raise ValueError("external wheel records are invalid")
    for index, raw in enumerate(wheel):
        record = _keys(raw, {"requested_ratio", "metrics_json", "direct_smd", "candidate", "control", "compiled_vertices", "compiled_bytes"}, f"external.wheel[{index}]")
        _artifact(record["metrics_json"], f"external.wheel[{index}].metrics_json")
        _manifest(record["direct_smd"], f"external.wheel[{index}].direct_smd", count=3)
        candidate = _manifest(record["candidate"], f"external.wheel[{index}].candidate", count=5)
        _manifest(record["control"], f"external.wheel[{index}].control", count=5)
        if any(not item["path"].endswith(kind) for item, kind in zip(candidate, _KINDS)):
            raise ValueError("compiled artifact ordering is invalid")
        if type(record["compiled_vertices"]) is not int or record["compiled_vertices"] <= 0 or type(record["compiled_bytes"]) is not int or record["compiled_bytes"] != sum(item["size_bytes"] for item in candidate):
            raise ValueError("external compiled accounting is invalid")
        vvd = candidate[1]
        if record["compiled_vertices"] != (vvd["size_bytes"] - 64) // 64:
            raise ValueError("external compiled vertex accounting is invalid")

    blender = _keys(root["blender_baseline"], {"available", "total_bytes", "reason"}, "blender")
    if blender["available"] is not False or blender["total_bytes"] != 633089 or not blender["reason"]:
        raise ValueError("Blender baseline is misrepresented")
    charger = _keys(root["charger_probe"], {"status", "metrics_available", "direct_smd_available", "compiled_available", "failure", "last_imported_triangle"}, "charger")
    if charger["status"] != "local_rejected_before_candidate_generation" or any(charger[name] is not False for name in ("metrics_available", "direct_smd_available", "compiled_available")) or type(charger["last_imported_triangle"]) is not int or charger["last_imported_triangle"] < 0 or not charger["failure"]:
        raise ValueError("Charger observation is misrepresented")
    decision = _keys(root["decision"], {"winner", "pressure_set_run", "reason"}, "decision")
    if decision["winner"] is not False or decision["pressure_set_run"] is not False or not decision["reason"]:
        raise ValueError("decision is invalid")
    return root
