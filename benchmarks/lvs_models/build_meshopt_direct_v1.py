from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from maximum_optimizer.direct_evidence import KINDS, RATIOS, SOURCES, canonical_value_digest, seal_evidence, load_meshopt_direct_evidence
from maximum_optimizer.qc_graph import parse_qc_graph

ROOT = REPO
WORK = ROOT / ".superpowers/benchmark/task5_meshopt_direct_v1"
SOURCE = ROOT / ".superpowers/lvs-task2-control-complete/pontiac_transam_wheel/workspace/pontiac_transam_wheel/diggercars/pontiac_transam3/wheel"
CONTROL = ROOT / ".superpowers/benchmark/task4_smoothing_fixed_v1/pontiac_transam_wheel_review_compiled/models/diggercars/pontiac_transam3"
MODEL_REL = Path("models/diggercars/pontiac_transam3")


def stat(path: Path, relative: str, kind: str | None = None) -> dict:
    value = {"path": relative, "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if kind is not None: value["kind"] = kind
    return value


def build() -> dict:
    # Task 5 is an immutable historical experiment. Later bridge/runner strategies must not
    # silently rewrite its tool identity; the attested build copies remain the executable proof.
    tool_paths = {
        "blender": (Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"), "5.0.1"),
        "studiomdl": (Path(r"D:\SteamLibrary\steamapps\common\GarrysMod\bin\studiomdl.exe"), "2026.04.29"),
        "meshopt_bridge": (ROOT / "benchmarks/lvs_models/evidence/meshopt-direct-v1/meshopt_bridge.build1.dll", "meshoptimizer-v1.2-abi2"),
    }
    tools = {name: {"version": version, "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, (path, version) in tool_paths.items()}
    tools["runner"] = {"version": "meshopt-direct-v1", "size_bytes": 61660, "sha256": "85ac15e4a768b0e91fc1cf48acbb308543bb924beacaa681e73e112b419c79bd"}
    sources = [stat(SOURCE / name, name) for name in SOURCES]
    source_hashes = {item["path"]: item["sha256"] for item in sources}
    control = [stat(CONTROL / ("wheel" + kind), f"control/{MODEL_REL.as_posix()}/wheel{kind}", kind) for kind in KINDS]
    graph = parse_qc_graph(SOURCE / "wheel.qc", SOURCE)
    declared = {reference.source_path.relative_to(SOURCE).as_posix() for reference in graph.references if reference.role in {"animation", "collision"}}
    nonvisual_paths = ("wheel.qc", *sorted(declared),)
    expected_nonvisual = ("wheel.qc", "wheel_anims/idle.smd", "wheel_anims/neutral.smd", "wheel_physics.smd")
    if nonvisual_paths != expected_nonvisual: raise ValueError("QC graph nonvisual declaration drift")
    nonvisual_artifacts = [stat(SOURCE / name, name) for name in nonvisual_paths]
    nonvisual_digest = canonical_value_digest(nonvisual_artifacts)
    vendor_manifest = [{"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted((ROOT / "third_party/meshoptimizer").rglob("*")) if path.is_file()]
    native_source_digest = "7bc894d00ee4d0a07be9c96c909720924b50940a57bdac8ca2767a0a77fa895f"
    vendor_digest = canonical_value_digest(vendor_manifest)
    evidence_root = ROOT / "benchmarks/lvs_models/evidence/meshopt-direct-v1"
    build_records = []
    timestamps = ("2026-07-12T07:33:50Z", "2026-07-12T07:34:05Z")
    for index in (1, 2):
        log = evidence_root / f"build{index}.log"; dll = evidence_root / f"meshopt_bridge.build{index}.dll"
        build_records.append({"record_id": f"clean-build-{index}", "invocation_timestamp_utc": timestamps[index-1], "invocation_nonce": f"task5-final-clean-build-{index}", "log": stat(log, f"benchmarks/lvs_models/evidence/meshopt-direct-v1/build{index}.log", ".log"), "dll": stat(dll, f"benchmarks/lvs_models/evidence/meshopt-direct-v1/meshopt_bridge.build{index}.dll", ".dll")})
    records = []
    for ratio in RATIOS:
        tag = f"{int(ratio * 100):03d}"
        run = WORK / f"final-r{tag}"
        compiled_root = WORK / f"final-r{tag}-compiled" / MODEL_REL
        metrics = json.loads((run / "candidate_metrics.json").read_text(encoding="utf-8"))
        for artifact in nonvisual_artifacts:
            work = run / artifact["path"]
            if stat(work, artifact["path"])["sha256"] != artifact["sha256"]: raise ValueError(f"nonvisual work copy drift for r{tag}/{artifact['path']}")
        preserved = {Path(item["output"]).as_posix(): item["output_sha256"] for item in metrics["provenance"] if item["role"] in {"animation", "collision"}}
        for artifact in nonvisual_artifacts[1:]:
            if preserved.get(artifact["path"]) != artifact["sha256"]: raise ValueError(f"optimizer provenance drift for r{tag}/{artifact['path']}")
        by_source = {Path(item["source"]).name: item for item in metrics["files"]}
        smd = []
        for source_name in SOURCES:
            item = by_source[source_name]
            obj = item["objects"][0]
            raw_file = run / item["raw_export_path"]
            restored_file = Path(item["output"])
            raw_hash = hashlib.sha256(raw_file.read_bytes()).hexdigest()
            restored_hash = hashlib.sha256(restored_file.read_bytes()).hexdigest()
            if raw_hash != item["raw_export_sha256"] or restored_hash != item["restored_export_sha256"]:
                raise ValueError(f"independent SMD stage hash mismatch for r{tag}/{source_name}")
            smd.append({
                "source": source_name, "source_sha256": source_hashes[source_name],
                "raw_path": f"candidate-r{tag}/{item['raw_export_path']}", "raw_sha256": raw_hash,
                "restored_path": f"candidate-r{tag}/output/{restored_file.name}", "restored_sha256": restored_hash,
                "triangles_before": item["triangles_before"], "triangles_after": item["triangles_after"],
                "source_vertices": obj["source_vertices"], "wedge_vertices": obj["wedge_vertices"],
                "output_vertices": obj["output_vertices"], "locked_vertices": obj["locked_vertices"],
                "locked_percentage": obj["locked_percentage"], "achieved_ratio": obj["achieved_ratio"],
            })
        candidate = [stat(compiled_root / ("wheel" + kind), f"candidate-r{tag}/{MODEL_REL.as_posix()}/wheel{kind}", kind) for kind in KINDS]
        candidate_total, control_total = sum(x["size_bytes"] for x in candidate), sum(x["size_bytes"] for x in control)
        records.append({
            "candidate_id": f"meshopt-direct-r{tag}", "ratio": ratio, "nonvisual_digest": nonvisual_digest, "smd": smd,
            "compiled": {"candidate": candidate, "control": control},
            "candidate_total_bytes": candidate_total, "control_total_bytes": control_total,
            "delta_bytes": candidate_total - control_total,
        })
    payload = seal_evidence({
        "schema_version": 2, "corpus_id": "lvs-models-v1", "strategy": "meshopt-direct-v1",
        "quality_status": "unverified", "quality_claim": None,
        "settings": {"ratios": list(RATIOS), "update_vertices": False, "transfer": "direct-v1"},
        "tools": tools,
        "build_attestation": {"inputs": {"command": "powershell -File maximum_optimizer/native/build.ps1", "generator": "Visual Studio 17 2022", "configuration": "Release|/Brepro", "architecture": "x64", "native_source_sha256": native_source_digest, "vendor_sha256": vendor_digest}, "records": build_records, "outputs_equal": len({record["dll"]["sha256"] for record in build_records}) == 1},
        "sources": sources, "nonvisual": {"artifacts": nonvisual_artifacts, "digest": nonvisual_digest}, "records": records,
        "decision": {"best_ratio": 0.25, "best_candidate_bytes": min(x["candidate_total_bytes"] for x in records), "best_blender_bytes": 633089, "winner": False, "reason": "direct output improves control but remains larger than the approved Blender wheel and saturates under existing locks"},
    })
    load_meshopt_direct_evidence(payload)
    return payload


if __name__ == "__main__":
    output = Path(__file__).with_name("meshopt_direct_v1.json")
    output.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
