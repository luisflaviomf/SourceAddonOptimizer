from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from collections import defaultdict
from typing import Sequence


Influences = tuple[tuple[str, float], ...]
LOCK = 1 << 0
PROTECT = 1 << 1


@dataclass(frozen=True)
class PositionTopology:
    canonical_position_ids: tuple[int, ...]
    vertex_flags: tuple[int, ...]
    canonical_position_count: int
    open_edge_count: int
    nonmanifold_edge_count: int
    locked_vertices: int
    protected_vertices: int
    uv_seam_vertices: int
    normal_seam_vertices: int
    material_seam_vertices: int
    skin_transition_vertices: int


def _float32_signature(values: Sequence[float], width: int) -> bytes:
    if len(values) != width or not all(math.isfinite(float(value)) for value in values):
        raise ValueError(f"attribute must contain finite float{width} values")
    words = []
    for value in values:
        word = struct.unpack("=I", struct.pack("=f", float(value)))[0]
        words.append(0 if word == 0x80000000 else word)
    return struct.pack("=" + "I" * width, *words)


def classify_position_topology(
    positions: Sequence[Sequence[float]],
    normals: Sequence[Sequence[float]],
    uvs: Sequence[Sequence[float]],
    indices: Sequence[int],
    material_ids: Sequence[int],
    weights: Sequence[Sequence[float]],
    bone_indices: Sequence[Sequence[int]],
    base_flags: Sequence[int] | None = None,
) -> PositionTopology:
    """Classify geometry on exact float32 positions, independently from wedge seams."""
    vertex_count = len(positions)
    if vertex_count == 0 or len(indices) < 3 or len(indices) % 3:
        raise ValueError("position topology requires a non-empty triangle mesh")
    if not (
        len(normals) == len(uvs) == len(weights) == len(bone_indices) == vertex_count
        and len(material_ids) == len(indices) // 3
    ):
        raise ValueError("position topology attribute counts do not match")
    if base_flags is None:
        base_flags = (0,) * vertex_count
    if len(base_flags) != vertex_count or any(type(flag) is not int or flag < 0 or flag > 0xff for flag in base_flags):
        raise ValueError("position topology base flags are invalid")
    if any(type(index) is not int or index < 0 or index >= vertex_count for index in indices):
        raise ValueError("position topology index is out of range")
    if any(type(material) is not int or material < 0 for material in material_ids):
        raise ValueError("position topology material is invalid")

    representative_by_position: dict[bytes, int] = {}
    canonical_ids: list[int] = []
    wedges_by_position: dict[int, list[int]] = defaultdict(list)
    for vertex, position in enumerate(positions):
        signature = _float32_signature(position, 3)
        representative = representative_by_position.setdefault(signature, vertex)
        canonical_ids.append(representative)
        wedges_by_position[representative].append(vertex)

    edge_owners: dict[tuple[int, int], list[int]] = defaultdict(list)
    triangle_vertices: list[tuple[int, int, int]] = []
    for triangle in range(len(material_ids)):
        raw = tuple(indices[triangle * 3 : triangle * 3 + 3])
        canonical = tuple(canonical_ids[index] for index in raw)
        triangle_vertices.append(raw)  # type: ignore[arg-type]
        if len(set(canonical)) != 3:
            for position in set(canonical):
                for wedge in wedges_by_position[position]:
                    base = list(base_flags)
                    base[wedge] |= LOCK
                    base_flags = tuple(base)
            continue
        for first, second in ((canonical[0], canonical[1]), (canonical[1], canonical[2]), (canonical[2], canonical[0])):
            edge_owners[(min(first, second), max(first, second))].append(triangle)

    flags = list(base_flags)
    open_edges = 0
    nonmanifold_edges = 0
    for edge, owners in edge_owners.items():
        if len(owners) == 1:
            open_edges += 1
        elif len(owners) > 2:
            nonmanifold_edges += 1
        else:
            continue
        for position in edge:
            for wedge in wedges_by_position[position]:
                flags[wedge] |= LOCK

    incident_materials: dict[int, set[int]] = defaultdict(set)
    for triangle, raw in enumerate(triangle_vertices):
        for wedge in raw:
            incident_materials[canonical_ids[wedge]].add(material_ids[triangle])

    uv_positions: set[int] = set()
    normal_positions: set[int] = set()
    material_positions = {position for position, values in incident_materials.items() if len(values) > 1}
    for position, wedges in wedges_by_position.items():
        if len({_float32_signature(uvs[wedge], 2) for wedge in wedges}) > 1:
            uv_positions.add(position)
        if len({_float32_signature(normals[wedge], 3) for wedge in wedges}) > 1:
            normal_positions.add(position)

    def influence_signature(vertex: int) -> tuple[tuple[int, bytes], ...]:
        if len(weights[vertex]) != 4 or len(bone_indices[vertex]) != 4:
            raise ValueError("skin attributes must contain four slots")
        result = []
        for bone, weight in zip(bone_indices[vertex], weights[vertex]):
            if type(bone) is not int or bone < 0 or not math.isfinite(float(weight)):
                raise ValueError("skin attribute is invalid")
            packed = _float32_signature((weight,), 1)
            if float(weight) > 0.0:
                result.append((bone, packed))
        return tuple(sorted(result))

    skin_positions: set[int] = set()
    for position, wedges in wedges_by_position.items():
        signatures = {influence_signature(wedge) for wedge in wedges}
        if len(signatures) > 1:
            skin_positions.add(position)

    for position in uv_positions | normal_positions | material_positions | skin_positions:
        for wedge in wedges_by_position[position]:
            flags[wedge] |= PROTECT
    return PositionTopology(
        tuple(canonical_ids), tuple(flags), len(wedges_by_position), open_edges, nonmanifold_edges,
        sum(bool(flag & LOCK) for flag in flags), sum(bool(flag & PROTECT) for flag in flags),
        sum(len(wedges_by_position[position]) for position in uv_positions),
        sum(len(wedges_by_position[position]) for position in normal_positions),
        sum(len(wedges_by_position[position]) for position in material_positions),
        sum(len(wedges_by_position[position]) for position in skin_positions),
    )


@dataclass(frozen=True)
class WedgeMesh:
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    weights: tuple[tuple[float, float, float, float], ...]
    bone_indices: tuple[tuple[int, int, int, int], ...]
    indices: tuple[int, ...]
    material_ids: tuple[int, ...]
    bone_names: tuple[str, ...]
    source_vertex_indices: tuple[int, ...]
    source_loop_indices: tuple[int, ...]
    corner_influences: tuple[Influences, ...]


@dataclass(frozen=True)
class RecombinedMesh:
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    influences: tuple[Influences, ...]
    indices: tuple[int, ...]


def recombine_full_attribute_vertices(
    indices: Sequence[int],
    material_ids: Sequence[int],
    positions: Sequence[Sequence[float]],
    normals: Sequence[Sequence[float]],
    uvs: Sequence[Sequence[float]],
    influences: Sequence[Influences],
) -> RecombinedMesh:
    if len(indices) % 3 or len(material_ids) != len(indices) // 3:
        raise ValueError("recombine topology/material counts do not match")
    by_key: dict[tuple[object, ...], int] = {}
    out_positions: list[tuple[float, float, float]] = []
    out_normals: list[tuple[float, float, float]] = []
    out_uvs: list[tuple[float, float]] = []
    out_influences: list[Influences] = []
    out_indices: list[int] = []
    for corner, source in enumerate(indices):
        if source < 0 or source >= len(positions) or not (
            len(normals) == len(uvs) == len(influences) == len(positions)
        ):
            raise ValueError("recombine source vertex is invalid")
        material = int(material_ids[corner // 3])
        key = (
            _exact_float_signature(positions[source]),
            _exact_float_signature(normals[source]),
            _exact_float_signature(uvs[source]),
            tuple(
                (name, struct.pack(">d", float(weight)))
                for name, weight in influences[source]
            ),
            material,
        )
        target = by_key.get(key)
        if target is None:
            target = len(out_positions)
            by_key[key] = target
            out_positions.append(tuple(float(value) for value in positions[source]))  # type: ignore[arg-type]
            out_normals.append(tuple(float(value) for value in normals[source]))  # type: ignore[arg-type]
            out_uvs.append(tuple(float(value) for value in uvs[source]))  # type: ignore[arg-type]
            out_influences.append(influences[source])
        out_indices.append(target)
    return RecombinedMesh(
        tuple(out_positions), tuple(out_normals), tuple(out_uvs), tuple(out_influences), tuple(out_indices)
    )


def normalize_influences(values: Sequence[tuple[str, float]]) -> Influences:
    combined: dict[str, float] = {}
    for name, weight in values:
        if type(name) is not str or not name or not math.isfinite(float(weight)):
            raise ValueError("bone influence is invalid")
        clamped = min(1.0, max(0.0, float(weight)))
        if clamped:
            combined[name] = combined.get(name, 0.0) + clamped
    selected = sorted(combined.items(), key=lambda item: (-item[1], item[0]))[:4]
    total = sum(weight for _name, weight in selected)
    if total <= 1e-12:
        raise ValueError("zero-sum skin weights")
    return tuple((name, weight / total) for name, weight in selected)


def interpolate_influences(
    corners: Sequence[Influences], barycentric: Sequence[float]
) -> Influences:
    if len(corners) != 3 or len(barycentric) != 3:
        raise ValueError("bone interpolation requires three corners")
    combined: dict[str, float] = {}
    for corner, factor in zip(corners, barycentric):
        if not math.isfinite(float(factor)):
            raise ValueError("non-finite barycentric weight")
        for name, weight in corner:
            combined[name] = combined.get(name, 0.0) + float(factor) * weight
    return normalize_influences(tuple(combined.items()))


def barycentric_weights(
    point: Sequence[float],
    first: Sequence[float],
    second: Sequence[float],
    third: Sequence[float],
) -> tuple[float, float, float]:
    v0 = tuple(second[i] - first[i] for i in range(3))
    v1 = tuple(third[i] - first[i] for i in range(3))
    v2 = tuple(point[i] - first[i] for i in range(3))
    d00 = sum(value * value for value in v0)
    d01 = sum(v0[i] * v1[i] for i in range(3))
    d11 = sum(value * value for value in v1)
    d20 = sum(v2[i] * v0[i] for i in range(3))
    d21 = sum(v2[i] * v1[i] for i in range(3))
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-20:
        raise ValueError("degenerate triangle")
    second_weight = (d11 * d20 - d01 * d21) / denominator
    third_weight = (d00 * d21 - d01 * d20) / denominator
    return 1.0 - second_weight - third_weight, second_weight, third_weight


def interpolate_vector(
    corners: Sequence[Sequence[float]], barycentric: Sequence[float], *, normalize: bool
) -> tuple[float, ...]:
    if len(corners) != 3 or len(barycentric) != 3 or not corners:
        raise ValueError("vector interpolation requires three corners")
    width = len(corners[0])
    if any(len(corner) != width for corner in corners):
        raise ValueError("corner vector width mismatch")
    result = tuple(
        sum(float(barycentric[index]) * float(corners[index][component]) for index in range(3))
        for component in range(width)
    )
    if normalize:
        length = math.sqrt(sum(value * value for value in result))
        if length <= 1e-12 or not math.isfinite(length):
            raise ValueError("zero-length interpolated vector")
        result = tuple(value / length for value in result)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("non-finite interpolated vector")
    return result


def _rounded(values: Sequence[float]) -> tuple[float, ...]:
    return tuple(round(float(value), 9) for value in values)


def _exact_float_signature(values: Sequence[float]) -> tuple[bytes, ...]:
    return tuple(struct.pack(">d", float(value)) for value in values)


def build_wedge_mesh(
    positions: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    loop_normals: Sequence[Sequence[float]],
    loop_uvs: Sequence[Sequence[float]],
    material_ids: Sequence[int],
    vertex_influences: Sequence[Sequence[tuple[str, float]]],
    *,
    exact_float32: bool = False,
    source_corner_indices: Sequence[int] | None = None,
) -> WedgeMesh:
    corner_count = len(triangles) * 3
    if len(loop_normals) != corner_count or len(loop_uvs) != corner_count:
        raise ValueError("loop attributes must contain three corners per triangle")
    if len(material_ids) != len(triangles) or len(vertex_influences) != len(positions):
        raise ValueError("mesh attribute counts do not match topology")
    if source_corner_indices is not None and (
        len(source_corner_indices) != corner_count
        or any(type(value) is not int or value < 0 for value in source_corner_indices)
    ):
        raise ValueError("source corner mapping does not match topology")
    normalized_influences = tuple(normalize_influences(values) for values in vertex_influences)
    bone_names = tuple(sorted({name for influences in normalized_influences for name, _weight in influences}))
    bone_ids = {name: index for index, name in enumerate(bone_names)}
    wedges: dict[tuple[object, ...], int] = {}
    out_positions: list[tuple[float, float, float]] = []
    out_normals: list[tuple[float, float, float]] = []
    out_uvs: list[tuple[float, float]] = []
    out_weights: list[tuple[float, float, float, float]] = []
    out_bones: list[tuple[int, int, int, int]] = []
    out_source_vertices: list[int] = []
    out_source_loops: list[int] = []
    out_influences: list[Influences] = []
    indices: list[int] = []

    for triangle_index, triangle in enumerate(triangles):
        if len(triangle) != 3:
            raise ValueError("triangles must have three corners")
        material = material_ids[triangle_index]
        for corner, source_vertex in enumerate(triangle):
            if type(source_vertex) is not int or source_vertex < 0 or source_vertex >= len(positions):
                raise ValueError("triangle index is out of range")
            loop_index = triangle_index * 3 + corner
            position = tuple(float(value) for value in positions[source_vertex])
            normal = tuple(float(value) for value in loop_normals[loop_index])
            uv = tuple(float(value) for value in loop_uvs[loop_index])
            influences = normalized_influences[source_vertex]
            if exact_float32:
                signature = tuple((name, _float32_signature((weight,), 1)) for name, weight in influences)
                key = (
                    _float32_signature(position, 3), _float32_signature(normal, 3),
                    _float32_signature(uv, 2), int(material), signature,
                )
            else:
                signature = tuple((name, round(weight, 9)) for name, weight in influences)
                key = (_rounded(position), _rounded(normal), _rounded(uv), int(material), signature)
            wedge = wedges.get(key)
            if wedge is None:
                wedge = len(out_positions)
                wedges[key] = wedge
                out_positions.append(position)  # type: ignore[arg-type]
                out_normals.append(normal)  # type: ignore[arg-type]
                out_uvs.append(uv)  # type: ignore[arg-type]
                weights = tuple(weight for _name, weight in influences) + (0.0,) * (4 - len(influences))
                bones = tuple(bone_ids[name] for name, _weight in influences) + (0,) * (4 - len(influences))
                out_weights.append(weights)  # type: ignore[arg-type]
                out_bones.append(bones)  # type: ignore[arg-type]
                out_source_vertices.append(source_vertex)
                out_source_loops.append(
                    source_corner_indices[loop_index] if source_corner_indices is not None else loop_index
                )
                out_influences.append(influences)
            indices.append(wedge)
    return WedgeMesh(
        tuple(out_positions), tuple(out_normals), tuple(out_uvs), tuple(out_weights),
        tuple(out_bones), tuple(indices), tuple(int(value) for value in material_ids),
        bone_names, tuple(out_source_vertices), tuple(out_source_loops), tuple(out_influences),
    )
