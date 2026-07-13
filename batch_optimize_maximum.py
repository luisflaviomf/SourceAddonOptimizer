from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import hashlib
import shutil
import stat
from typing import Sequence

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
    compact_direct_result,
    simplify_mesh,
)
from maximum_optimizer.compiler_aware import (
    SmdAuditValidationError, allows_exact_fallback, allows_strategy_exact_fallback,
    engine_evidence, exact_source_payload,
    move_modifier_first, preserve_whole_source, provenance_status, require_triangular_mesh,
)
from maximum_optimizer.importance_map import MeshImportanceInput, build_importance_weights
from maximum_optimizer.round_planar_priority import (
    _canonicalize_positions,
    classify_round_component,
)
from maximum_optimizer.regions import (
    RegionManifest,
    build_region_manifest,
    is_region_key,
    manifest_for_source,
    normalized_source_identity,
    resolve_region_assignments,
    source_material_slot_identities,
)
from maximum_optimizer.qc_graph import QcGraph, parse_qc_graph, rewritten_qc_graph_texts
from maximum_optimizer.mesh_attributes import (
    barycentric_weights,
    build_wedge_mesh,
    classify_position_topology,
    interpolate_influences,
    interpolate_vector,
    normalize_influences,
    recombine_full_attribute_vertices,
)
from maximum_optimizer.smoothing import (
    apply_reconstructed_smoothing,
    canonicalize_export_normals,
    canonicalize_normals_by_identity,
)
from maximum_optimizer.smd_contract import (
    POSITION_SERIALIZATION_TOLERANCE,
    DirectDegeneratePrefilter,
    map_imported_corners_to_smd,
    prefilter_direct_degenerate_smd,
    restore_direct_smd_normals,
    restore_ordered_smd_normals,
    serialize_direct_smd,
)


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXPECTED_CANDIDATE_FIELDS = {
    "candidate_id", "engine", "ratio", "target_error", "update_vertices", "region_overrides",
    "strategy", "transfer",
}
_DIRECT_PREFILTER_CANDIDATE_FIELDS = (
    _EXPECTED_CANDIDATE_FIELDS | {"direct_degenerate_prefilter"}
)
_SEARCH_CANDIDATE_FIELDS = {
    "candidate_id", "engine", "target_ratio", "target_error", "repair_profile", "region_overrides",
    "strategy", "update_vertices", "transfer",
}
_DIRECT_SOURCE_CORNER_MAP: dict[int, tuple[int, ...]] = {}
_BLENDER_STRATEGIES = frozenset({
    "blender-adaptive-v1",
    "blender-importance-map-v1",
    "round-planar-priority-v1",
    "round-priority-collapse-v1",
})
_PRESERVE_EXACT_BLENDER_STRATEGIES = frozenset({
    "blender-adaptive-v1",
    "blender-importance-map-v1",
})
_DIRECT_SERIALIZER_STRATEGIES = frozenset({
    "meshopt-direct-v1",
    "meshopt-direct-position-v1",
    "meshopt-remapped-topology-v1",
    "meshopt-remapped-visual-v1",
})
_POSITION_TOPOLOGY_STRATEGIES = frozenset({
    "meshopt-direct-position-v1",
    "meshopt-remapped-topology-v1",
    "meshopt-remapped-visual-v1",
})
_ADAPTIVE_EXACT_PRESERVATION_MATRIX = frozenset({
    ("eligible-exact-v1", True, "ratio-preserved-exact-v1"),
    ("eligible-exact-v1", True, "approved-exact-source-fallback-v1"),
    ("ineligible-changed-v1", False, "adaptive-output-changed-v1"),
    ("unauthorized-v1", True, "unproven-exact-preservation-v1"),
    ("unauthorized-v1", False, "unproven-exact-preservation-v1"),
})


@dataclass(frozen=True)
class CandidateConfig:
    candidate_id: str
    engine: str
    ratio: float
    target_error: float
    update_vertices: bool
    region_overrides: tuple[tuple[str, float], ...]
    strategy: str = "meshopt-project-v1"
    transfer: str = "projection-v1"
    direct_degenerate_prefilter: str | None = None


@dataclass(frozen=True)
class Settings:
    raw_root: Path
    root: Path
    candidate_json: Path
    meshopt_dll: Path | None
    candidate: CandidateConfig


@dataclass(frozen=True)
class SourceContract:
    original_text: str
    effective_text: str
    audit: "SmdAudit"
    source_triangle_count: int
    prefilter: DirectDegeneratePrefilter | None


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
    bone_nodes: tuple[tuple[int, str, int], ...] = ()
    influence_bones: tuple[int, ...] = ()
    uv_bounds: tuple[float, float, float, float] | None = None
    finite_normal_count: int = 0
    influence_sets: tuple[tuple[int, ...], ...] = ()
    uv_seam_positions: int = 0
    hard_normal_positions: int = 0
    position_normal_keys: int = 0


def _strict_number(name: str, value: object, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} is out of range")
    return result


def load_candidate_payload(payload: object) -> CandidateConfig:
    fields = frozenset(payload) if type(payload) is dict else frozenset()
    if type(payload) is not dict or fields not in {
        frozenset(_EXPECTED_CANDIDATE_FIELDS),
        frozenset(_DIRECT_PREFILTER_CANDIDATE_FIELDS),
        frozenset(_SEARCH_CANDIDATE_FIELDS),
    }:
        raise ValueError("candidate JSON has missing or unknown fields")
    from_search = fields == frozenset(_SEARCH_CANDIDATE_FIELDS)
    candidate_id = payload["candidate_id"]
    if type(candidate_id) is not str or not _ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate_id is unsafe")
    if payload["engine"] not in {"meshoptimizer", "blender"}:
        raise ValueError("engine must be meshoptimizer or blender")
    if from_search and (
        type(payload["repair_profile"]) is not str
        or not _ID_RE.fullmatch(payload["repair_profile"])
    ):
        raise ValueError("repair_profile is invalid")
    ratio_key = "target_ratio" if from_search else "ratio"
    ratio = _strict_number(ratio_key, payload[ratio_key], minimum=0.000001, maximum=1.0)
    target_error = _strict_number("target_error", payload["target_error"], minimum=0.0, maximum=1.0)
    update_vertices = payload["update_vertices"]
    if type(update_vertices) is not bool:
        raise ValueError("update_vertices must be bool")
    strategy = payload["strategy"]
    transfer = payload["transfer"]
    direct_prefilter = payload.get("direct_degenerate_prefilter")
    if direct_prefilter is not None and (
        direct_prefilter != "direct-degenerate-prefilter-v1"
        or strategy not in _DIRECT_SERIALIZER_STRATEGIES
    ):
        raise ValueError("direct prefilter requires a typed direct R&D strategy")
    contract = (payload["engine"], strategy, update_vertices, transfer)
    if contract not in {
        ("meshoptimizer", "meshopt-direct-v1", False, "direct-v1"),
        ("meshoptimizer", "meshopt-direct-position-v1", False, "direct-v1"),
        ("meshoptimizer", "meshopt-remapped-topology-v1", False, "remapped-topology-v1"),
        ("meshoptimizer", "meshopt-remapped-visual-v1", False, "visual-remapped-topology-v1"),
        ("meshoptimizer", "meshopt-project-v1", True, "projection-v1"),
        ("blender", "blender-adaptive-v1", True, "blender-native-v1"),
        ("blender", "blender-importance-map-v1", True, "blender-native-v1"),
        ("blender", "round-planar-priority-v1", True, "blender-native-v1"),
        ("blender", "round-priority-collapse-v1", True, "blender-native-v1"),
    }:
        raise ValueError("unknown or inconsistent optimizer strategy")
    if strategy in _BLENDER_STRATEGIES and target_error != 0.0:
        raise ValueError("Blender research target_error must be zero")
    if from_search and strategy in _DIRECT_SERIALIZER_STRATEGIES and payload["repair_profile"] != strategy:
        raise ValueError("repair_profile must identify the direct strategy")
    overrides = payload["region_overrides"]
    if type(overrides) is not list:
        raise ValueError("region_overrides must be a list")
    cleaned: dict[str, float] = {}
    for position, override in enumerate(overrides):
        if type(override) is not dict or set(override) != {"region_key", "ratio"}:
            raise ValueError(f"region_overrides[{position}] is invalid")
        key = override["region_key"]
        if type(key) is not str or not is_region_key(key) or key in cleaned:
            raise ValueError("region override key is unsafe or duplicated")
        cleaned[key] = _strict_number(
            f"region_overrides[{position}].ratio", override["ratio"], minimum=0.000001, maximum=1.0
        )
    return CandidateConfig(
        candidate_id=candidate_id,
        engine=payload["engine"],
        ratio=ratio,
        target_error=target_error,
        update_vertices=update_vertices,
        region_overrides=tuple(cleaned.items()),
        strategy=strategy,
        transfer=transfer,
        direct_degenerate_prefilter=direct_prefilter,
    )


def resolve_region_ratios(
    manifest: RegionManifest,
    observations: Sequence[tuple[str, str, tuple[str, ...]]],
    overrides: Sequence[tuple[str, float]],
    default_ratio: float,
) -> dict[tuple[str, str, tuple[str, ...]], float]:
    assignments = resolve_region_assignments(manifest, observations)
    by_key = {key: observation for observation, key in assignments.items()}
    result = {observation: default_ratio for observation in observations}
    seen: set[str] = set()
    for key, ratio in overrides:
        if key in seen:
            raise ValueError(f"ambiguous duplicate region override: {key}")
        seen.add(key)
        observation = by_key.get(key)
        if observation is None:
            raise ValueError(f"unknown region override: {key}")
        result[observation] = ratio
    return result


def make_simplify_options(
    candidate: CandidateConfig, ratio: float, policy: SimplificationPolicy
) -> SimplifyOptions:
    return SimplifyOptions(
        target_ratio=ratio,
        target_error=candidate.target_error,
        update_vertices=candidate.update_vertices,
        meshopt_options=policy.meshopt_options,
        position_remap=candidate.strategy in _POSITION_TOPOLOGY_STRATEGIES,
    )


def should_preserve_exact(
    candidate: CandidateConfig, region_ratios: Sequence[float]
) -> bool:
    return (
        candidate.strategy in (
            _PRESERVE_EXACT_BLENDER_STRATEGIES
            | _DIRECT_SERIALIZER_STRATEGIES
        )
        and preserve_whole_source(region_ratios)
    )


def _object_region_observations(
    source_identity: str,
    objects: Sequence[object],
    source_materials: Sequence[str] | None = None,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    result = []
    for obj in objects:
        datablock_names = tuple(
            str(material.name) if material else "none" for material in obj.data.materials
        )
        materials = (
            source_material_slot_identities(source_identity, source_materials, datablock_names)
            if source_materials is not None
            else datablock_names
        )
        result.append((source_identity, str(obj.name), materials))
    return tuple(result)


def optimize_region_objects(
    source_identity: str,
    objects: Sequence[object],
    candidate: CandidateConfig,
    manifest: RegionManifest,
    source_materials: Sequence[str] | None = None,
    *,
    optimizer=None,
) -> list[dict[str, object]]:
    if optimizer is None:
        optimizer = (
            _optimize_blender_object
            if candidate.strategy in _BLENDER_STRATEGIES
            else _optimize_mesh_object
        )
    observations = _object_region_observations(source_identity, objects, source_materials)
    source_manifest = manifest_for_source(manifest, source_identity)
    assignments = resolve_region_assignments(source_manifest, observations)
    source_overrides = tuple(
        (key, ratio)
        for key, ratio in candidate.region_overrides
        if key in source_manifest.by_key
    )
    ratios = resolve_region_ratios(
        source_manifest, observations, source_overrides, candidate.ratio
    )
    return [
        {
            **optimizer(obj, candidate, ratios[observation]),
            "region_key": assignments[observation],
        }
        for obj, observation in zip(objects, observations)
    ]


def _surviving_priority_vertices(obj: object, group: object) -> tuple[int, ...]:
    group_index = getattr(group, "index", None)
    if type(group_index) is not int or group_index < 0:
        raise SmdAuditValidationError("round priority group has invalid Blender index")
    surviving = tuple(
        int(vertex.index) if hasattr(vertex, "index") else index
        for index, vertex in enumerate(obj.data.vertices)
        if any(
            int(item.group) == group_index and float(item.weight) > 0.0
            for item in vertex.groups
        )
    )
    if not surviving:
        raise SmdAuditValidationError(
            "round priority assignments did not survive planar dissolve"
        )
    return surviving


def _round_identity_sha256(
    positions: Sequence[Sequence[float]],
) -> str:
    normalized = [
        [0.0 if float(value) == 0.0 else float(value) for value in row]
        for row in positions
    ]
    payload = json.dumps(
        normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _round_priority_identities(
    positions: Sequence[Sequence[float]],
    priority_vertices: Sequence[int],
) -> dict[str, object]:
    indices = tuple(sorted(set(priority_vertices)))
    if not indices or any(index < 0 or index >= len(positions) for index in indices):
        raise ValueError("round priority contains invalid or empty vertex identities")
    priority_positions = tuple(positions[index] for index in indices)
    canonical_ids, _ = _canonicalize_positions(
        priority_positions, POSITION_SERIALIZATION_TOLERANCE
    )
    canonical_positions = tuple(sorted(
        tuple(float(value) for value in priority_positions[index])
        for index in set(canonical_ids)
    ))
    return {
        "positions": canonical_positions,
        "count": len(canonical_positions),
        "sha256": _round_identity_sha256(canonical_positions),
    }


def _verify_round_priority_survival(
    expected: dict[str, object],
    actual_positions: Sequence[Sequence[float]],
) -> dict[str, object]:
    expected_positions = expected.get("positions")
    if not isinstance(expected_positions, tuple) or not expected_positions:
        raise ValueError("round priority identities are missing canonical positions")
    actual_ids, _ = _canonicalize_positions(
        actual_positions, POSITION_SERIALIZATION_TOLERANCE
    )
    actual_representatives = tuple(sorted(set(actual_ids)))
    actual_cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for actual_index in actual_representatives:
        cell = (
            math.floor(
                float(actual_positions[actual_index][0])
                / POSITION_SERIALIZATION_TOLERANCE
            ),
            math.floor(
                float(actual_positions[actual_index][1])
                / POSITION_SERIALIZATION_TOLERANCE
            ),
            math.floor(
                float(actual_positions[actual_index][2])
                / POSITION_SERIALIZATION_TOLERANCE
            ),
        )
        actual_cells[cell].append(actual_index)
    matched_actual: set[int] = set()
    matched_expected = []
    for expected_index, expected_position in enumerate(expected_positions):
        expected_cell = (
            math.floor(
                float(expected_position[0]) / POSITION_SERIALIZATION_TOLERANCE
            ),
            math.floor(
                float(expected_position[1]) / POSITION_SERIALIZATION_TOLERANCE
            ),
            math.floor(
                float(expected_position[2]) / POSITION_SERIALIZATION_TOLERANCE
            ),
        )
        nearby = []
        for x in range(expected_cell[0] - 1, expected_cell[0] + 2):
            for y in range(expected_cell[1] - 1, expected_cell[1] + 2):
                for z in range(expected_cell[2] - 1, expected_cell[2] + 2):
                    nearby.extend(actual_cells.get((x, y, z), ()))
        candidates = [
            actual_index
            for actual_index in nearby
            if actual_index not in matched_actual
            and all(
                abs(
                    float(expected_position[axis])
                    - float(actual_positions[actual_index][axis])
                ) <= POSITION_SERIALIZATION_TOLERANCE
                for axis in range(3)
            )
        ]
        if not candidates:
            raise SmdAuditValidationError(
                f"round priority vertex did not survive: {expected_index}"
            )
        matched_actual.add(min(candidates))
        matched_expected.append(expected_position)
    sha256 = _round_identity_sha256(matched_expected)
    if len(matched_expected) != expected.get("count") or sha256 != expected.get("sha256"):
        raise SmdAuditValidationError("round priority identity set changed")
    return {"count": len(matched_expected), "sha256": sha256}


def _verify_round_boundary_survival(
    expected_positions: Sequence[Sequence[float]],
    expected_edges: Sequence[tuple[int, int]],
    actual_positions: Sequence[Sequence[float]],
    actual_triangles: Sequence[Sequence[int]],
) -> dict[str, int]:
    """Require every protected geometric boundary vertex and edge to remain exact."""
    if not expected_edges:
        return {"boundary_vertices": 0, "boundary_edges": 0}
    expected_vertices = sorted({index for edge in expected_edges for index in edge})
    if any(index < 0 or index >= len(expected_positions) for index in expected_vertices):
        raise ValueError("round boundary edge contains an invalid source vertex")
    actual_ids, _ = _canonicalize_positions(
        actual_positions, POSITION_SERIALIZATION_TOLERANCE
    )
    actual_representatives = tuple(sorted(set(actual_ids)))
    matched: dict[int, int] = {}
    for expected_index in expected_vertices:
        expected = expected_positions[expected_index]
        candidates = [
            actual_index
            for actual_index in actual_representatives
            if all(
                abs(float(expected[axis]) - float(actual_positions[actual_index][axis]))
                <= POSITION_SERIALIZATION_TOLERANCE
                for axis in range(3)
            )
        ]
        if not candidates:
            raise SmdAuditValidationError(
                f"round boundary vertex did not survive: {expected_index}"
            )
        matched[expected_index] = min(candidates)

    owners: dict[tuple[int, int], int] = defaultdict(int)
    for triangle in actual_triangles:
        if len(triangle) != 3 or any(
            type(index) is not int or index < 0 or index >= len(actual_positions)
            for index in triangle
        ):
            raise ValueError("actual round triangles must contain valid index triples")
        canonical = tuple(actual_ids[index] for index in triangle)
        if len(set(canonical)) != 3:
            raise SmdAuditValidationError("round boundary audit found degenerate topology")
        for first, second in (
            (canonical[0], canonical[1]),
            (canonical[1], canonical[2]),
            (canonical[2], canonical[0]),
        ):
            owners[(min(first, second), max(first, second))] += 1
    for expected_first, expected_second in expected_edges:
        first, second = matched[expected_first], matched[expected_second]
        edge = (min(first, second), max(first, second))
        if first == second or owners.get(edge) != 1:
            raise SmdAuditValidationError(
                f"round boundary edge did not survive: {expected_first}-{expected_second}"
            )
    return {
        "boundary_vertices": len(expected_vertices),
        "boundary_edges": len(expected_edges),
    }


def _round_evidence_payload(
    decision: object, modifier_evidence: dict[str, object]
) -> dict[str, object]:
    components = [
        {
            "index": component.index,
            "canonical_vertices": component.canonical_vertices,
            "source_vertices": component.source_vertices,
            "triangles": component.triangles,
            "area_fraction": component.area_fraction,
            "extent_fraction": component.extent_fraction,
            "significant": component.significant,
            "eligible": component.eligible,
            "reason": component.reason,
            "axis": component.axis,
            "priority_vertices": component.priority_vertices,
            "boundary_edges": component.boundary_edges,
            "boundary_loops": [
                {
                    "vertices": loop.vertices,
                    "axial_span": loop.axial_span,
                    "angular_bins_occupied": loop.angular_bins_occupied,
                    "radial_cv": loop.radial_cv,
                    "center_offset_fraction": loop.center_offset_fraction,
                    "edge_length_cv": loop.edge_length_cv,
                    "outer_radius_fraction": loop.outer_radius_fraction,
                }
                for loop in component.boundary_loops
            ],
        }
        for component in decision.components
    ]
    return {
        "round_admission": str(decision.reason),
        "round_axis": decision.axis,
        "round_prepass": modifier_evidence["prepass"],
        "round_position_tolerance": decision.position_tolerance,
        "round_components": components,
        "round_planar_angle_degrees": modifier_evidence["planar_angle_degrees"],
        "round_planar_triangles_after": modifier_evidence["planar_triangles_after"],
        "round_collapse_triangles_before": modifier_evidence[
            "collapse_triangles_before"
        ],
        "round_collapse_triangles_after": modifier_evidence[
            "collapse_triangles_after"
        ],
        "round_priority_vertices_requested": modifier_evidence["priority_vertices_requested"],
        "round_priority_vertices_survived": modifier_evidence["priority_vertices_survived"],
        "round_priority_geometric_vertices_requested": modifier_evidence[
            "priority_geometric_vertices_requested"
        ],
        "round_priority_geometric_vertices_survived_planar": modifier_evidence[
            "priority_geometric_vertices_survived_planar"
        ],
        "round_priority_geometric_vertices_survived_collapse": modifier_evidence[
            "priority_geometric_vertices_survived_collapse"
        ],
        "round_priority_identity_sha256_requested": modifier_evidence[
            "priority_identity_sha256_requested"
        ],
        "round_priority_identity_sha256_planar": modifier_evidence[
            "priority_identity_sha256_planar"
        ],
        "round_priority_identity_sha256_collapse": modifier_evidence[
            "priority_identity_sha256_collapse"
        ],
        "round_boundary_vertices_requested": modifier_evidence["boundary_vertices_requested"],
        "round_boundary_edges_requested": modifier_evidence["boundary_edges_requested"],
        "round_boundary_vertices_survived_planar": modifier_evidence[
            "boundary_vertices_survived_planar"
        ],
        "round_boundary_edges_survived_planar": modifier_evidence[
            "boundary_edges_survived_planar"
        ],
        "round_boundary_vertices_survived_collapse": modifier_evidence[
            "boundary_vertices_survived_collapse"
        ],
        "round_boundary_edges_survived_collapse": modifier_evidence[
            "boundary_edges_survived_collapse"
        ],
    }


def _round_object_geometry(
    obj: object,
) -> tuple[tuple[tuple[float, float, float], ...], tuple[tuple[int, int, int], ...]]:
    obj.data.calc_loop_triangles()
    positions = tuple(tuple(float(value) for value in vertex.co) for vertex in obj.data.vertices)
    triangles = tuple(
        tuple(int(index) for index in triangle.vertices)
        for triangle in obj.data.loop_triangles
    )
    return positions, triangles


def _apply_round_planar_modifiers(
    obj: object,
    *,
    ratio: float,
    priority_vertices: Sequence[int],
    boundary_edges: Sequence[tuple[int, int]] = (),
    planar_angle_degrees: float = 1.0,
) -> dict[str, object]:
    if bpy is None:
        raise RuntimeError("round planar strategy requires Blender")
    if not priority_vertices:
        raise SmdAuditValidationError("round-planar-priority has no silhouette ring")
    if obj.vertex_groups.get("__maximum_round_priority_v1__") is not None:
        raise RuntimeError("reserved round priority vertex group already exists")
    group = obj.vertex_groups.new(name="__maximum_round_priority_v1__")
    group.add(list(priority_vertices), 1.0, "REPLACE")
    expected_positions = _round_object_geometry(obj)[0]
    expected_priority = _round_priority_identities(
        expected_positions, priority_vertices
    )
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        planar = obj.modifiers.new(name="MaximumRoundPlanar", type="DECIMATE")
        move_modifier_first(obj.modifiers, planar)
        planar.decimate_type = "DISSOLVE"
        planar.angle_limit = math.radians(float(planar_angle_degrees))
        planar.use_dissolve_boundaries = False
        planar.delimit = {"UV", "SHARP", "NORMAL", "MATERIAL", "SEAM"}
        bpy.ops.object.modifier_apply(modifier=planar.name)

        surviving_group = obj.vertex_groups.get("__maximum_round_priority_v1__")
        if surviving_group is None:
            raise SmdAuditValidationError(
                "round priority group did not survive planar dissolve"
            )
        surviving_vertices = _surviving_priority_vertices(obj, surviving_group)
        planar_positions, planar_triangles = _round_object_geometry(obj)
        planar_triangles_after = len(planar_triangles)
        planar_priority = _verify_round_priority_survival(
            expected_priority, planar_positions
        )
        if boundary_edges:
            planar_boundary = _verify_round_boundary_survival(
                expected_positions, boundary_edges, planar_positions, planar_triangles
            )
        else:
            planar_boundary = {"boundary_vertices": 0, "boundary_edges": 0}

        collapse = obj.modifiers.new(name="MaximumRoundPriority", type="DECIMATE")
        move_modifier_first(obj.modifiers, collapse)
        collapse.decimate_type = "COLLAPSE"
        collapse.ratio = float(ratio)
        collapse.use_collapse_triangulate = True
        collapse.vertex_group = surviving_group.name
        collapse.invert_vertex_group = True
        collapse.vertex_group_factor = 1.0
        bpy.ops.object.modifier_apply(modifier=collapse.name)
        collapse_positions, collapse_triangles = _round_object_geometry(obj)
        collapse_priority = _verify_round_priority_survival(
            expected_priority, collapse_positions
        )
        if boundary_edges:
            collapse_boundary = _verify_round_boundary_survival(
                expected_positions, boundary_edges, collapse_positions, collapse_triangles
            )
        else:
            collapse_boundary = {"boundary_vertices": 0, "boundary_edges": 0}
        return {
            "prepass": "planar-dissolve",
            "planar_angle_degrees": float(planar_angle_degrees),
            "planar_triangles_after": planar_triangles_after,
            "collapse_triangles_before": planar_triangles_after,
            "collapse_triangles_after": len(collapse_triangles),
            "priority_vertices_requested": len(tuple(priority_vertices)),
            "priority_vertices_survived": len(surviving_vertices),
            "priority_geometric_vertices_requested": expected_priority["count"],
            "priority_geometric_vertices_survived_planar": planar_priority["count"],
            "priority_geometric_vertices_survived_collapse": collapse_priority["count"],
            "priority_identity_sha256_requested": expected_priority["sha256"],
            "priority_identity_sha256_planar": planar_priority["sha256"],
            "priority_identity_sha256_collapse": collapse_priority["sha256"],
            "boundary_vertices_requested": len({
                index for edge in boundary_edges for index in edge
            }),
            "boundary_edges_requested": len(tuple(boundary_edges)),
            "boundary_vertices_survived_planar": planar_boundary["boundary_vertices"],
            "boundary_edges_survived_planar": planar_boundary["boundary_edges"],
            "boundary_vertices_survived_collapse": collapse_boundary["boundary_vertices"],
            "boundary_edges_survived_collapse": collapse_boundary["boundary_edges"],
        }
    finally:
        remaining = obj.vertex_groups.get("__maximum_round_priority_v1__")
        if remaining is not None:
            obj.vertex_groups.remove(remaining)


def _apply_round_collapse_only_modifier(
    obj: object,
    *,
    ratio: float,
    priority_vertices: Sequence[int],
    boundary_edges: Sequence[tuple[int, int]] = (),
) -> dict[str, object]:
    if bpy is None:
        raise RuntimeError("round collapse strategy requires Blender")
    if not priority_vertices:
        raise SmdAuditValidationError("round-priority-collapse has no silhouette ring")
    group_name = "__maximum_round_priority_v1__"
    if obj.vertex_groups.get(group_name) is not None:
        raise RuntimeError("reserved round priority vertex group already exists")
    group = obj.vertex_groups.new(name=group_name)
    group.add(list(priority_vertices), 1.0, "REPLACE")
    expected_positions, source_triangles = _round_object_geometry(obj)
    expected_priority = _round_priority_identities(
        expected_positions, priority_vertices
    )
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        collapse = obj.modifiers.new(name="MaximumRoundPriorityCollapse", type="DECIMATE")
        move_modifier_first(obj.modifiers, collapse)
        collapse.decimate_type = "COLLAPSE"
        collapse.ratio = float(ratio)
        collapse.use_collapse_triangulate = True
        collapse.vertex_group = group.name
        collapse.invert_vertex_group = True
        collapse.vertex_group_factor = 1.0
        bpy.ops.object.modifier_apply(modifier=collapse.name)

        collapse_positions, collapse_triangles = _round_object_geometry(obj)
        collapse_priority = _verify_round_priority_survival(
            expected_priority, collapse_positions
        )
        if boundary_edges:
            collapse_boundary = _verify_round_boundary_survival(
                expected_positions,
                boundary_edges,
                collapse_positions,
                collapse_triangles,
            )
        else:
            collapse_boundary = {"boundary_vertices": 0, "boundary_edges": 0}
        if len(obj.data.polygons) != len(collapse_triangles):
            raise SmdAuditValidationError(
                "round collapse output must be explicitly triangulated"
            )
        return {
            "prepass": "none",
            "planar_angle_degrees": None,
            "planar_triangles_after": None,
            "collapse_triangles_before": len(source_triangles),
            "collapse_triangles_after": len(collapse_triangles),
            "priority_vertices_requested": len(tuple(priority_vertices)),
            "priority_vertices_survived": None,
            "priority_geometric_vertices_requested": expected_priority["count"],
            "priority_geometric_vertices_survived_planar": None,
            "priority_geometric_vertices_survived_collapse": collapse_priority["count"],
            "priority_identity_sha256_requested": expected_priority["sha256"],
            "priority_identity_sha256_planar": None,
            "priority_identity_sha256_collapse": collapse_priority["sha256"],
            "boundary_vertices_requested": len({
                index for edge in boundary_edges for index in edge
            }),
            "boundary_edges_requested": len(tuple(boundary_edges)),
            "boundary_vertices_survived_planar": None,
            "boundary_edges_survived_planar": None,
            "boundary_vertices_survived_collapse": collapse_boundary[
                "boundary_vertices"
            ],
            "boundary_edges_survived_collapse": collapse_boundary["boundary_edges"],
        }
    finally:
        remaining = obj.vertex_groups.get(group_name)
        if remaining is not None:
            obj.vertex_groups.remove(remaining)


def _optimize_blender_object(
    obj: object, candidate: CandidateConfig, ratio: float
) -> dict[str, object]:
    """Apply Blender collapse decimation to one stable source region.

    Blender owns interpolation of UVs and vertex groups. The post-modifier loop
    normals are reapplied as custom split normals so Source Tools consumes the
    evaluated shading instead of falling back to flat ``from_pydata`` normals.
    """
    if bpy is None:
        raise RuntimeError("Blender adaptive strategy requires Blender")
    if candidate.strategy not in _BLENDER_STRATEGIES:
        raise ValueError("Blender optimizer received a non-Blender strategy")
    mesh = obj.data
    mesh.calc_loop_triangles()
    triangles_before = len(mesh.loop_triangles)
    vertices_before = len(mesh.vertices)
    if triangles_before <= 0:
        raise ValueError("imported mesh has no triangles")
    importance = None
    importance_group = None
    round_evidence = None
    if candidate.strategy in {
        "round-planar-priority-v1",
        "round-priority-collapse-v1",
    }:
        positions = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
        faces = tuple(tuple(int(index) for index in tri.vertices) for tri in mesh.loop_triangles)
        influences = tuple(
            tuple((int(item.group), float(item.weight)) for item in vertex.groups)
            for vertex in mesh.vertices
        )
        decision = classify_round_component(positions, faces, influences)
        if not decision.eligible:
            raise SmdAuditValidationError(
                f"{candidate.strategy} rejected: {decision.reason}"
            )
        if candidate.strategy == "round-planar-priority-v1":
            modifier_evidence = _apply_round_planar_modifiers(
                obj,
                ratio=ratio,
                priority_vertices=decision.priority_vertices,
                boundary_edges=decision.boundary_edges,
            )
        else:
            modifier_evidence = _apply_round_collapse_only_modifier(
                obj,
                ratio=ratio,
                priority_vertices=decision.priority_vertices,
                boundary_edges=decision.boundary_edges,
            )
        round_evidence = _round_evidence_payload(decision, modifier_evidence)
        modifier = None
    else:
        modifier = obj.modifiers.new(name="MaximumCompilerAware", type="DECIMATE")
        move_modifier_first(obj.modifiers, modifier)
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = float(ratio)
        modifier.use_collapse_triangulate = True
    if candidate.strategy == "blender-importance-map-v1":
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            raise RuntimeError("importance strategy requires an active UV layer")
        faces = tuple(tuple(int(index) for index in tri.vertices) for tri in mesh.loop_triangles)
        corner_uvs = tuple(
            tuple(tuple(float(value) for value in uv_layer.data[index].uv) for index in tri.loops)
            for tri in mesh.loop_triangles
        )
        materials = tuple(
            int(mesh.polygons[tri.polygon_index].material_index) for tri in mesh.loop_triangles
        )
        skin = tuple(
            tuple(sorted(
                (int(item.group), float(item.weight))
                for item in vertex.groups if float(item.weight) > 0.0
            ))
            for vertex in mesh.vertices
        )
        importance = build_importance_weights(MeshImportanceInput(
            positions=tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices),
            faces=faces,
            face_materials=materials,
            corner_uvs=corner_uvs,
            skin_signatures=skin,
        ))
        if obj.vertex_groups.get("__maximum_importance_v1__") is not None:
            raise RuntimeError("reserved importance vertex group already exists")
        importance_group = obj.vertex_groups.new(name="__maximum_importance_v1__")
        for vertex_index, weight in enumerate(importance.weights):
            if weight > 0.0:
                importance_group.add([vertex_index], weight, "REPLACE")
        modifier.vertex_group = importance_group.name
        # Blender Decimate interprets the named group as the affected region.
        # Inversion is therefore required to protect high-importance weights.
        modifier.invert_vertex_group = True
        modifier.vertex_group_factor = 1.0
    if modifier is not None:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    if importance_group is not None:
        remaining_group = obj.vertex_groups.get("__maximum_importance_v1__")
        if remaining_group is not None:
            obj.vertex_groups.remove(remaining_group)
    mesh.calc_loop_triangles()
    triangles_after = len(mesh.loop_triangles)
    if triangles_after <= 0:
        raise RuntimeError("Blender adaptive strategy removed the complete region")
    require_triangular_mesh(len(mesh.polygons), triangles_after)
    faces = tuple(tuple(int(index) for index in tri.vertices) for tri in mesh.loop_triangles)
    positions = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
    loop_normals = tuple(
        tuple(float(value) for value in mesh.loops[index].normal)
        for tri in mesh.loop_triangles for index in tri.loops
    )
    reconstruction = apply_reconstructed_smoothing(mesh, positions, faces, loop_normals)
    metrics = {
        "triangles_before": triangles_before,
        "triangles_after": triangles_after,
        "source_vertices": vertices_before,
        "output_vertices": len(mesh.vertices),
        "requested_ratio": ratio,
        "achieved_ratio": triangles_after / triangles_before,
        "smooth_faces": sum(reconstruction.smooth_faces),
        "sharp_edges": len(reconstruction.sharp_edges),
        "strategy": candidate.strategy,
        "transfer": candidate.transfer,
    }
    if importance is not None:
        reason_counts = Counter(reason for items in importance.reasons for reason in items)
        metrics.update({
            "importance_protected_vertices": len(importance.protected_vertices),
            "importance_protected_fraction": len(importance.protected_vertices) / vertices_before,
            "importance_reasons": dict(sorted(reason_counts.items())),
            "importance_group_inverted": True,
            "importance_group_removed": obj.vertex_groups.get("__maximum_importance_v1__") is None,
        })
    if round_evidence is not None:
        metrics.update(round_evidence)
    return metrics


def reject_unsupported_dmx(graph: QcGraph) -> None:
    dmx = next(
        (reference for reference in graph.references if reference.source_path.suffix.casefold() == ".dmx"),
        None,
    )
    if dmx is not None:
        raise ValueError(
            f"DMX is not supported by Maximum audit: {dmx.logical_path} "
            f"({dmx.directive} line {dmx.line})"
        )


def parse_args(argv: Sequence[str]) -> Settings:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--candidate-json", required=True)
    parser.add_argument("--meshopt-dll")
    parsed = parser.parse_args(argv)
    raw_root = _raw_absolute(Path(parsed.root).expanduser())
    _reject_lexical_reparse_components(raw_root)
    try:
        root = raw_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"root does not exist: {raw_root}") from exc
    candidate_json = Path(parsed.candidate_json).expanduser().resolve()
    meshopt_dll = Path(parsed.meshopt_dll).expanduser().resolve() if parsed.meshopt_dll else None
    if not root.is_dir():
        raise ValueError(f"root does not exist: {root}")
    try:
        payload = json.loads(candidate_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read candidate JSON: {candidate_json}") from exc
    candidate = load_candidate_payload(payload)
    if candidate.engine == "meshoptimizer" and meshopt_dll is None:
        raise ValueError("meshoptimizer candidate requires --meshopt-dll")
    return Settings(raw_root, root, candidate_json, meshopt_dll, candidate)


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
    face_normals: list[tuple[float, float, float]] = []
    for triangle in triangles:
        a, b, c = (vertices[index] for index in triangle)
        first = tuple(float(b[axis]) - float(a[axis]) for axis in range(3))
        second = tuple(float(c[axis]) - float(a[axis]) for axis in range(3))
        cross = (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )
        length = math.sqrt(sum(value * value for value in cross))
        face_normals.append(tuple(value / length for value in cross) if length > 1e-12 else (0.0, 0.0, 1.0))
    for edge, owners in edge_triangles.items():
        if len(owners) == 1:
            for vertex in edge:
                flags[vertex] |= LOCK
        if len(owners) >= 2 and len({material_ids[index] for index in owners}) > 1:
            for vertex in edge:
                flags[vertex] |= PROTECT
        if len(owners) == 2:
            first, second = (face_normals[index] for index in owners)
            if sum(first[axis] * second[axis] for axis in range(3)) < math.cos(math.radians(50.0)):
                for vertex in edge:
                    flags[vertex] |= LOCK | PROTECT
    longest = max(geometry.extents)
    if longest > 0:
        incident_lengths: dict[int, list[float]] = defaultdict(list)
        neighbors: dict[int, set[int]] = defaultdict(set)
        for edge in edge_triangles:
            a, b = edge
            length = math.sqrt(
                sum((float(vertices[a][axis]) - float(vertices[b][axis])) ** 2 for axis in range(3))
            )
            incident_lengths[a].append(length)
            incident_lengths[b].append(length)
            neighbors[a].add(b)
            neighbors[b].add(a)
        for vertex, lengths in incident_lengths.items():
            local_scale = sorted(lengths)[len(lengths) // 2]
            if local_scale < longest * 0.02 and len(neighbors[vertex]) <= 5:
                flags[vertex] |= PRIORITY | PROTECT
        for triangle in triangles:
            lengths = []
            for a, b in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                lengths.append(
                    math.sqrt(
                        sum((float(vertices[a][axis]) - float(vertices[b][axis])) ** 2 for axis in range(3))
                    )
                )
            if max(lengths) > 0 and min(lengths) / max(lengths) < 0.08:
                for vertex in triangle:
                    flags[vertex] |= PRIORITY | PROTECT
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


def audit_smd_text(text: str) -> SmdAudit:
    section = ""
    triangle_lines: list[str] = []
    materials: list[str] = []
    bone_nodes: list[tuple[int, str, int]] = []
    influence_bones: set[int] = set()
    u_values: list[float] = []
    v_values: list[float] = []
    finite_normal_count = 0
    influence_sets: set[tuple[int, ...]] = set()
    uvs_by_position: dict[tuple[float, float, float], set[tuple[float, float]]] = defaultdict(set)
    normals_by_position: dict[tuple[float, float, float], set[tuple[float, float, float]]] = defaultdict(set)
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
            match = re.match(r'^(-?\d+)\s+"([^"]*)"\s+(-?\d+)', line)
            if match:
                bone_nodes.append((int(match.group(1)), match.group(2), int(match.group(3))))
        elif section == "triangles" and line:
            parts = line.split()
            if len(parts) >= 9 and parts[0].lstrip("-").isdigit():
                triangle_lines.append(line)
                influence_bones.add(int(parts[0]))
                try:
                    position = tuple(float(parts[index]) for index in (1, 2, 3))
                    normal = tuple(float(parts[index]) for index in (4, 5, 6))
                    uv = (float(parts[7]), float(parts[8]))
                except ValueError as exc:
                    raise ValueError("SMD vertex has non-numeric normal/UV") from exc
                if not all(math.isfinite(value) for value in (*normal, *uv)):
                    raise ValueError("SMD vertex has non-finite normal/UV")
                if sum(value * value for value in normal) > 1e-12:
                    finite_normal_count += 1
                u_values.append(uv[0])
                v_values.append(uv[1])
                current_influences = {int(parts[0])}
                if len(parts) > 9:
                    try:
                        link_count = int(parts[9])
                    except ValueError as exc:
                        raise ValueError("SMD vertex has invalid influence count") from exc
                    if link_count < 0 or len(parts) < 10 + link_count * 2:
                        raise ValueError("SMD vertex influence row is truncated")
                    if link_count:
                        current_influences = set()
                    for link_position in range(link_count):
                        bone = int(parts[10 + link_position * 2])
                        influence_bones.add(bone)
                        current_influences.add(bone)
                influence_sets.add(tuple(sorted(current_influences)))
                position_key = tuple(round(value, 6) for value in position)
                uvs_by_position[position_key].add(tuple(round(value, 6) for value in uv))
                normals_by_position[position_key].add(tuple(round(value, 6) for value in normal))
            else:
                if line not in materials:
                    materials.append(line)
    uv_bounds = (
        (min(u_values), max(u_values), min(v_values), max(v_values)) if u_values else None
    )
    return SmdAudit(
        len(triangle_lines) // 3,
        tuple(materials),
        tuple(node[0] for node in bone_nodes),
        tuple(bone_nodes),
        tuple(sorted(influence_bones)),
        uv_bounds,
        finite_normal_count,
        tuple(sorted(influence_sets)),
        sum(1 for values in uvs_by_position.values() if len(values) > 1),
        sum(1 for values in normals_by_position.values() if len(values) > 1),
        sum(len(values) for values in normals_by_position.values()),
    )


def audit_exported_smd_text(text: str) -> SmdAudit:
    """Type only destination parse/value failures that permit exact-source fallback."""
    try:
        return audit_smd_text(text)
    except ValueError as exc:
        raise SmdAuditValidationError(f"exported SMD audit failed: {exc}") from exc


def validate_smd_audits(before: SmdAudit, after: SmdAudit) -> None:
    if not set(before.materials).issubset(after.materials):
        raise SmdAuditValidationError("export lost SMD materials")
    if before.bone_nodes != after.bone_nodes:
        raise SmdAuditValidationError("export changed SMD bone names/order/hierarchy")
    if not set(before.influence_bones).issubset(after.influence_bones):
        raise SmdAuditValidationError("export lost SMD bone influence identities")
    if not set(before.influence_sets).issubset(after.influence_sets):
        raise SmdAuditValidationError("export lost SMD bone influence sets")
    if before.uv_seam_positions and not after.uv_seam_positions:
        raise SmdAuditValidationError("export lost all UV seam evidence")
    if before.hard_normal_positions and not after.hard_normal_positions:
        raise SmdAuditValidationError("export lost all hard-normal seam evidence")
    if after.position_normal_keys > before.position_normal_keys:
        raise SmdAuditValidationError(
            "export amplified SMD position-normal keys "
            f"({before.position_normal_keys}->{after.position_normal_keys})"
        )
    if before.uv_bounds is not None and after.uv_bounds is None:
        raise SmdAuditValidationError("export has incomplete UV evidence")
    before_corners = before.triangle_count * 3
    after_corners = after.triangle_count * 3
    if before.finite_normal_count > 0 and after.finite_normal_count == 0:
        raise SmdAuditValidationError("export has zero valid normals")
    if (before_corners > 0 and after_corners > 0
            and after.finite_normal_count * before_corners
            < before.finite_normal_count * after_corners):
        raise SmdAuditValidationError("export has worse finite-normal coverage")


def restore_smd_normal_identity(
    original_text: str,
    exported_text: str,
    *,
    max_distance: float = 2e-2,
    position_tolerance: float = 2e-6,
) -> str:
    return restore_ordered_smd_normals(
        original_text,
        exported_text,
        max_distance=max_distance,
        position_tolerance=position_tolerance,
    )


def restore_smd_bone_identity(original_text: str, exported_text: str) -> str:
    def node_section(text: str) -> tuple[int, int, list[str], dict[int, str]]:
        lines = text.splitlines(keepends=True)
        start = next((index for index, line in enumerate(lines) if line.strip().casefold() == "nodes"), -1)
        if start < 0:
            raise ValueError("SMD nodes section is missing")
        end = next((index for index in range(start + 1, len(lines)) if lines[index].strip().casefold() == "end"), -1)
        if end < 0:
            raise ValueError("SMD nodes section is unterminated")
        mapping: dict[int, str] = {}
        for line in lines[start + 1 : end]:
            match = re.match(r'^\s*(-?\d+)\s+"([^"]*)"\s+(-?\d+)', line)
            if match:
                mapping[int(match.group(1))] = match.group(2)
        return start, end, lines, mapping

    original_start, original_end, original_lines, original_ids = node_section(original_text)
    exported_start, exported_end, exported_lines, exported_ids = node_section(exported_text)
    original_by_name = {name: bone_id for bone_id, name in original_ids.items()}
    if set(original_by_name) != set(exported_ids.values()):
        raise ValueError("exported SMD bone names differ from original")
    remap = {bone_id: original_by_name[name] for bone_id, name in exported_ids.items()}
    output = (
        exported_lines[: exported_start + 1]
        + original_lines[original_start + 1 : original_end]
        + exported_lines[exported_end:]
    )
    section = ""
    for index, raw in enumerate(output):
        line = raw.strip()
        folded = line.casefold()
        if folded in {"nodes", "skeleton", "triangles"}:
            section = folded
            continue
        if folded == "end":
            section = ""
            continue
        if section not in {"skeleton", "triangles"} or not line:
            continue
        parts = line.split()
        if not parts or not parts[0].lstrip("-").isdigit():
            continue
        if section == "triangles" and len(parts) < 9:
            continue
        old = int(parts[0])
        if old not in remap:
            raise ValueError(f"exported SMD references unknown bone id {old}")
        parts[0] = str(remap[old])
        if section == "triangles" and len(parts) > 9:
            link_count = int(parts[9])
            for link in range(link_count):
                position = 10 + link * 2
                link_id = int(parts[position])
                if link_id not in remap:
                    raise ValueError(f"exported SMD link references unknown bone id {link_id}")
                parts[position] = str(remap[link_id])
        ending = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
        output[index] = " ".join(parts) + ending
    return "".join(output)


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
    tuple[tuple[int, int, int], ...],
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float], ...],
    tuple[int, ...],
    tuple[tuple[tuple[str, float], ...], ...],
]:
    mesh = obj.data
    mesh.update()
    positions = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
    mesh.calc_loop_triangles()
    triangles = tuple(tuple(int(index) for index in triangle.vertices) for triangle in mesh.loop_triangles)
    loop_normals: list[tuple[float, float, float]] = []
    loop_uvs: list[tuple[float, float]] = []
    uv_layer = mesh.uv_layers.active
    for triangle in mesh.loop_triangles:
        for loop_index in triangle.loops:
            corner_normal = mesh.corner_normals[loop_index].vector
            loop_normals.append(tuple(float(value) for value in corner_normal))
            if uv_layer is None:
                loop_uvs.append((0.0, 0.0))
            else:
                uv = uv_layer.data[loop_index].uv
                loop_uvs.append((float(uv.x), float(uv.y)))

    group_weights = []
    for vertex in mesh.vertices:
        entries = sorted(
            ((obj.vertex_groups[int(group.group)].name, float(group.weight)) for group in vertex.groups if group.weight > 0.0),
            key=lambda item: (-item[1], item[0]),
        )[:4]
        group_weights.append(normalize_influences(entries) if entries else (("__maximum_rigid__", 1.0),))
    material_ids = tuple(int(triangle.material_index) for triangle in mesh.loop_triangles)
    return positions, triangles, tuple(loop_normals), tuple(loop_uvs), material_ids, tuple(group_weights)


def projection_vertex_materials(
    vertex_count: int,
    result_indices: Sequence[int],
    result_material_ids: Sequence[int],
    source_material_ids: Sequence[int],
    material_slot_count: int,
) -> dict[int, int]:
    if vertex_count <= 0 or len(result_indices) % 3 or len(result_material_ids) != len(result_indices) // 3:
        raise ValueError("projection topology/material counts are invalid")
    if material_slot_count <= 0:
        raise ValueError("projection material slot count is invalid")
    source_materials = set()
    for material in source_material_ids:
        if type(material) is not int or material < 0 or material >= material_slot_count:
            raise ValueError("source projection material is out of range")
        source_materials.add(material)
    result: dict[int, int] = {}
    for triangle_index, material in enumerate(result_material_ids):
        if type(material) is not int or material < 0 or material >= material_slot_count:
            raise ValueError("result projection material is out of range")
        if material not in source_materials:
            raise ValueError("result projection has no compatible source material")
        for vertex in result_indices[triangle_index * 3 : triangle_index * 3 + 3]:
            if type(vertex) is not int or vertex < 0 or vertex >= vertex_count:
                raise ValueError("projection vertex index is out of range")
            previous = result.get(vertex)
            if previous is not None and previous != material:
                raise ValueError("projection vertex has mixed incompatible material corners")
            result[vertex] = material
    return result


def build_region_material_bvhs(
    positions: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    material_ids: Sequence[int],
    *,
    vector_factory,
    bvh_factory,
) -> dict[int, tuple[object, tuple[int, ...]]]:
    if len(triangles) != len(material_ids):
        raise ValueError("source projection material counts do not match triangles")
    converted = [vector_factory(position) for position in positions]
    faces_by_material: dict[int, list[int]] = defaultdict(list)
    for face_index, (triangle, material) in enumerate(zip(triangles, material_ids)):
        if len(triangle) != 3 or any(index < 0 or index >= len(converted) for index in triangle):
            raise ValueError("source projection triangle is invalid")
        faces_by_material[int(material)].append(face_index)
    result: dict[int, tuple[object, tuple[int, ...]]] = {}
    for material, face_indices in sorted(faces_by_material.items()):
        polygons = [tuple(int(index) for index in triangles[face]) for face in face_indices]
        result[material] = (bvh_factory(converted, polygons), tuple(face_indices))
    return result


def find_compatible_projection(
    buckets: dict[int, tuple[object, tuple[int, ...]]], material: int, point: object
) -> tuple[object, int, float]:
    bucket = buckets.get(material)
    if bucket is None:
        raise RuntimeError(f"surface projection has no compatible region/material bucket: {material}")
    bvh, face_indices = bucket
    nearest = bvh.find_nearest(point)
    if nearest is None or nearest[0] is None or nearest[2] is None:
        raise RuntimeError("compatible surface projection failed")
    local_face = int(nearest[2])
    if local_face < 0 or local_face >= len(face_indices):
        raise RuntimeError("compatible surface projection returned an invalid face")
    distance = float(nearest[3]) if nearest[3] is not None else math.inf
    return nearest[0], face_indices[local_face], distance


def rebuild_mesh_smoothing_only(
    obj: object, normal_identity_keys: Sequence[object] | None = None
) -> dict[str, object]:
    """Rebuild an imported triangle mesh without changing its geometry or attributes."""
    mesh = obj.data
    mesh.update()
    mesh.calc_loop_triangles()
    positions = tuple(tuple(float(value) for value in vertex.co) for vertex in mesh.vertices)
    faces = tuple(tuple(int(index) for index in triangle.vertices) for triangle in mesh.loop_triangles)
    loop_normals = tuple(
        tuple(float(value) for value in mesh.corner_normals[loop_index].vector)
        for triangle in mesh.loop_triangles
        for loop_index in triangle.loops
    )
    loop_normals = canonicalize_export_normals(loop_normals)
    if normal_identity_keys is not None:
        loop_normals = canonicalize_normals_by_identity(loop_normals, normal_identity_keys)
    uv_layer = mesh.uv_layers.active
    loop_uvs = tuple(
        (0.0, 0.0) if uv_layer is None else tuple(float(value) for value in uv_layer.data[loop_index].uv)
        for triangle in mesh.loop_triangles
        for loop_index in triangle.loops
    )
    material_ids = tuple(int(triangle.material_index) for triangle in mesh.loop_triangles)
    materials = tuple(mesh.materials)
    vertex_groups = tuple(
        tuple(
            sorted(
                (
                    (obj.vertex_groups[int(item.group)].name, float(item.weight))
                    for item in vertex.groups
                ),
                key=lambda item: item[0],
            )
        )
        for vertex in mesh.vertices
    )
    if not faces or len(loop_normals) != len(faces) * 3:
        raise ValueError("smoothing-only rebuild requires a non-empty triangle mesh")

    mesh.clear_geometry()
    mesh.materials.clear()
    mesh.from_pydata(positions, (), faces)
    for material in materials:
        mesh.materials.append(material)
    for polygon, material_id in zip(mesh.polygons, material_ids):
        if material_id < 0 or material_id >= max(1, len(mesh.materials)):
            raise RuntimeError("smoothing-only material mapping is out of range")
        polygon.material_index = material_id
    rebuilt_uv = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(mesh.loops, loop_uvs):
        rebuilt_uv.data[loop.index].uv = uv

    all_indices = list(range(len(mesh.vertices)))
    for group in obj.vertex_groups:
        group.remove(all_indices)
    groups_by_name = {group.name: group for group in obj.vertex_groups}
    for vertex_index, entries in enumerate(vertex_groups):
        for group_name, weight in entries:
            group = groups_by_name.get(group_name)
            if group is None:
                group = obj.vertex_groups.new(name=group_name)
                groups_by_name[group_name] = group
            group.add([vertex_index], weight, "REPLACE")

    reconstruction = apply_reconstructed_smoothing(mesh, positions, faces, loop_normals)
    return {
        "vertices": len(positions),
        "triangles": len(faces),
        "smooth_faces": sum(reconstruction.smooth_faces),
        "sharp_edges": len(reconstruction.sharp_edges),
    }


def _optimize_mesh_object(obj: object, candidate: CandidateConfig, ratio: float) -> dict[str, object]:
    mesh = obj.data
    mesh.calc_loop_triangles()
    positions, triangles, loop_normals, loop_uvs, material_ids, vertex_influences = _vertex_source_attributes(obj)
    group_names = tuple(group.name for group in obj.vertex_groups)
    if not triangles:
        raise ValueError("imported mesh has no triangles")
    skinned = len(obj.vertex_groups) > 0
    wedges = build_wedge_mesh(
        positions, triangles, loop_normals, loop_uvs, material_ids, vertex_influences,
        exact_float32=candidate.strategy in _POSITION_TOPOLOGY_STRATEGIES,
        source_corner_indices=_DIRECT_SOURCE_CORNER_MAP.get(id(obj)),
    )
    wedge_triangles = tuple(
        tuple(wedges.indices[offset : offset + 3]) for offset in range(0, len(wedges.indices), 3)
    )
    policy = derive_simplification_policy(
        wedges.positions,
        wedge_triangles,
        material_ids=material_ids,
        skin_weights=wedges.weights if skinned else None,
    )
    position_topology = None
    if candidate.strategy in _POSITION_TOPOLOGY_STRATEGIES:
        position_topology = classify_position_topology(
            wedges.positions, wedges.normals, wedges.uvs, wedges.indices, material_ids,
            wedges.weights, wedges.bone_indices,
            tuple(flag & PRIORITY for flag in policy.vertex_flags),
        )
        policy = SimplificationPolicy(
            position_topology.vertex_flags,
            policy.meshopt_options,
            policy.geometry,
        )
    elif skinned:
        flags = list(policy.vertex_flags)
        signatures = [
            tuple(sorted({bone for bone, weight in zip(bones, weights) if weight > 0.0}))
            for bones, weights in zip(wedges.bone_indices, wedges.weights)
        ]
        counts = Counter(signatures)
        dominant = max(counts, key=lambda item: (counts[item], item))
        for index, signature in enumerate(signatures):
            if signature != dominant:
                flags[index] |= LOCK | PROTECT
        policy = SimplificationPolicy(tuple(flags), policy.meshopt_options, policy.geometry)
    source = MeshInput(
        positions=wedges.positions,
        normals=wedges.normals,
        uvs=wedges.uvs,
        weights=wedges.weights,
        bone_indices=wedges.bone_indices,
        indices=wedges.indices,
        material_ids=material_ids,
        vertex_flags=policy.vertex_flags,
    )
    result = simplify_mesh(source, make_simplify_options(candidate, ratio, policy))
    if len(result.indices) >= len(source.indices) and ratio < 0.999999:
        raise RuntimeError("meshoptimizer did not reduce this mesh")

    if candidate.strategy in _DIRECT_SERIALIZER_STRATEGIES:
        direct = compact_direct_result(source, result)
        compact_positions = list(direct.positions)
        compact_normals = list(direct.normals)
        compact_uvs = list(direct.uvs)
        compact_influences = [
            wedges.corner_influences[index] for index in direct.source_vertex_indices
        ]
        output_indices = direct.indices
        direct_corner_ordinals = tuple(
            wedges.source_loop_indices[direct.source_vertex_indices[index]] for index in direct.indices
        )
        direct_smd_payload = {
            "positions": tuple(compact_positions),
            "normals": tuple(compact_normals),
            "uvs": tuple(compact_uvs),
            "influences": tuple(compact_influences),
            "indices": tuple(output_indices),
            "materials": tuple(str(mesh.materials[index].name) for index in result.material_ids),
            "source_corner_ordinals": tuple(
                wedges.source_loop_indices[index] for index in direct.source_vertex_indices
            ),
        }
    else:
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
        from mathutils.geometry import closest_point_on_tri

        vertex_materials = projection_vertex_materials(
            len(result.positions), result.indices, result.material_ids, material_ids, len(mesh.materials)
        )
        used_vertices = set(result.indices)
        transferred: dict[int, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float], tuple[tuple[str, float], ...]]] = {}
        material_bvhs = build_region_material_bvhs(
            positions,
            triangles,
            material_ids,
            vector_factory=Vector,
            bvh_factory=lambda vertices, polygons: BVHTree.FromPolygons(
                vertices, polygons, all_triangles=True
            ),
        )
        for index in used_vertices:
            target_material = vertex_materials[index]
            projected, face_index, nearest_distance = find_compatible_projection(
                material_bvhs, target_material, Vector(result.positions[index])
            )
            hint_face = wedges.source_loop_indices[index] // 3
            if material_ids[hint_face] != target_material:
                raise RuntimeError("projection hint has an incompatible region/material")
            hint_triangle = triangles[hint_face]
            hint_projected = closest_point_on_tri(
                Vector(result.positions[index]), *(Vector(positions[vertex]) for vertex in hint_triangle)
            )
            hint_distance = (hint_projected - Vector(result.positions[index])).length
            if hint_distance <= nearest_distance + max(1e-6, nearest_distance * 1e-4):
                projected = hint_projected
                face_index = hint_face
            triangle = triangles[face_index]
            barycentric = barycentric_weights(projected, *(positions[vertex] for vertex in triangle))
            corner_offset = face_index * 3
            normals = loop_normals[corner_offset : corner_offset + 3]
            uvs = loop_uvs[corner_offset : corner_offset + 3]
            influences = tuple(vertex_influences[vertex] for vertex in triangle)
            transferred[index] = (
                tuple(float(value) for value in projected),
                interpolate_vector(normals, barycentric, normalize=True),
                interpolate_vector(uvs, barycentric, normalize=False),
                interpolate_influences(influences, barycentric),
            )
        transfer_positions = [tuple(position) for position in result.positions]
        transfer_normals = [tuple(normal) for normal in result.normals]
        transfer_uvs = [tuple(uv) for uv in result.uvs]
        transfer_influences = [wedges.corner_influences[index] for index in range(len(result.positions))]
        for index, values in transferred.items():
            transfer_positions[index], transfer_normals[index], transfer_uvs[index], transfer_influences[index] = values
        recombined = recombine_full_attribute_vertices(
            result.indices, result.material_ids, transfer_positions, transfer_normals, transfer_uvs, transfer_influences
        )
        compact_positions = list(recombined.positions)
        compact_normals = list(recombined.normals)
        compact_uvs = list(recombined.uvs)
        compact_influences = list(recombined.influences)
        output_indices = recombined.indices
        direct_corner_ordinals = ()
        direct_smd_payload = None
    faces = tuple(
        tuple(output_indices[offset : offset + 3]) for offset in range(0, len(output_indices), 3)
    )
    preserved_materials = tuple(mesh.materials)
    mesh.clear_geometry()
    mesh.from_pydata(compact_positions, (), faces)
    for material in preserved_materials:
        if material.name not in mesh.materials:
            mesh.materials.append(material)
    for polygon, material_id in zip(mesh.polygons, result.material_ids):
        if material_id < 0 or material_id >= len(mesh.materials):
            raise RuntimeError("export material mapping is out of range")
        polygon.material_index = int(material_id)

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        uv_layer.data[loop.index].uv = compact_uvs[loop.vertex_index]
    mesh.update()
    loop_output_normals = tuple(compact_normals[index] for face in faces for index in face)
    apply_reconstructed_smoothing(mesh, compact_positions, faces, loop_output_normals)

    if skinned:
        existing_names = {group.name for group in obj.vertex_groups}
        for group_name in group_names:
            if group_name not in existing_names:
                obj.vertex_groups.new(name=group_name)
        all_indices = list(range(len(mesh.vertices)))
        for group in obj.vertex_groups:
            group.remove(all_indices)
        groups_by_name = {group.name: group for group in obj.vertex_groups}
        for vertex_index, entries in enumerate(compact_influences):
            for group_name, weight in entries:
                if group_name == "__maximum_rigid__":
                    continue
                group = groups_by_name.get(group_name)
                if group is None:
                    group = obj.vertex_groups.new(name=group_name)
                    groups_by_name[group_name] = group
                group.add([vertex_index], weight, "REPLACE")

    return {
        "triangles_before": len(source.indices) // 3,
        "triangles_after": len(result.indices) // 3,
        "source_vertices": len(positions),
        "wedge_vertices": len(wedges.positions),
        "output_vertices": len(compact_positions),
        "locked_vertices": sum(bool(flag & LOCK) for flag in policy.vertex_flags),
        "locked_percentage": sum(bool(flag & LOCK) for flag in policy.vertex_flags) / len(policy.vertex_flags),
        "protected_vertices": sum(bool(flag & PROTECT) for flag in policy.vertex_flags),
        "priority_vertices": sum(bool(flag & PRIORITY) for flag in policy.vertex_flags),
        "requested_ratio": ratio,
        "achieved_ratio": len(result.indices) / len(source.indices),
        "is_thin": policy.geometry.is_thin,
        "skinned": skinned,
        "result_error": result.result_error,
        "strategy": candidate.strategy,
        "transfer": candidate.transfer,
        "canonical_positions": position_topology.canonical_position_count if position_topology else None,
        "open_geometric_edges": position_topology.open_edge_count if position_topology else None,
        "nonmanifold_geometric_edges": position_topology.nonmanifold_edge_count if position_topology else None,
        "uv_seam_vertices": position_topology.uv_seam_vertices if position_topology else None,
        "normal_seam_vertices": position_topology.normal_seam_vertices if position_topology else None,
        "material_seam_vertices": position_topology.material_seam_vertices if position_topology else None,
        "skin_transition_vertices": position_topology.skin_transition_vertices if position_topology else None,
        "_direct_corner_ordinals": direct_corner_ordinals,
        "_direct_smd_payload": direct_smd_payload,
    }


def require_direct_single_object(candidate: CandidateConfig, mesh_objects: Sequence[object]) -> None:
    if candidate.strategy in _DIRECT_SERIALIZER_STRATEGIES and not mesh_objects:
        raise RuntimeError(f"{candidate.strategy} requires at least one mapped source object")


def direct_degenerate_prefilter(
    candidate: CandidateConfig, original_text: str
) -> DirectDegeneratePrefilter | None:
    if candidate.direct_degenerate_prefilter is None:
        return None
    if (
        candidate.direct_degenerate_prefilter
        != "direct-degenerate-prefilter-v1"
        or candidate.strategy not in _DIRECT_SERIALIZER_STRATEGIES
    ):
        raise ValueError("direct prefilter requires a typed direct R&D strategy")
    return prefilter_direct_degenerate_smd(original_text)


def prepare_source_contract(
    candidate: CandidateConfig | None, original_text: str
) -> SourceContract:
    prefilter = (
        direct_degenerate_prefilter(candidate, original_text)
        if candidate is not None else None
    )
    effective_text = prefilter.filtered_text if prefilter is not None else original_text
    audit = audit_smd_text(effective_text)
    source_triangle_count = (
        int(prefilter.evidence["source_triangle_count"])
        if prefilter is not None else audit.triangle_count
    )
    return SourceContract(
        original_text, effective_text, audit, source_triangle_count, prefilter
    )


def _import_source_contract(
    source: Path, contract: SourceContract, source_tools: object
) -> None:
    if contract.prefilter is None:
        source_tools.import_source_file(source)
        return
    staging_dir = Path(tempfile.mkdtemp(
        prefix=".maximum-direct-prefilter-", dir=source.parent
    ))
    staged_source = staging_dir / source.name
    try:
        atomic_write_bytes(
            source.parent, staged_source, contract.effective_text.encode("utf-8")
        )
        source_tools.import_source_file(staged_source)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _describe_source_file(
    source: Path,
    source_identity: str,
    candidate: CandidateConfig | None = None,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    assert bpy is not None
    import batch_optimize_qc as source_tools

    contract = prepare_source_contract(
        candidate, source.read_text(encoding="utf-8", errors="strict")
    )
    _clear_blender_scene()
    _import_source_contract(source, contract, source_tools)
    mesh_objects = tuple(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    if not mesh_objects:
        raise RuntimeError(f"Source Tools imported no mesh from {source}")
    return _object_region_observations(
        source_identity, mesh_objects, contract.audit.materials
    )


def direct_prefilter_runtime_evidence(
    evidence: dict[str, object], *, applied: bool
) -> dict[str, object]:
    runtime = dict(evidence)
    runtime["applied"] = bool(applied)
    if not applied:
        runtime["detected_dropped_count"] = evidence["dropped_count"]
        runtime["detected_dropped_fraction"] = evidence["dropped_fraction"]
        runtime["detected_triangles"] = evidence["triangles"]
        runtime["dropped_count"] = 0
        runtime["dropped_fraction"] = 0.0
        runtime["triangles"] = []
    runtime_without_hash = {
        key: value for key, value in runtime.items() if key != "evidence_sha256"
    }
    encoded = (
        json.dumps(runtime_without_hash, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    runtime["evidence_sha256"] = hashlib.sha256(encoded).hexdigest()
    return runtime


def processed_provenance_status(
    file_metrics: dict[str, object], *, strategy: str
) -> tuple[str, str]:
    if bool(file_metrics.get("preserved_exact")):
        return (
            "preserved",
            str(file_metrics.get("fallback_reason") or "exact-source-v1"),
        )
    if strategy in _BLENDER_STRATEGIES:
        return provenance_status(file_metrics.get("fallback_reason"), strategy=strategy)
    return "optimized", strategy


def parse_adaptive_exact_preservation_payload(value: object) -> dict[str, object]:
    """Parse only the closed producer matrix; diagnostic text has no authority."""
    if type(value) is not dict or set(value) != {
        "schema", "source_identity", "kind", "preserved_exact", "reason",
    }:
        raise ValueError("adaptive exact preservation matrix fields are invalid")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("adaptive exact preservation matrix schema is invalid")
    try:
        canonical_source = normalized_source_identity(value["source_identity"])
    except (TypeError, ValueError) as exc:
        raise ValueError("adaptive exact preservation source identity is invalid") from exc
    if (
        value["source_identity"] != canonical_source
        or type(value["kind"]) is not str
        or type(value["preserved_exact"]) is not bool
        or type(value["reason"]) is not str
        or (value["kind"], value["preserved_exact"], value["reason"])
        not in _ADAPTIVE_EXACT_PRESERVATION_MATRIX
    ):
        raise ValueError("adaptive exact preservation matrix is invalid")
    return dict(value)


def adaptive_exact_preservation_payload(
    *, source_identity: str, preserved_exact: bool, ratio_preserved: bool,
    approved_fallback: bool,
) -> dict[str, object]:
    """Create one normalized adaptive-source authorization disposition.

    ``fallback_reason`` remains a human diagnostic elsewhere in the metrics file.
    It is deliberately not accepted here: the producer must retain the trusted
    control-flow state that caused exact preservation.
    """
    if any(type(value) is not bool for value in (
        preserved_exact, ratio_preserved, approved_fallback,
    )):
        raise ValueError("adaptive exact preservation state must be boolean")
    if not preserved_exact and not ratio_preserved and not approved_fallback:
        kind, reason = "ineligible-changed-v1", "adaptive-output-changed-v1"
    elif preserved_exact and ratio_preserved and not approved_fallback:
        kind, reason = "eligible-exact-v1", "ratio-preserved-exact-v1"
    elif preserved_exact and approved_fallback and not ratio_preserved:
        kind, reason = "eligible-exact-v1", "approved-exact-source-fallback-v1"
    else:
        kind, reason = "unauthorized-v1", "unproven-exact-preservation-v1"
    return parse_adaptive_exact_preservation_payload({
        "schema": 1,
        "source_identity": source_identity,
        "kind": kind,
        "preserved_exact": preserved_exact,
        "reason": reason,
    })


def bind_direct_degenerate_provenance(
    provenance: dict[str, object], file_metrics: dict[str, object]
) -> dict[str, object]:
    evidence = file_metrics.get("direct_degenerate_prefilter")
    return (
        {**provenance, "direct_degenerate_prefilter": evidence}
        if evidence is not None else dict(provenance)
    )


def _write_round_export_fallback(
    source: Path,
    destination: Path,
    candidate: CandidateConfig,
    error: BaseException,
) -> str:
    if candidate.strategy not in {
        "round-planar-priority-v1",
        "round-priority-collapse-v1",
    } or not allows_strategy_exact_fallback(
        error, strategy=candidate.strategy
    ):
        raise error
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(source.parent, destination, exact_source_payload(source.read_bytes()))
    return str(error)


def _process_source_file(
    source: Path,
    destination: Path,
    candidate: CandidateConfig,
    source_identity: str,
    region_manifest: RegionManifest,
) -> dict[str, object]:
    assert bpy is not None
    import batch_optimize_qc as source_tools

    original_text = source.read_text(encoding="utf-8", errors="strict")
    source_contract = prepare_source_contract(candidate, original_text)
    before_audit = source_contract.audit
    direct_prefilter = source_contract.prefilter
    _clear_blender_scene()
    _import_source_contract(source, source_contract, source_tools)
    mesh_objects = tuple(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    if not mesh_objects:
        raise RuntimeError(f"Source Tools imported no mesh from {source}")
    require_direct_single_object(candidate, mesh_objects)
    source_observations = _object_region_observations(
        source_identity, mesh_objects, before_audit.materials
    )
    source_manifest = manifest_for_source(region_manifest, source_identity)
    source_assignments = resolve_region_assignments(source_manifest, source_observations)
    source_overrides = tuple(
        (key, ratio) for key, ratio in candidate.region_overrides
        if key in source_manifest.by_key
    )
    source_ratios = resolve_region_ratios(
        source_manifest, source_observations, source_overrides, candidate.ratio
    )
    preserve_exact = should_preserve_exact(candidate, tuple(source_ratios.values()))
    ratio_preserved_exact = preserve_exact
    approved_exact_fallback = False
    fallback_reason: str | None = None
    if candidate.strategy in _DIRECT_SERIALIZER_STRATEGIES:
        dropped_source_triangles = frozenset(
            direct_prefilter.dropped_source_triangles
            if direct_prefilter is not None else ()
        )
        used_source_triangles: set[int] = set(dropped_source_triangles)
        for obj in mesh_objects:
            positions, triangles, loop_normals, loop_uvs, material_ids, vertex_influences = _vertex_source_attributes(obj)
            datablock_names = tuple(str(material.name) if material else "none" for material in obj.data.materials)
            slot_identities = source_material_slot_identities(
                source_identity, before_audit.materials, datablock_names
            )
            material_names = tuple(
                before_audit.materials[int(identity.split(":", 2)[1])]
                for identity in slot_identities
            )
            mapping = map_imported_corners_to_smd(
                original_text, positions, triangles, loop_normals, loop_uvs, material_ids,
                material_names, vertex_influences,
                excluded_source_triangles=frozenset(used_source_triangles),
                dropped_source_triangles=dropped_source_triangles,
            )
            _DIRECT_SOURCE_CORNER_MAP[id(obj)] = mapping
            used_source_triangles.update(ordinal // 3 for ordinal in mapping)
    try:
        if preserve_exact:
            object_metrics = []
            for obj, observation in zip(mesh_objects, source_observations):
                obj.data.calc_loop_triangles()
                triangles = len(obj.data.loop_triangles)
                object_metrics.append({
                    "triangles_before": triangles,
                    "triangles_after": triangles,
                    "source_vertices": len(obj.data.vertices),
                    "output_vertices": len(obj.data.vertices),
                    "requested_ratio": source_ratios[observation],
                    "achieved_ratio": 1.0,
                    "strategy": candidate.strategy,
                    "transfer": "exact-source-v1",
                    "preserved_exact": True,
                    "region_key": source_assignments[observation],
                })
        else:
            object_metrics = optimize_region_objects(
                source_identity, mesh_objects, candidate, region_manifest, before_audit.materials,
            )
    except (ValueError, RuntimeError) as exc:
        if candidate.strategy not in _BLENDER_STRATEGIES or not allows_strategy_exact_fallback(
            exc, strategy=candidate.strategy
        ):
            raise
        preserve_exact = True
        ratio_preserved_exact = False
        approved_exact_fallback = True
        fallback_reason = str(exc)
        object_metrics = [
            {
                "triangles_before": None,
                "triangles_after": None,
                "source_vertices": None,
                "output_vertices": None,
                "requested_ratio": source_ratios[observation],
                "achieved_ratio": 1.0,
                "strategy": candidate.strategy,
                "transfer": "exact-source-fallback-v1",
                "preserved_exact": True,
                "fallback_reason": fallback_reason,
                "region_key": source_assignments[observation],
            }
            for observation in source_observations
        ]
    finally:
        for obj in mesh_objects:
            _DIRECT_SOURCE_CORNER_MAP.pop(id(obj), None)
    direct_corner_ordinals = tuple(
        ordinal for item in object_metrics for ordinal in item.pop("_direct_corner_ordinals", ())
    )
    direct_payloads = tuple(
        payload for item in object_metrics
        if (payload := item.pop("_direct_smd_payload", None)) is not None
    )
    print("MAXIMUM_OBJECT_METRICS " + json.dumps({
        "source": source_identity,
        "objects": object_metrics,
    }, sort_keys=True))
    destination = safe_output_path(source.parent, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if preserve_exact:
        atomic_write_bytes(source.parent, destination, exact_source_payload(source.read_bytes()))
    elif direct_payloads:
        combined = {"positions": [], "normals": [], "uvs": [], "influences": [], "indices": [], "materials": [], "source_corner_ordinals": []}
        for payload in direct_payloads:
            offset = len(combined["positions"])
            for name in ("positions", "normals", "uvs", "influences", "materials", "source_corner_ordinals"):
                combined[name].extend(payload[name])
            combined["indices"].extend(offset + index for index in payload["indices"])
        payload = combined
        serialized = serialize_direct_smd(
            original_text, payload["positions"], payload["normals"], payload["uvs"],
            payload["influences"], payload["indices"], payload["materials"],
            source_corner_ordinals=payload["source_corner_ordinals"],
            dropped_source_triangles=(
                frozenset(direct_prefilter.dropped_source_triangles)
                if direct_prefilter is not None else frozenset()
            ),
        )
        atomic_write_bytes(source.parent, destination, serialized.encode("utf-8"))
    else:
        try:
            staging_dir = Path(tempfile.mkdtemp(prefix=".maximum-export-", dir=destination.parent))
            staging = staging_dir / destination.name
            try:
                source_tools.export_source_file(staging, staging.suffix.lstrip("."))
                if not staging.is_file():
                    raise RuntimeError(f"Source Tools did not export {staging}")
                with staging.open("r+b") as stream:
                    os.fsync(stream.fileno())
                safe_output_path(source.parent, destination)
                os.replace(staging, destination)
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)
        except (OSError, ValueError, RuntimeError) as exc:
            fallback_reason = _write_round_export_fallback(
                source, destination, candidate, exc
            )
            preserve_exact = True
            for item in object_metrics:
                item["attempted_achieved_ratio"] = item.get("achieved_ratio")
                item["achieved_ratio"] = 1.0
                item["preserved_exact"] = True
                item["fallback_reason"] = fallback_reason
                item["transfer"] = "exact-source-fallback-v1"
    if not destination.is_file():
        raise RuntimeError(f"Source Tools did not export {destination}")
    raw_export_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
    raw_export_path = safe_output_path(source.parent, source.parent / "maximum_direct_raw" / destination.name)
    raw_export_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(source.parent, raw_export_path, destination.read_bytes())
    if not preserve_exact:
        restored = destination.read_text(encoding="utf-8", errors="strict")
        if not direct_payloads:
            restored = restore_smd_bone_identity(original_text, restored)
        atomic_write_bytes(source.parent, destination, restored.encode("utf-8"))
    try:
        audit_text = (
            source_contract.effective_text
            if preserve_exact and direct_prefilter is not None
            else destination.read_text(encoding="utf-8", errors="replace")
        )
        after_audit = audit_exported_smd_text(audit_text)
        validate_smd_audits(before_audit, after_audit)
    except RuntimeError as exc:
        if candidate.strategy not in _BLENDER_STRATEGIES or not allows_strategy_exact_fallback(
            exc, strategy=candidate.strategy
        ):
            raise
        preserve_exact = True
        ratio_preserved_exact = False
        approved_exact_fallback = True
        fallback_reason = str(exc)
        atomic_write_bytes(source.parent, destination, exact_source_payload(source.read_bytes()))
        audit_text = (
            source_contract.effective_text
            if direct_prefilter is not None
            else destination.read_text(encoding="utf-8", errors="replace")
        )
        after_audit = audit_exported_smd_text(audit_text)
        validate_smd_audits(before_audit, after_audit)
        for item in object_metrics:
            item["attempted_achieved_ratio"] = item.get("achieved_ratio")
            item["achieved_ratio"] = 1.0
            item["preserved_exact"] = True
            item["fallback_reason"] = fallback_reason
            item["transfer"] = "exact-source-fallback-v1"
    output_triangle_count = (
        source_contract.source_triangle_count if preserve_exact else after_audit.triangle_count
    )
    if not preserve_exact and output_triangle_count >= source_contract.source_triangle_count:
        raise RuntimeError("export did not reduce SMD triangle count")
    result = {
        "source": source.as_posix(),
        "output": destination.as_posix(),
        "raw_export_sha256": raw_export_sha256,
        "raw_export_path": raw_export_path.relative_to(source.parent).as_posix(),
        "restored_export_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "triangles_before": source_contract.source_triangle_count,
        "retained_triangles_before": before_audit.triangle_count,
        "triangles_after": output_triangle_count,
        "materials_before": list(before_audit.materials),
        "materials_after": list(after_audit.materials),
        "bones_before": list(before_audit.bones),
        "bones_after": list(after_audit.bones),
        "bone_nodes_preserved": before_audit.bone_nodes == after_audit.bone_nodes,
        "influence_bones_before": list(before_audit.influence_bones),
        "influence_bones_after": list(after_audit.influence_bones),
        "uv_bounds_before": before_audit.uv_bounds,
        "uv_bounds_after": after_audit.uv_bounds,
        "finite_normals_after": after_audit.finite_normal_count,
        "uv_seams_before": before_audit.uv_seam_positions,
        "uv_seams_after": after_audit.uv_seam_positions,
        "hard_normal_seams_before": before_audit.hard_normal_positions,
        "hard_normal_seams_after": after_audit.hard_normal_positions,
        "influence_sets_before": before_audit.influence_sets,
        "influence_sets_after": after_audit.influence_sets,
        "objects": object_metrics,
        "regions": [item["region_key"] for item in object_metrics],
        "fallback_reason": fallback_reason,
        "preserved_exact": preserve_exact,
    }
    if direct_prefilter is not None:
        result["direct_degenerate_prefilter"] = direct_prefilter_runtime_evidence(
            direct_prefilter.evidence, applied=not preserve_exact
        )
    if candidate.strategy == "blender-adaptive-v1":
        result["adaptive_exact_preservation"] = adaptive_exact_preservation_payload(
            source_identity=source_identity,
            preserved_exact=preserve_exact,
            ratio_preserved=ratio_preserved_exact,
            approved_fallback=approved_exact_fallback,
        )
    return result


def _path_is_reparse_point(path: Path) -> bool:
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_flag)


def _reject_lexical_reparse_components(path: Path) -> None:
    raw = _raw_absolute(Path(path))
    anchor = Path(raw.anchor)
    current = anchor
    components = raw.relative_to(anchor).parts
    for component in components:
        current = current / component
        if os.path.lexists(current) and _path_is_reparse_point(current):
            raise ValueError(f"lexical path traverses reparse point: {current}")


def _raw_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _ensure_secure_directory(root: Path, directory: Path) -> Path:
    raw_root = _raw_absolute(root)
    if not os.path.lexists(raw_root) or not raw_root.is_dir():
        raise ValueError(f"output root is not an existing directory: {raw_root}")
    if _path_is_reparse_point(raw_root):
        raise ValueError(f"output root cannot be a reparse point: {raw_root}")
    resolved_root = raw_root.resolve(strict=True)
    raw_directory = _raw_absolute(directory)
    try:
        relative = raw_directory.relative_to(raw_root)
    except ValueError as exc:
        raise ValueError(f"output directory escapes root: {raw_directory}") from exc
    current = raw_root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _path_is_reparse_point(current):
            raise ValueError(f"output directory traverses reparse point: {current}")
        if os.path.lexists(current) and not current.is_dir():
            raise ValueError(f"output parent is not a directory: {current}")
        if not os.path.lexists(current):
            current.mkdir()
        if _path_is_reparse_point(current):
            raise ValueError(f"output directory became a reparse point: {current}")
        try:
            current.resolve(strict=True).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"resolved output directory escapes root: {current}") from exc
    resolved_directory = current.resolve(strict=True)
    try:
        resolved_directory.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"resolved output directory escapes root: {resolved_directory}") from exc
    return resolved_directory


def safe_output_path(root: Path, target: Path) -> Path:
    root = _raw_absolute(Path(root))
    if _path_is_reparse_point(root):
        raise ValueError("output root cannot be a reparse point")
    resolved_root = root.resolve(strict=True)
    target = Path(target)
    if not target.is_absolute():
        target = root / target
    target = _raw_absolute(target)
    resolved_parent = _ensure_secure_directory(root, target.parent)
    try:
        target.relative_to(root)
        resolved_parent.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"output path escapes root: {target}") from exc
    if os.path.lexists(target) and _path_is_reparse_point(target):
        raise ValueError(f"output path cannot be a reparse point: {target}")
    resolved_target = resolved_parent / target.name
    if os.path.lexists(target):
        try:
            target.resolve(strict=True).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"resolved output path escapes root: {target}") from exc
    return resolved_target


def atomic_write_bytes(root: Path, target: Path, data: bytes) -> Path:
    target = safe_output_path(root, target)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe_output_path(root, target)
        os.replace(temporary, target)
        return target
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def write_source_region_manifest(root: Path) -> Path:
    """Build shared region identities without simplifying or rewriting source files."""
    root = Path(root).resolve()
    qcs = tuple(
        path for path in sorted(root.rglob("*.qc"), key=lambda item: item.as_posix().casefold())
        if not path.name.casefold().endswith("_opt.qc")
        and "output" not in {part.casefold() for part in path.parts}
    )
    if not qcs:
        raise ValueError(f"no source QC files found under {root}")
    visual_sources: dict[str, Path] = {}
    source_occurrences: dict[str, list[dict[str, object]]] = defaultdict(list)
    for qc in qcs:
        graph = parse_qc_graph(qc, root)
        reject_unsupported_dmx(graph)
        for reference in graph.references:
            if reference.role != "visual":
                continue
            identity = normalized_source_identity(reference.source_path.relative_to(root).as_posix())
            existing = visual_sources.get(identity)
            if existing is not None and existing != reference.source_path:
                raise ValueError(f"ambiguous logical visual source identity: {identity}")
            visual_sources[identity] = reference.source_path
            occurrence = {
                "graph_file": reference.graph_file.relative_to(root).as_posix(),
                "directive": reference.directive,
                "line": reference.line,
                "logical_path": identity,
            }
            if occurrence not in source_occurrences[identity]:
                source_occurrences[identity].append(occurrence)
    observations = tuple(
        observation
        for identity, source in sorted(visual_sources.items())
        for observation in _describe_source_file(source, identity)
    )
    manifest = build_region_manifest(observations, occurrences=source_occurrences)
    destination = root / "maximum_region_manifest.json"
    atomic_write_bytes(
        root,
        destination,
        (json.dumps(manifest.to_payload(), indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )
    return destination


def run_blender(settings: Settings) -> dict[str, object]:
    assert bpy is not None
    import os
    import batch_optimize_qc as source_tools

    if settings.candidate.engine == "meshoptimizer":
        if settings.meshopt_dll is None or not settings.meshopt_dll.is_file():
            raise ValueError(f"meshopt DLL does not exist: {settings.meshopt_dll}")
        os.environ["MAXIMUM_MESHOPT_DLL"] = str(settings.meshopt_dll)
    source_tools.ensure_source_tools_enabled()

    qcs = tuple(
        path for path in sorted(settings.root.rglob("*.qc"), key=lambda item: item.as_posix().casefold())
        if not path.name.casefold().endswith("_opt.qc") and "output" not in {part.casefold() for part in path.parts}
    )
    if not qcs:
        raise ValueError(f"no source QC files found under {settings.root}")

    parsed_graphs: list[tuple[Path, QcGraph]] = []
    visual_sources: dict[str, Path] = {}
    source_occurrences: dict[str, list[dict[str, object]]] = defaultdict(list)
    for qc in qcs:
        graph = parse_qc_graph(qc, settings.root)
        reject_unsupported_dmx(graph)
        if not any(reference.role == "visual" for reference in graph.references):
            raise ValueError(f"QC graph has no visual geometry: {qc}")
        parsed_graphs.append((qc, graph))
        for reference in graph.references:
            if reference.role != "visual":
                continue
            source_identity = normalized_source_identity(
                reference.source_path.relative_to(settings.root).as_posix()
            )
            existing = visual_sources.get(source_identity)
            if existing is not None and existing != reference.source_path:
                raise ValueError(f"ambiguous logical visual source identity: {source_identity}")
            visual_sources[source_identity] = reference.source_path
            occurrence = {
                "graph_file": reference.graph_file.relative_to(settings.root).as_posix(),
                "directive": reference.directive,
                "line": reference.line,
                "logical_path": source_identity,
            }
            if occurrence not in source_occurrences[source_identity]:
                source_occurrences[source_identity].append(occurrence)

    observations = tuple(
        observation
        for source_identity, source in sorted(visual_sources.items())
        for observation in _describe_source_file(
            source, source_identity, settings.candidate
        )
    )
    region_manifest = build_region_manifest(
        observations, occurrences=source_occurrences
    )
    region_manifest_path = settings.root / "maximum_region_manifest.json"
    atomic_write_bytes(
        settings.root,
        region_manifest_path,
        (json.dumps(region_manifest.to_payload(), indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )

    files: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    processed: dict[Path, tuple[Path, dict[str, object]]] = {}
    matched_regions: Counter[str] = Counter()
    for qc, graph in parsed_graphs:
        optimized_sources: dict[Path, Path] = {}
        for reference in graph.references:
            source_hash = hashlib.sha256(reference.source_path.read_bytes()).hexdigest()
            if reference.role == "visual":
                if reference.source_path not in processed:
                    destination = reference.source_path.parent / "output" / f"{reference.source_path.stem}_opt.smd"
                    source_identity = normalized_source_identity(
                        reference.source_path.relative_to(settings.root).as_posix()
                    )
                    item = _process_source_file(
                        reference.source_path,
                        destination,
                        settings.candidate,
                        source_identity,
                        region_manifest,
                    )
                    processed[reference.source_path] = (destination, item)
                    files.append(item)
                    for key in item["regions"]:
                        if key in dict(settings.candidate.region_overrides):
                            matched_regions[key] += 1
                destination, _item = processed[reference.source_path]
                optimized_sources[reference.source_path] = destination
                status, reason = processed_provenance_status(
                    _item, strategy=settings.candidate.strategy
                )
                output_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
            else:
                status = "preserved"
                reason = "physics-pipeline-not-enabled" if reference.role == "collision" else "animation-preserved"
                destination = reference.source_path
                output_hash = source_hash
            provenance_item: dict[str, object] = {
                    "graph_file": reference.graph_file.relative_to(settings.root).as_posix(),
                    "directive": reference.directive,
                    "line": reference.line,
                    "logical_path": reference.logical_path,
                    "role": reference.role,
                    "status": status,
                    "reason": reason,
                    "source_sha256": source_hash,
                    "output": destination.relative_to(settings.root).as_posix(),
                    "output_sha256": output_hash,
                }
            if reference.role == "visual":
                provenance_item = bind_direct_degenerate_provenance(
                    provenance_item, _item
                )
            provenance.append(provenance_item)
        for output_path, text in rewritten_qc_graph_texts(graph, optimized_sources).items():
            atomic_write_bytes(settings.root, output_path, text.encode("utf-8"))

    override_keys = {key for key, _ratio in settings.candidate.region_overrides}
    missing_overrides = sorted(key for key in override_keys if matched_regions[key] == 0)
    ambiguous_overrides = sorted(key for key in override_keys if matched_regions[key] > 1)
    if missing_overrides or ambiguous_overrides:
        raise ValueError(
            f"unknown or ambiguous region overrides: missing={missing_overrides}, ambiguous={ambiguous_overrides}"
        )

    before = sum(int(item["triangles_before"]) for item in files)
    after = sum(int(item["triangles_after"]) for item in files)
    engine = engine_evidence(
        settings.candidate.engine, settings.candidate.strategy, tuple(bpy.app.version)
    )
    metrics = {
        "schema_version": 1,
        "candidate_id": settings.candidate.candidate_id,
        **engine,
        "triangles_before": before,
        "triangles_after": after,
        "triangle_reduction_ratio": (before - after) / before if before else 0.0,
        "attribute_repair_failures": 0,
        "files": files,
        "provenance": provenance,
        "region_manifest": region_manifest_path.relative_to(settings.root).as_posix(),
        "region_manifest_sha256": hashlib.sha256(region_manifest_path.read_bytes()).hexdigest(),
    }
    metrics_path = settings.root / "candidate_metrics.json"
    atomic_write_bytes(
        settings.root,
        metrics_path,
        (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
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
