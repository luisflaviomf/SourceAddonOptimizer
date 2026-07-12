from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maximum_optimizer.direct_evidence import seal_evidence
from maximum_optimizer.position_evidence import load_position_evidence

KINDS = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".phy")


def stat(path: Path, label: str) -> dict:
    return {"path": label, "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _base(record_kind: str, metrics: list[dict], wheel_records: list[dict], bridge_sha256: str) -> dict:
    return seal_evidence({
        "schema_version": 3,
        "record_kind": record_kind,
        "corpus_id": "lvs-models-v1",
        "strategy": "meshopt-direct-position-v1",
        "evidence_scope": "local_external_evidence",
        "quality_status": "unverified",
        "quality_claim": None,
        "checked_in_metrics": {"records": metrics},
        "local_external_evidence": {
            "available_in_checkout": False,
            "artifact_label": "local_external_evidence",
            "reason": "direct SMD and compiled binaries are local experiment artifacts and are not committed",
            "tooling_source_snapshot_available": False,
            "bridge_sha256": bridge_sha256,
            "wheel_records": wheel_records,
        },
        "blender_baseline": {
            "available": False,
            "total_bytes": 633089,
            "reason": "baseline artifact is not committed; byte count is a local prior observation",
        },
        "charger_probe": {
            "status": "local_rejected_before_candidate_generation",
            "metrics_available": False,
            "direct_smd_available": False,
            "compiled_available": False,
            "failure": "ambiguous hard-normal provenance exceeds the fixed 15-degree disambiguation ceiling",
            "last_imported_triangle": 2105,
        },
        "decision": {
            "winner": False,
            "pressure_set_run": False,
            "reason": "local wheel bytes exceed the prior Blender observation and the local Charger probe fails closed",
        },
    })


def _fake_artifact(path: str, size: int) -> dict:
    return {"path": path, "size_bytes": size, "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest()}


def build_unit_fixture() -> dict:
    metrics = []
    wheel = []
    for tag, ratio, after in (("025", 0.25, 25), ("040", 0.4, 40)):
        sources = [
            {"source": "wh.smd", "triangles_before": 20, "triangles_after": after // 5,
             "locked_vertices": 1, "wedge_vertices": 10, "output_vertices": 6},
            {"source": "wh1.smd", "triangles_before": 30, "triangles_after": after * 3 // 10,
             "locked_vertices": 0, "wedge_vertices": 15, "output_vertices": 9},
            {"source": "wh2.smd", "triangles_before": 50, "triangles_after": after - after // 5 - after * 3 // 10,
             "locked_vertices": 1, "wedge_vertices": 25, "output_vertices": 15},
        ]
        counts = {name: sum(item[name] for item in sources) for name in ("triangles_before", "triangles_after", "locked_vertices", "wedge_vertices", "output_vertices")}
        metrics.append({"requested_ratio": ratio, "achieved_ratio": counts["triangles_after"] / counts["triangles_before"], "counts": counts, "sources": sources})
        candidate_sizes = (100, 704, 100, 100, 100)
        candidate = [_fake_artifact(f"candidate-r{tag}/wheel{kind}", size) for kind, size in zip(KINDS, candidate_sizes)]
        wheel.append({
            "requested_ratio": ratio,
            "metrics_json": _fake_artifact(f"candidate-r{tag}/candidate_metrics.json", 100),
            "direct_smd": [_fake_artifact(f"candidate-r{tag}/{name}_opt.smd", 100) for name in ("wh", "wh1", "wh2")],
            "candidate": candidate,
            "control": [_fake_artifact(f"control-r{tag}/wheel{kind}", 200) for kind in KINDS],
            "compiled_vertices": 10,
            "compiled_bytes": sum(candidate_sizes),
        })
    payload = _base("hermetic_test_fixture", metrics, wheel, hashlib.sha256(b"fixture bridge").hexdigest())
    return load_position_evidence(payload)


def _metric_record(metrics: dict, ratio: float) -> dict:
    sources = []
    for item in metrics["files"]:
        obj = item["objects"][0]
        sources.append({
            "source": Path(item["source"]).name,
            "triangles_before": obj["triangles_before"],
            "triangles_after": obj["triangles_after"],
            "locked_vertices": obj["locked_vertices"],
            "wedge_vertices": obj["wedge_vertices"],
            "output_vertices": obj["output_vertices"],
        })
    counts = {name: sum(item[name] for item in sources) for name in ("triangles_before", "triangles_after", "locked_vertices", "wedge_vertices", "output_vertices")}
    return {"requested_ratio": ratio, "achieved_ratio": counts["triangles_after"] / counts["triangles_before"], "counts": counts, "sources": sources}


def _wheel_record(work: Path, control: Path, tag: str, ratio: float, run_name: str) -> tuple[dict, dict]:
    run = work / run_name
    compiled = work / f"{run_name}-compiled/models/diggercars/pontiac_transam3"
    metrics_path = run / "candidate_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    candidate = [stat(compiled / f"wheel{kind}", f"candidate-r{tag}/wheel{kind}") for kind in KINDS]
    external = {
        "requested_ratio": ratio,
        "metrics_json": stat(metrics_path, f"candidate-r{tag}/candidate_metrics.json"),
        "direct_smd": [stat(run / "output" / f"{name}_opt.smd", f"candidate-r{tag}/{name}_opt.smd") for name in ("wh", "wh1", "wh2")],
        "candidate": candidate,
        "control": [stat(control / f"wheel{kind}", f"control-r{tag}/wheel{kind}") for kind in KINDS],
        "compiled_vertices": (candidate[1]["size_bytes"] - 64) // 64,
        "compiled_bytes": sum(item["size_bytes"] for item in candidate),
    }
    return _metric_record(metrics, ratio), external


def build_local(external_root: Path) -> dict:
    work = external_root / "benchmark/task6_meshopt_direct_position_v1"
    control = external_root / "benchmark/task4_smoothing_fixed_v1/pontiac_transam_wheel_review_compiled/models/diggercars/pontiac_transam3"
    pairs = [
        _wheel_record(work, control, "025", 0.25, "prov-wheel-r025"),
        _wheel_record(work, control, "040", 0.4, "prov8-wheel-r040"),
    ]
    bridge = ROOT / "maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"
    payload = _base("local_experiment", [pair[0] for pair in pairs], [pair[1] for pair in pairs], hashlib.sha256(bridge.read_bytes()).hexdigest())
    return load_position_evidence(payload)


def main() -> None:
    raw_root = os.environ.get("LVS_TASK6_EVIDENCE_ROOT")
    if not raw_root:
        raise SystemExit("LVS_TASK6_EVIDENCE_ROOT is required for the explicit local integration rehash")
    external_root = Path(raw_root).resolve()
    if not external_root.is_dir():
        raise SystemExit(f"LVS_TASK6_EVIDENCE_ROOT is not a directory: {external_root}")
    payload = build_local(external_root)
    Path(__file__).with_name("meshopt_direct_position_v1.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
