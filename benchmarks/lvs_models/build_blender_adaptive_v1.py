from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from maximum_optimizer.compiled_size import read_vvd_lod_vertices
from maximum_optimizer.compiler_aware import compiler_proxy_bytes
from maximum_optimizer.compiler_aware_evidence import parse_compiler_aware_evidence
from maximum_optimizer.qc_inventory import parse_qc_fingerprint


OUTPUT = Path(__file__).with_name("blender_adaptive_v1.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(root: Path, stem: str) -> list[dict]:
    paths = sorted(root.glob(stem + ".*"), key=lambda path: path.name)
    return [
        {"path": path.name, "size_bytes": path.stat().st_size, "sha256": digest(path)}
        for path in paths if path.suffix.casefold() in {".mdl", ".vvd", ".vtx", ".phy"}
    ]


def tool(path: Path, version: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing tool: {path}")
    return {"version": version, "sha256": digest(path)}


def record(
    family_id: str, run: Path, baseline_root: Path, compiled_dir: str, stem: str,
) -> dict:
    metrics = json.loads((run / "candidate_metrics.json").read_text(encoding="utf-8"))
    candidate_root = run / "compiled" / "models" / compiled_dir
    baseline_family = baseline_root / compiled_dir
    candidate = artifacts(candidate_root, stem)
    baseline = artifacts(baseline_family, stem)
    vvd = candidate_root / f"{stem}.vvd"
    compiled_vertices = sum(read_vvd_lod_vertices(vvd))
    after = int(metrics["triangles_after"])
    predicted = compiler_proxy_bytes(compiled_vertices, after)
    payload = sum(
        item["size_bytes"] for item in candidate
        if item["path"].endswith((".vvd", ".dx80.vtx", ".dx90.vtx"))
    )
    fallbacks = []
    for item in metrics["files"]:
        if not item.get("fallback_reason"):
            continue
        source = Path(item["source"])
        output = Path(item["output"])
        fallbacks.append({
            "source": source.relative_to(run).as_posix(),
            "reason": item["fallback_reason"],
            "source_sha256": digest(source),
            "output_sha256": digest(output),
            "size_bytes": source.stat().st_size,
        })
    source_qc = next(path for path in run.glob("*.qc") if not path.stem.casefold().endswith("_opt"))
    optimized_qc = next(run.glob("*_OPT.qc"))
    source_fp = parse_qc_fingerprint(source_qc)
    optimized_fp = parse_qc_fingerprint(optimized_qc)
    differing = [
        name for name in source_fp.__dataclass_fields__
        if getattr(source_fp, name) != getattr(optimized_fp, name)
    ]
    return {
        "family_id": family_id,
        "ratio": 0.4,
        "candidate_metrics_sha256": digest(run / "candidate_metrics.json"),
        "candidate_payload_sha256": digest(run / "candidate.json"),
        "compile_log_sha256": digest(run / "compile-current.log"),
        "baseline": {"total_bytes": sum(x["size_bytes"] for x in baseline), "artifacts": baseline},
        "candidate": {"total_bytes": sum(x["size_bytes"] for x in candidate), "artifacts": candidate},
        "triangles": {"before": int(metrics["triangles_before"]), "after": after},
        "proxy": {
            "compiled_vertices": compiled_vertices,
            "triangles": after,
            "predicted_bytes": predicted,
            "actual_payload_bytes": payload,
            "absolute_error_bytes": abs(payload - predicted),
        },
        "fallbacks": fallbacks,
        "qc": {"status": "pass" if differing == ["mesh_files"] else "fail", "differing_fields": differing},
    }


def main() -> int:
    runs = Path(os.environ["LVS_TASK7_EVIDENCE_ROOT"]).resolve()
    baseline = Path(os.environ["LVS_BLENDER_MODELS_ROOT"]).resolve()
    blender = Path(os.environ["BLENDER_EXE"]).resolve()
    studiomdl = Path(os.environ["STUDIOMDL_EXE"]).resolve()
    payload = {
        "schema_version": 1,
        "strategy": "blender-adaptive-v1",
        "toolchain": {
            "blender": tool(blender, "5.0.1"),
            "studiomdl": tool(studiomdl, "Jun 29 2026 Garry's Mod Edition"),
        },
        "implementation": {"scripts": {
            "batch_optimize_maximum.py": digest(REPO / "batch_optimize_maximum.py"),
            "maximum_optimizer/compiler_aware.py": digest(REPO / "maximum_optimizer/compiler_aware.py"),
            "maximum_optimizer/candidates.py": digest(REPO / "maximum_optimizer/candidates.py"),
        }},
        "scoring": {
            "selector": "actual-studiomdl-compiled-bytes",
            "proxy_role": "diagnostic-only-confirmed-by-snapshot",
        },
        "records": [
            record("pontiac_transam_wheel", runs / "wheel-r040", baseline,
                   "diggercars/pontiac_transam3", "wheel"),
            record("dodge_charger", runs / "charger-auto-r040", baseline,
                   "diggercars/dodge_charger", "charger"),
        ],
        "quality": {
            "status": "unverified",
            "texture_status": "resolved-wheel-only",
            "render_status": "wheel-raw-ranking-only-charger-failed-closed",
            "reason": "wheel QC-aware manifest has no missing textures but bodygroups occlude internal detail; Charger render attempt produced no accepted manifest because geometry metrics fail closed on a preserved degenerate source; thresholds and runtime are not calibrated",
        },
        "decision": {
            "winner": False,
            "reason": "both compiled checkpoints beat b050, but only one of five pressure families ran and visual/runtime acceptance is incomplete",
            "pressure_families_run": 1,
            "required_pressure_families": 5,
        },
    }
    canonical = (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    payload["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    parse_compiler_aware_evidence(payload)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
