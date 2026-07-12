from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maximum_optimizer.direct_evidence import canonical_value_digest, seal_evidence
from maximum_optimizer.position_evidence import load_position_evidence
from maximum_optimizer.qc_graph import parse_qc_graph

WORK = ROOT / ".superpowers/benchmark/task6_meshopt_direct_position_v1"
WHEEL_SOURCE = ROOT / ".superpowers/lvs-task2-control-complete/pontiac_transam_wheel/workspace/pontiac_transam_wheel/diggercars/pontiac_transam3/wheel"
CHARGER_SOURCE = ROOT / ".superpowers/lvs-task2-control-complete/dodge_charger/workspace/dodge_charger/diggercars/dodge_charger/charger"
CONTROL = ROOT / ".superpowers/benchmark/task4_smoothing_fixed_v1/pontiac_transam_wheel_review_compiled/models/diggercars/pontiac_transam3"
KINDS = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".phy")


def stat(path: Path, portable: str) -> dict:
    return {"path": portable, "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def source_manifest(root: Path, qc_name: str) -> list[dict]:
    graph = parse_qc_graph(root / qc_name, root)
    paths = {Path(qc_name)}
    paths.update(reference.source_path.relative_to(root) for reference in graph.references)
    return [stat(root / path, path.as_posix()) for path in sorted(paths, key=lambda value: value.as_posix().casefold())]


def wheel_record(tag: str, ratio: float, run_name: str) -> dict:
    run = WORK / run_name
    compiled = WORK / f"{run_name}-compiled/models/diggercars/pontiac_transam3"
    metrics_path = run / "candidate_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    direct = [stat(run / "output" / f"{stem}_opt.smd", f"candidate-r{tag}/direct/{stem}_opt.smd") for stem in ("wh", "wh1", "wh2")]
    candidate = [stat(compiled / f"wheel{kind}", f"candidate-r{tag}/models/diggercars/pontiac_transam3/wheel{kind}") for kind in KINDS]
    control = [stat(CONTROL / f"wheel{kind}", f"control/models/diggercars/pontiac_transam3/wheel{kind}") for kind in KINDS]
    objects = [obj for item in metrics["files"] for obj in item["objects"]]
    return {
        "requested_ratio": ratio,
        "metrics": stat(metrics_path, f"candidate-r{tag}/candidate_metrics.json"),
        "direct_smd": direct,
        "candidate": candidate,
        "control": control,
        "counts": {
            "triangles_before": metrics["triangles_before"], "triangles_after": metrics["triangles_after"],
            "locked_vertices": sum(item["locked_vertices"] for item in objects),
            "wedge_vertices": sum(item["wedge_vertices"] for item in objects),
            "output_vertices": sum(item["output_vertices"] for item in objects),
            "compiled_vertices": (next(item["size_bytes"] for item in candidate if item["path"].endswith(".vvd")) - 64) // 64,
            "compiled_bytes": sum(item["size_bytes"] for item in candidate),
        },
    }


def build() -> dict:
    evidence_root = ROOT / "benchmarks/lvs_models/evidence/meshopt-direct-position-v1"
    builds = [
        {
            "record_id": f"clean-build-{index}",
            "log": stat(evidence_root / f"build{index}.log", f"benchmarks/lvs_models/evidence/meshopt-direct-position-v1/build{index}.log"),
            "dll": stat(evidence_root / f"meshopt_bridge.build{index}.dll", f"benchmarks/lvs_models/evidence/meshopt-direct-position-v1/meshopt_bridge.build{index}.dll"),
        }
        for index in (1, 2)
    ]
    native_files = [ROOT / "maximum_optimizer/native/build.ps1", ROOT / "maximum_optimizer/native/CMakeLists.txt", ROOT / "maximum_optimizer/native/meshopt_bridge.cpp"]
    vendor_files = sorted(path for path in (ROOT / "third_party/meshoptimizer").rglob("*") if path.is_file())
    native = [stat(path, path.relative_to(ROOT).as_posix()) for path in native_files]
    vendor = [stat(path, path.relative_to(ROOT).as_posix()) for path in vendor_files]
    tools = {
        "bridge": stat(ROOT / "maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll", "maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"),
        "runner": stat(ROOT / "batch_optimize_maximum.py", "batch_optimize_maximum.py"),
        "serializer": stat(ROOT / "maximum_optimizer/smd_contract.py", "maximum_optimizer/smd_contract.py"),
        "mesh_attributes": stat(ROOT / "maximum_optimizer/mesh_attributes.py", "maximum_optimizer/mesh_attributes.py"),
        "meshoptimizer_version": (ROOT / "third_party/meshoptimizer/VERSION").read_text(encoding="utf-8").strip(),
        "bridge_abi": 3,
    }
    payload = seal_evidence({
        "schema_version": 2, "corpus_id": "lvs-models-v1", "strategy": "meshopt-direct-position-v1",
        "quality_status": "unverified", "quality_claim": None,
        "tools": tools,
        "build_attestation": {
            "records": builds, "outputs_equal": len({item["dll"]["sha256"] for item in builds}) == 1,
            "native_inputs": native, "native_digest": canonical_value_digest(native),
            "vendor_inputs": vendor, "vendor_digest": canonical_value_digest(vendor),
        },
        "sources": {"wheel": source_manifest(WHEEL_SOURCE, "wheel.qc"), "charger": source_manifest(CHARGER_SOURCE, "charger.qc")},
        "wheel_records": [wheel_record("025", 0.25, "prov-wheel-r025"), wheel_record("040", 0.40, "prov8-wheel-r040")],
        "blender_baseline": {"available": False, "total_bytes": 633089, "reason": "imported baseline artifacts are not present in this portable workspace; no hashes claimed"},
        "charger_probe": {
            "status": "rejected_before_candidate_generation", "metrics_available": False,
            "direct_smd_available": False, "compiled_available": False,
            "failure": "ambiguous hard-normal provenance exceeds the fixed 15-degree disambiguation ceiling",
            "last_imported_triangle": 2105,
        },
        "decision": {"winner": False, "pressure_set_run": False, "reason": "wheel loses to Blender baseline and hardened Charger provenance fails closed"},
    })
    load_position_evidence(payload)
    return payload


if __name__ == "__main__":
    Path(__file__).with_name("meshopt_direct_position_v1.json").write_text(
        json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
