"""Blender-side immutable LVS smoothing experiment (strategy smoothing-fixed-v1)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import batch_optimize_maximum as maximum
import batch_optimize_qc as source_tools
from maximum_optimizer.qc_graph import parse_qc_graph


STRATEGY = "smoothing-fixed-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _position_normal_sets(text: str) -> dict[tuple[float, float, float], set[tuple[float, float, float]]]:
    result: dict[tuple[float, float, float], set[tuple[float, float, float]]] = {}
    in_triangles = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.casefold() == "triangles":
            in_triangles = True
            continue
        if in_triangles and line.casefold() == "end":
            break
        parts = line.split()
        if in_triangles and len(parts) >= 9 and parts[0].lstrip("-").isdigit():
            position = tuple(round(float(parts[index]), 6) for index in (1, 2, 3))
            normal = tuple(round(float(parts[index]), 6) for index in (4, 5, 6))
            result.setdefault(position, set()).add(normal)  # type: ignore[arg-type]
    return result


def _rebuild_source(root: Path, source: Path) -> dict[str, object]:
    original_text = source.read_text(encoding="utf-8", errors="strict")
    before = maximum.audit_smd_text(original_text)
    before_hash = _sha256(source)
    maximum._clear_blender_scene()
    source_tools.import_source_file(source)
    objects = tuple(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    if len(objects) != 1:
        raise RuntimeError(f"smoothing-fixed-v1 requires one imported mesh from {source}, got {len(objects)}")
    object_metrics = [maximum.rebuild_mesh_smoothing_only(objects[0])]
    staging_dir = Path(tempfile.mkdtemp(prefix=".smoothing-fixed-v1-", dir=source.parent))
    staging = staging_dir / source.name
    try:
        source_tools.export_source_file(staging, "smd")
        restored = maximum.restore_smd_bone_identity(
            original_text, staging.read_text(encoding="utf-8", errors="strict")
        )
        restored = maximum.restore_smd_normal_identity(original_text, restored)
        staging.write_text(restored, encoding="utf-8", newline="")
        after = maximum.audit_smd_text(restored)
        before_sets = _position_normal_sets(original_text)
        after_sets = _position_normal_sets(restored)
        amplified = [
            {"position": position, "before": sorted(before_sets.get(position, set())), "after": sorted(values)}
            for position, values in after_sets.items()
            if len(values) > len(before_sets.get(position, set()))
        ]
        print("SMOOTHING_FIXED_V1_FILE " + json.dumps({
            "source": source.relative_to(root).as_posix(),
            "position_normal_keys_before": before.position_normal_keys,
            "position_normal_keys_after": after.position_normal_keys,
            "hard_normal_positions_before": before.hard_normal_positions,
            "hard_normal_positions_after": after.hard_normal_positions,
            "objects": object_metrics,
            "amplified_examples": amplified[:5],
        }, sort_keys=True))
        maximum.validate_smd_audits(before, after)
        if after.triangle_count != before.triangle_count:
            raise RuntimeError("smoothing-fixed-v1 changed triangle topology")
        os.replace(staging, source)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return {
        "source": source.relative_to(root).as_posix(),
        "source_sha256": before_hash,
        "output_sha256": _sha256(source),
        "triangles_before": before.triangle_count,
        "triangles_after": after.triangle_count,
        "position_normal_keys_before": before.position_normal_keys,
        "position_normal_keys_after": after.position_normal_keys,
        "hard_normal_positions_before": before.hard_normal_positions,
        "hard_normal_positions_after": after.hard_normal_positions,
        "uv_seam_positions_before": before.uv_seam_positions,
        "uv_seam_positions_after": after.uv_seam_positions,
        "objects": object_metrics,
    }


def run(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    source_tools.ensure_source_tools_enabled()
    qcs = tuple(sorted(root.rglob("*.qc"), key=lambda path: path.as_posix().casefold()))
    if len(qcs) != 1:
        raise ValueError(f"smoothing-fixed-v1 requires exactly one family QC, found {len(qcs)}")
    graph = parse_qc_graph(qcs[0], root)
    visual_sources = tuple(dict.fromkeys(
        reference.source_path for reference in graph.references if reference.role == "visual"
    ))
    if not visual_sources:
        raise ValueError("family QC has no visual SMD sources")
    if any(source.suffix.casefold() != ".smd" for source in visual_sources):
        raise ValueError("smoothing-fixed-v1 supports SMD visual sources only")
    before_nonvisual = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path not in visual_sources
    }
    files = [_rebuild_source(root, source) for source in visual_sources]
    after_nonvisual = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path not in visual_sources
    }
    if before_nonvisual != after_nonvisual:
        raise RuntimeError("smoothing-fixed-v1 mutated QC, animation, collision or other nonvisual input")
    return {
        "schema_version": 1,
        "strategy": STRATEGY,
        "quality_status": "unverified",
        "quality_claim": None,
        "blender_version": list(bpy.app.version),
        "family_root": root.name,
        "files": files,
        "nonvisual_files_preserved": True,
        "triangles_before": sum(int(item["triangles_before"]) for item in files),
        "triangles_after": sum(int(item["triangles_after"]) for item in files),
        "position_normal_keys_before": sum(int(item["position_normal_keys_before"]) for item in files),
        "position_normal_keys_after": sum(int(item["position_normal_keys_after"]) for item in files),
    }


if __name__ == "__main__":
    if "--" not in sys.argv:
        raise SystemExit("family root must follow --")
    family_root = Path(sys.argv[sys.argv.index("--") + 1])
    result = run(family_root)
    output = family_root / "smoothing_fixed_v1_metrics.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("SMOOTHING_FIXED_V1 " + json.dumps(result, sort_keys=True))
