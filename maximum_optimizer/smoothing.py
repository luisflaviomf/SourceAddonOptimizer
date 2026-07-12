from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Sequence


Float3 = tuple[float, float, float]
Edge = tuple[int, int]


@dataclass(frozen=True)
class SmoothingReconstruction:
    smooth_faces: tuple[bool, ...]
    sharp_edges: frozenset[Edge]
    loop_normals: tuple[Float3, ...]


def _float3(values: Sequence[float], label: str) -> Float3:
    if len(values) != 3:
        raise ValueError(f"{label} must be a float3")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} must be finite")
    return result  # type: ignore[return-value]


def _position_key(position: Float3) -> tuple[bytes, bytes, bytes]:
    return tuple(struct.pack(">d", value) for value in position)  # type: ignore[return-value]


def _normal_matches(first: Float3, second: Float3, tolerance: float) -> bool:
    return sum((a - b) ** 2 for a, b in zip(first, second)) <= tolerance * tolerance


def canonicalize_export_normals(
    normals: Sequence[Sequence[float]], *, digits: int = 6
) -> tuple[Float3, ...]:
    """Collapse float noise below SMD's serialized precision before Blender packing."""
    if type(digits) is not int or digits < 1 or digits > 9:
        raise ValueError("normal precision digits are out of range")
    result = []
    for values in normals:
        rounded = tuple(round(value, digits) for value in _float3(values, "normal"))
        length = math.sqrt(sum(value * value for value in rounded))
        if length <= 1e-20 or not math.isfinite(length):
            raise ValueError("normal must be non-zero")
        result.append(tuple(value / length for value in rounded))
    return tuple(result)  # type: ignore[return-value]


def canonicalize_normals_by_identity(
    normals: Sequence[Sequence[float]], identities: Sequence[object]
) -> tuple[Float3, ...]:
    """Reuse one imported normal for copies of the same source SMD normal key."""
    if len(normals) != len(identities):
        raise ValueError("normal identities must match loop normals")
    representatives: dict[object, Float3] = {}
    result = []
    for values, identity in zip(normals, identities):
        try:
            existing = representatives.get(identity)
        except TypeError as exc:
            raise ValueError("normal identity must be hashable") from exc
        normal = _float3(values, "normal")
        if existing is None:
            representatives[identity] = normal
            existing = normal
        result.append(existing)
    return tuple(result)


def reconstruct_smoothing(
    positions: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    loop_normals: Sequence[Sequence[float]],
    *,
    normal_tolerance: float = 1e-6,
) -> SmoothingReconstruction:
    """Recover Blender shading metadata without changing the mesh payload.

    Adjacency is keyed by exact positions rather than vertex ids, so appearance seams
    remain connected for smoothing analysis. Only a discontinuity in intended corner
    normals creates a crease. Open and non-manifold edges are marked sharp fail-closed.
    """
    if not math.isfinite(normal_tolerance) or normal_tolerance < 0.0:
        raise ValueError("normal tolerance must be finite and non-negative")
    converted_positions = tuple(_float3(value, "position") for value in positions)
    converted_normals = tuple(_float3(value, "normal") for value in loop_normals)
    if len(converted_normals) != len(faces) * 3:
        raise ValueError("loop normals must contain three corners per face")
    for normal in converted_normals:
        length = math.sqrt(sum(value * value for value in normal))
        if length <= 1e-20:
            raise ValueError("normal must be non-zero")

    loop_normals: list[Float3] = []
    smooth_faces: list[bool] = []
    edge_uses: dict[
        tuple[tuple[bytes, bytes, bytes], tuple[bytes, bytes, bytes]],
        list[tuple[Edge, dict[tuple[bytes, bytes, bytes], Float3]]],
    ] = {}
    fail_closed_edges: set[Edge] = set()
    for face_index, face in enumerate(faces):
        if len(face) != 3:
            raise ValueError("smoothing reconstruction requires triangles")
        indices = tuple(int(index) for index in face)
        if any(index < 0 or index >= len(converted_positions) for index in indices):
            raise ValueError("smoothing face index is out of range")
        intended = converted_normals[face_index * 3 : face_index * 3 + 3]
        loop_normals.extend(intended)
        # Blender Source Tools exports custom split normals only from polygons marked
        # smooth. This is an exporter-consumption flag, not a global shading policy:
        # exact per-loop normals and sharp edges retain flat faces and true creases.
        smooth_faces.append(True)
        for start_corner, end_corner in ((0, 1), (1, 2), (2, 0)):
            start, end = indices[start_corner], indices[end_corner]
            start_key = _position_key(converted_positions[start])
            end_key = _position_key(converted_positions[end])
            topological = (min(start, end), max(start, end))
            if start_key == end_key:
                fail_closed_edges.add(topological)
                continue
            canonical = tuple(sorted((start_key, end_key)))
            edge_uses.setdefault(canonical, []).append(
                (topological, {start_key: intended[start_corner], end_key: intended[end_corner]})
            )

    sharp_edges: set[Edge] = set(fail_closed_edges)
    for uses in edge_uses.values():
        if len(uses) != 2:
            sharp_edges.update(edge for edge, _normals in uses)
            continue
        (_first_edge, first_normals), (_second_edge, second_normals) = uses
        continuous = first_normals.keys() == second_normals.keys() and all(
            _normal_matches(first_normals[key], second_normals[key], normal_tolerance)
            for key in first_normals
        )
        if not continuous:
            sharp_edges.update(edge for edge, _normals in uses)

    return SmoothingReconstruction(
        tuple(smooth_faces), frozenset(sharp_edges), tuple(loop_normals)
    )


def apply_reconstructed_smoothing(
    mesh: object,
    positions: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    loop_normals: Sequence[Sequence[float]],
) -> SmoothingReconstruction:
    """Apply deterministic face, edge and custom loop-normal state to a rebuilt mesh."""
    result = reconstruct_smoothing(positions, faces, loop_normals)
    polygons = mesh.polygons  # type: ignore[attr-defined]
    edges = mesh.edges  # type: ignore[attr-defined]
    loops = mesh.loops  # type: ignore[attr-defined]
    if len(polygons) != len(result.smooth_faces) or len(loops) != len(result.loop_normals):
        raise ValueError("rebuilt Blender mesh does not match smoothing topology")
    for polygon, use_smooth in zip(polygons, result.smooth_faces):
        polygon.use_smooth = use_smooth
    for edge in edges:
        edge.use_edge_sharp = tuple(sorted(int(index) for index in edge.vertices)) in result.sharp_edges
    mesh.normals_split_custom_set(result.loop_normals)  # type: ignore[attr-defined]
    mesh.update()  # type: ignore[attr-defined]
    return result
