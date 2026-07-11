from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Mapping, Sequence
from pathlib import PureWindowsPath

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from maximum_optimizer.meshopt_bridge import (
    LOCK,
    PRIORITY,
    PROTECT,
    SIMPLIFY_LOCK_BORDER,
    SIMPLIFY_PERMISSIVE,
    SIMPLIFY_REGULARIZE_LIGHT,
    MeshInput,
    SimplifyOptions,
    simplify_mesh,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXPECTED_CANDIDATE_FIELDS = {
    "candidate_id", "engine", "ratio", "target_error", "update_vertices", "region_overrides"
}


@dataclass(frozen=True)
class CandidateConfig:
    candidate_id: str
    engine: str
    ratio: float
    target_error: float
    update_vertices: bool
    region_overrides: Mapping[str, float]


@dataclass(frozen=True)
class Settings:
    root: Path
    candidate_json: Path
    meshopt_dll: Path
    candidate: CandidateConfig


@dataclass(frozen=True)
class GeometryClass:
    is_thin: bool
    extents: tuple[float, float, float]
    border_vertex_count: int


@dataclass(frozen=True)
class SimplificationPolicy:
    vertex_flags: tuple[int, ...]
    meshopt_options: int
    geometry: GeometryClass


@dataclass(frozen=True)
class SmdAudit:
    triangle_count: int
    materials: tuple[str, ...]
    bones: tuple[int, ...]


def _strict_number(name: str, value: object, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} is out of range")
    return result


def load_candidate_payload(payload: object) -> CandidateConfig:
    if type(payload) is not dict or set(payload) != _EXPECTED_CANDIDATE_FIELDS:
        raise ValueError("candidate JSON has missing or unknown fields")
    candidate_id = payload["candidate_id"]
    if type(candidate_id) is not str or not _ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate_id is unsafe")
    if payload["engine"] != "meshoptimizer":
        raise ValueError("engine must be meshoptimizer")
    ratio = _strict_number("ratio", payload["ratio"], minimum=0.000001, maximum=1.0)
    target_error = _strict_number("target_error", payload["target_error"], minimum=0.0, maximum=1.0)
    update_vertices = payload["update_vertices"]
    if type(update_vertices) is not bool:
        raise ValueError("update_vertices must be bool")
    overrides = payload["region_overrides"]
    if type(overrides) is not dict:
        raise ValueError("region_overrides must be an object")
    cleaned: dict[str, float] = {}
    for key, value in overrides.items():
        if type(key) is not str or not _ID_RE.fullmatch(key):
            raise ValueError("region override key is unsafe")
        cleaned[key] = _strict_number(f"region_overrides.{key}", value, minimum=0.000001, maximum=1.0)
    return CandidateConfig(
        candidate_id=candidate_id,
        engine="meshoptimizer",
        ratio=ratio,
        target_error=target_error,
        update_vertices=update_vertices,
        region_overrides=MappingProxyType(cleaned),
    )


def parse_args(argv: Sequence[str]) -> Settings:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--candidate-json", required=True)
    parser.add_argument("--meshopt-dll", required=True)
    parsed = parser.parse_args(argv)
    root = Path(parsed.root).expanduser().resolve()
    candidate_json = Path(parsed.candidate_json).expanduser().resolve()
    meshopt_dll = Path(parsed.meshopt_dll).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"root does not exist: {root}")
    try:
        payload = json.loads(candidate_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate JSON: {candidate_json}") from exc
    return Settings(root, candidate_json, meshopt_dll, load_candidate_payload(payload))


def _triangle_edges(triangle: Sequence[int]) -> tuple[tuple[int, int], ...]:
    a, b, c = triangle
    return tuple((min(x, y), max(x, y)) for x, y in ((a, b), (b, c), (c, a)))


def classify_geometry(
    vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]
) -> GeometryClass:
    if not vertices or not triangles:
        raise ValueError("geometry must not be empty")
    coords = tuple(tuple(float(value) for value in vertex) for vertex in vertices)
    if any(len(vertex) != 3 or not all(math.isfinite(value) for value in vertex) for vertex in coords):
        raise ValueError("vertices must be finite float3 values")
    if any(
        len(triangle) != 3
        or any(type(index) is not int or index < 0 or index >= len(coords) for index in triangle)
        or len(set(triangle)) != 3
        for triangle in triangles
    ):
        raise ValueError("triangles must contain three distinct in-range indices")
    extents = tuple(max(vertex[axis] for vertex in coords) - min(vertex[axis] for vertex in coords) for axis in range(3))
    longest = max(extents)
    ordered = sorted(extents)
    is_thin = longest > 0.0 and ordered[0] <= longest * 0.025
    edge_counts = Counter(edge for triangle in triangles for edge in _triangle_edges(triangle))
    border_vertices = {vertex for edge, count in edge_counts.items() if count == 1 for vertex in edge}
    return GeometryClass(is_thin, extents, len(border_vertices))


def derive_simplification_policy(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    *,
    material_ids: Sequence[int],
    skin_weights: Sequence[Sequence[float]] | None,
) -> SimplificationPolicy:
    if len(material_ids) != len(triangles):
        raise ValueError("material_ids must match triangles")
    geometry = classify_geometry(vertices, triangles)
    edge_triangles: dict[tuple[int, int], list[int]] = defaultdict(list)
    for triangle_index, triangle in enumerate(triangles):
        for edge in _triangle_edges(triangle):
            edge_triangles[edge].append(triangle_index)
    flags = [0] * len(vertices)
    for edge, owners in edge_triangles.items():
        if len(owners) == 1:
            for vertex in edge:
                flags[vertex] |= LOCK
        if len(owners) >= 2 and len({material_ids[index] for index in owners}) > 1:
            for vertex in edge:
                flags[vertex] |= PROTECT
    if geometry.is_thin:
        flags = [flag | PRIORITY for flag in flags]
    options = SIMPLIFY_LOCK_BORDER | SIMPLIFY_PERMISSIVE
    if skin_weights is not None:
        if len(skin_weights) != len(vertices):
            raise ValueError("skin weights must match vertices")
        options |= SIMPLIFY_REGULARIZE_LIGHT
    return SimplificationPolicy(tuple(flags), options, geometry)


def repair_weights(weights: Sequence[float]) -> tuple[float, float, float, float]:
    if len(weights) != 4 or not all(math.isfinite(float(value)) for value in weights):
        raise ValueError("weights must be finite float4")
    clamped = tuple(min(1.0, max(0.0, float(value))) for value in weights)
    total = sum(clamped)
    if total <= 1e-12:
        raise ValueError("zero-sum skin weights")
    return tuple(value / total for value in clamped)  # type: ignore[return-value]


_QC_REF_RE = re.compile(
    r'(?im)^(?P<prefix>\s*\$(?:body|model|collisionmodel|collisionjoints)\b(?:\s+\S+)?\s+)(?P<quote>["\'])(?P<path>[^"\']+)(?P=quote)'
)


def rewrite_qc_references(text: str, replacements: Mapping[str, str]) -> str:
    normalized = {key.replace("\\", "/").casefold(): value.replace("\\", "/") for key, value in replacements.items()}

    def replace(match: re.Match[str]) -> str:
        key = match.group("path").replace("\\", "/").casefold()
        replacement = normalized.get(key)
        if replacement is None:
            return match.group(0)
        quote = match.group("quote")
        return f'{match.group("prefix")}{quote}{replacement}{quote}'

    return _QC_REF_RE.sub(replace, text)


def audit_smd_text(text: str) -> SmdAudit:
    section = ""
    triangle_lines: list[str] = []
    materials: set[str] = set()
    bones: set[int] = set()
    for raw in text.splitlines():
        line = raw.strip()
        lower = line.casefold()
        if lower in {"nodes", "skeleton", "triangles"}:
            section = lower
            continue
        if lower == "end":
            section = ""
            continue
        if section == "nodes":
            match = re.match(r"^(-?\d+)\s+", line)
            if match:
                bones.add(int(match.group(1)))
        elif section == "triangles" and line:
            parts = line.split()
            if len(parts) >= 9 and parts[0].lstrip("-").isdigit():
                triangle_lines.append(line)
            else:
                materials.add(line)
    return SmdAudit(len(triangle_lines) // 3, tuple(sorted(materials)), tuple(sorted(bones)))


def resolve_contained_source(root: Path, qc_dir: Path, token: str) -> Path:
    normalized = token.replace("\\", "/")
    windows = PureWindowsPath(token)
    if not normalized or normalized.startswith("/") or windows.is_absolute() or windows.drive or ".." in Path(normalized).parts:
        raise ValueError(f"unsafe QC source reference: {token}")
    root_resolved = root.resolve(strict=True)
    source = (qc_dir / Path(normalized)).resolve(strict=True)
    try:
        source.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"QC source escapes root: {token}") from exc
    if not source.is_file():
        raise ValueError(f"QC source is not a file: {token}")
    return source


_GEOMETRY_REF_RE = re.compile(
    r'(?im)^\s*\$(?:body|model|collisionmodel|collisionjoints)\b(?:\s+\S+)?\s+["\'](?P<path>[^"\']+\.(?:smd|dmx))["\']'
)


def geometry_references(qc_text: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for match in _GEOMETRY_REF_RE.finditer(qc_text):
        token = match.group("path")
        key = token.replace("\\", "/").casefold()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return tuple(result)


def _clear_blender_scene() -> None:
    assert bpy is not None
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.armatures):
        for datablock in tuple(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _vertex_source_attributes(obj: object) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float], ...],
    tuple[tuple[tuple[int, float], ...], ...],
]:
    mesh = obj.data
    mesh.update()
    positions = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
    normals = tuple(tuple(float(value) for value in vertex.normal) for vertex in mesh.vertices)

    uv_sums = [[0.0, 0.0, 0] for _ in mesh.vertices]
    uv_layer = mesh.uv_layers.active
    if uv_layer is not None:
        for loop in mesh.loops:
            uv = uv_layer.data[loop.index].uv
            accumulator = uv_sums[loop.vertex_index]
            accumulator[0] += float(uv.x)
            accumulator[1] += float(uv.y)
            accumulator[2] += 1
    uvs = tuple(
        (values[0] / values[2], values[1] / values[2]) if values[2] else (0.0, 0.0)
        for values in uv_sums
    )

    group_weights = []
    for vertex in mesh.vertices:
        entries = sorted(
            ((int(group.group), float(group.weight)) for group in vertex.groups if group.weight > 0.0),
            key=lambda item: (-item[1], item[0]),
        )[:4]
        group_weights.append(tuple(entries))
    return positions, normals, uvs, tuple(group_weights)


def _bridge_weights(
    group_weights: Sequence[Sequence[tuple[int, float]]], *, skinned: bool
) -> tuple[tuple[float, float, float, float], ...]:
    result = []
    for entries in group_weights:
        raw = tuple(weight for _, weight in entries)
        if skinned:
            padded = raw + (0.0,) * (4 - len(raw))
            result.append(repair_weights(padded))
        else:
            result.append((1.0, 0.0, 0.0, 0.0))
    return tuple(result)


def _optimize_mesh_object(obj: object, candidate: CandidateConfig) -> dict[str, object]:
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.kdtree import KDTree

    mesh = obj.data
    mesh.calc_loop_triangles()
    positions, normals, uvs, group_weights = _vertex_source_attributes(obj)
    group_names = tuple(group.name for group in obj.vertex_groups)
    triangles = tuple(tuple(int(index) for index in triangle.vertices) for triangle in mesh.loop_triangles)
    material_ids = tuple(int(triangle.material_index) for triangle in mesh.loop_triangles)
    if not triangles:
        raise ValueError("imported mesh has no triangles")
    skinned = len(obj.vertex_groups) > 0
    weights = _bridge_weights(group_weights, skinned=skinned)
    policy = derive_simplification_policy(
        positions,
        triangles,
        material_ids=material_ids,
        skin_weights=weights if skinned else None,
    )
    ratio = candidate.region_overrides.get("thin", candidate.ratio) if policy.geometry.is_thin else candidate.ratio
    source = MeshInput(
        positions=positions,
        normals=normals,
        uvs=uvs,
        weights=weights,
        indices=tuple(index for triangle in triangles for index in triangle),
        material_ids=material_ids,
        vertex_flags=policy.vertex_flags,
    )
    result = simplify_mesh(
        source,
        SimplifyOptions(
            target_ratio=ratio,
            target_error=candidate.target_error,
            update_vertices=candidate.update_vertices,
            meshopt_options=policy.meshopt_options,
        ),
    )
    if len(result.indices) >= len(source.indices) and ratio < 0.999999:
        raise RuntimeError("meshoptimizer did not reduce this mesh")

    used_vertices = set(result.indices)
    projected_positions = [tuple(position) for position in result.positions]
    transferred_normals = [tuple(normal) for normal in result.normals]
    transferred_uvs = [tuple(uv) for uv in result.uvs]
    nearest_sources = list(range(len(positions)))
    bvh = BVHTree.FromPolygons(
        [Vector(position) for position in positions],
        [tuple(triangle) for triangle in triangles],
        all_triangles=True,
    )
    kd = KDTree(len(positions))
    for index, position in enumerate(positions):
        kd.insert(Vector(position), index)
    kd.balance()
    for index in used_vertices:
        nearest = bvh.find_nearest(Vector(result.positions[index]))
        if nearest is None or nearest[0] is None:
            raise RuntimeError("surface projection failed")
        projected = nearest[0]
        projected_positions[index] = tuple(float(value) for value in projected)
        _, source_index, _ = kd.find(projected)
        nearest_sources[index] = int(source_index)
        transferred_normals[index] = normals[source_index]
        transferred_uvs[index] = uvs[source_index]

    faces = tuple(tuple(result.indices[offset : offset + 3]) for offset in range(0, len(result.indices), 3))
    preserved_materials = tuple(mesh.materials)
    mesh.clear_geometry()
    mesh.from_pydata(projected_positions, (), faces)
    for material in preserved_materials:
        if material.name not in mesh.materials:
            mesh.materials.append(material)
    for polygon, material_id in zip(mesh.polygons, result.material_ids):
        polygon.material_index = min(int(material_id), max(0, len(mesh.materials) - 1))

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = transferred_uvs[loop.vertex_index]
    mesh.update()
    if hasattr(mesh, "normals_split_custom_set_from_vertices"):
        mesh.normals_split_custom_set_from_vertices(transferred_normals)

    if skinned:
        existing_names = {group.name for group in obj.vertex_groups}
        for group_name in group_names:
            if group_name not in existing_names:
                obj.vertex_groups.new(name=group_name)
        all_indices = list(range(len(mesh.vertices)))
        for group in obj.vertex_groups:
            group.remove(all_indices)
        for vertex_index in used_vertices:
            source_index = nearest_sources[vertex_index]
            entries = group_weights[source_index]
            repaired = repair_weights(tuple(weight for _, weight in entries) + (0.0,) * (4 - len(entries)))
            for (group_index, _), weight in zip(entries, repaired):
                obj.vertex_groups[group_index].add([vertex_index], weight, "REPLACE")

    return {
        "triangles_before": len(source.indices) // 3,
        "triangles_after": len(result.indices) // 3,
        "is_thin": policy.geometry.is_thin,
        "skinned": skinned,
        "result_error": result.result_error,
    }


def _process_source_file(
    source: Path, destination: Path, candidate: CandidateConfig
) -> dict[str, object]:
    assert bpy is not None
    import batch_optimize_qc as source_tools

    before_audit = audit_smd_text(source.read_text(encoding="utf-8", errors="replace"))
    _clear_blender_scene()
    source_tools.import_source_file(source)
    mesh_objects = tuple(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    if not mesh_objects:
        raise RuntimeError(f"Source Tools imported no mesh from {source}")
    object_metrics = [_optimize_mesh_object(obj, candidate) for obj in mesh_objects]
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_tools.export_source_file(destination, destination.suffix.lstrip("."))
    if not destination.is_file():
        raise RuntimeError(f"Source Tools did not export {destination}")
    after_audit = audit_smd_text(destination.read_text(encoding="utf-8", errors="replace"))
    if not set(before_audit.materials).issubset(after_audit.materials):
        raise RuntimeError("export lost SMD materials")
    if not set(before_audit.bones).issubset(after_audit.bones):
        raise RuntimeError("export lost SMD bones")
    if after_audit.triangle_count >= before_audit.triangle_count:
        raise RuntimeError("export did not reduce SMD triangle count")
    return {
        "source": source.as_posix(),
        "output": destination.as_posix(),
        "triangles_before": before_audit.triangle_count,
        "triangles_after": after_audit.triangle_count,
        "materials_before": list(before_audit.materials),
        "materials_after": list(after_audit.materials),
        "bones_before": list(before_audit.bones),
        "bones_after": list(after_audit.bones),
        "objects": object_metrics,
    }


def run_blender(settings: Settings) -> dict[str, object]:
    assert bpy is not None
    import os
    import batch_optimize_qc as source_tools

    if not settings.meshopt_dll.is_file():
        raise ValueError(f"meshopt DLL does not exist: {settings.meshopt_dll}")
    os.environ["MAXIMUM_MESHOPT_DLL"] = str(settings.meshopt_dll)
    source_tools.ensure_source_tools_enabled()

    qcs = tuple(
        path for path in sorted(settings.root.rglob("*.qc"), key=lambda item: item.as_posix().casefold())
        if not path.name.casefold().endswith("_opt.qc") and "output" not in {part.casefold() for part in path.parts}
    )
    if not qcs:
        raise ValueError(f"no source QC files found under {settings.root}")

    files: list[dict[str, object]] = []
    for qc in qcs:
        qc_text = qc.read_text(encoding="utf-8", errors="replace")
        replacements: dict[str, str] = {}
        for token in geometry_references(qc_text):
            source = resolve_contained_source(settings.root, qc.parent, token)
            destination = qc.parent / "output" / f"{source.stem}_opt{source.suffix.lower()}"
            files.append(_process_source_file(source, destination, settings.candidate))
            replacements[token] = destination.relative_to(qc.parent).as_posix()
        if not replacements:
            raise ValueError(f"QC has no geometry references: {qc}")
        opt_qc = qc.with_name(f"{qc.stem}_OPT.qc")
        opt_qc.write_text(rewrite_qc_references(qc_text, replacements), encoding="utf-8", newline="\n")

    before = sum(int(item["triangles_before"]) for item in files)
    after = sum(int(item["triangles_after"]) for item in files)
    metrics = {
        "schema_version": 1,
        "candidate_id": settings.candidate.candidate_id,
        "engine": "meshoptimizer",
        "engine_version": 10200,
        "triangles_before": before,
        "triangles_after": after,
        "triangle_reduction_ratio": (before - after) / before if before else 0.0,
        "attribute_repair_failures": 0,
        "files": files,
    }
    metrics_path = settings.root / "candidate_metrics.json"
    temporary = metrics_path.with_name(f".{metrics_path.name}.tmp")
    temporary.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(metrics_path)
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        if "--" not in sys.argv:
            raise SystemExit("Blender arguments must follow --")
        argv = sys.argv[sys.argv.index("--") + 1 :]
    settings = parse_args(argv)
    if bpy is None:
        raise RuntimeError("batch_optimize_maximum.py must run inside Blender")
    metrics = run_blender(settings)
    print(json.dumps({"event": "maximum_candidate_complete", **metrics}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
