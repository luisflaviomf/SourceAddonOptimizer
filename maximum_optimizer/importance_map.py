from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Sequence
from types import MappingProxyType
from collections.abc import Mapping


Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
SkinSignature = tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class MeshImportanceInput:
    positions: tuple[Vec3, ...]
    faces: tuple[tuple[int, int, int], ...]
    face_materials: tuple[int, ...]
    corner_uvs: tuple[tuple[Vec2, Vec2, Vec2], ...]
    skin_signatures: tuple[SkinSignature, ...]


@dataclass(frozen=True)
class ImportanceMap:
    weights: tuple[float, ...]
    reasons: tuple[tuple[str, ...], ...]
    reason_weights: Mapping[str, float]

    @property
    def protected_vertices(self) -> tuple[int, ...]:
        return tuple(index for index, weight in enumerate(self.weights) if weight > 0.0)


def _projected_hull(points: Sequence[tuple[float, float, int]]) -> tuple[int, ...]:
    # Collapse coincident projections to the lowest source index. This makes a
    # silhouette repeatable without depending on Blender iteration order.
    unique: dict[tuple[float, float], int] = {}
    for x, y, index in points:
        unique[(x, y)] = min(index, unique.get((x, y), index))
    ordered = sorted((x, y, index) for (x, y), index in unique.items())
    if len(ordered) <= 2:
        return tuple(item[2] for item in ordered)

    def cross(a: tuple[float, float, int], b: tuple[float, float, int], c: tuple[float, float, int]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    lower: list[tuple[float, float, int]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float, int]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple(sorted({point[2] for point in lower[:-1] + upper[:-1]}))


def _face_normal(positions: Sequence[Vec3], face: tuple[int, int, int]) -> Vec3:
    a, b, c = (positions[index] for index in face)
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(value * value for value in normal))
    return (0.0, 0.0, 0.0) if length == 0.0 else tuple(value / length for value in normal)  # type: ignore[return-value]


def _validate(mesh: MeshImportanceInput) -> None:
    count = len(mesh.positions)
    if len(mesh.face_materials) != len(mesh.faces):
        raise ValueError("face material count does not match faces")
    if len(mesh.corner_uvs) != len(mesh.faces) or any(len(item) != 3 for item in mesh.corner_uvs):
        raise ValueError("corner UV payload does not match triangular faces")
    if len(mesh.skin_signatures) != count:
        raise ValueError("skin signature count does not match vertices")
    for position in mesh.positions:
        if len(position) != 3 or not all(math.isfinite(value) for value in position):
            raise ValueError("positions must be finite 3D values")
    for face in mesh.faces:
        if len(face) != 3 or len(set(face)) != 3 or any(index < 0 or index >= count for index in face):
            raise ValueError("faces must be non-degenerate valid triangles")


def build_importance_weights(
    mesh: MeshImportanceInput, *, crease_degrees: float = 45.0
) -> ImportanceMap:
    """Classify vertices whose collapse is likely to change visible boundaries.

    The map is intentionally binary in v1. Blender's modifier still controls the
    global ratio; this map only identifies geometry/appearance constraints.
    """
    _validate(mesh)
    if not 0.0 <= crease_degrees <= 180.0 or not math.isfinite(crease_degrees):
        raise ValueError("crease_degrees is out of range")
    reasons: list[set[str]] = [set() for _ in mesh.positions]

    projections = (
        tuple((p[0], p[1], i) for i, p in enumerate(mesh.positions)),
        tuple((p[0], p[2], i) for i, p in enumerate(mesh.positions)),
        tuple((p[1], p[2], i) for i, p in enumerate(mesh.positions)),
    )
    for projection in projections:
        for index in _projected_hull(projection):
            reasons[index].add("projected-silhouette")

    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    vertex_uvs: list[set[Vec2]] = [set() for _ in mesh.positions]
    normals = tuple(_face_normal(mesh.positions, face) for face in mesh.faces)
    for face_index, face in enumerate(mesh.faces):
        for corner, vertex in enumerate(face):
            vertex_uvs[vertex].add(tuple(float(v) for v in mesh.corner_uvs[face_index][corner]))
            edge_faces[tuple(sorted((vertex, face[(corner + 1) % 3])))].append(face_index)

    crease_cos = math.cos(math.radians(crease_degrees))
    for edge, adjacent in edge_faces.items():
        if len(adjacent) == 1:
            for vertex in edge:
                reasons[vertex].add("open-border")
        elif len(adjacent) == 2:
            left, right = adjacent
            if mesh.face_materials[left] != mesh.face_materials[right]:
                for vertex in edge:
                    reasons[vertex].add("material-boundary")
            dot = sum(a * b for a, b in zip(normals[left], normals[right]))
            if dot < crease_cos:
                for vertex in edge:
                    reasons[vertex].add("hard-crease")
        else:
            for vertex in edge:
                reasons[vertex].add("non-manifold")
        if mesh.skin_signatures[edge[0]] != mesh.skin_signatures[edge[1]]:
            for vertex in edge:
                reasons[vertex].add("skin-transition")

    for index, uvs in enumerate(vertex_uvs):
        if len(uvs) > 1:
            reasons[index].add("uv-boundary")

    reason_weights = MappingProxyType({
        "projected-silhouette": 1.0,
        "open-border": 1.0,
        "material-boundary": 1.0,
        "skin-transition": 1.0,
        "non-manifold": 1.0,
        "uv-boundary": 0.5,
        "hard-crease": 0.5,
    })
    ordered = tuple(tuple(sorted(items)) for items in reasons)
    return ImportanceMap(
        weights=tuple(max((reason_weights[item] for item in items), default=0.0) for items in ordered),
        reasons=ordered,
        reason_weights=reason_weights,
    )
