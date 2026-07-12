from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Sequence


Influences = tuple[tuple[str, float], ...]


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
) -> WedgeMesh:
    corner_count = len(triangles) * 3
    if len(loop_normals) != corner_count or len(loop_uvs) != corner_count:
        raise ValueError("loop attributes must contain three corners per triangle")
    if len(material_ids) != len(triangles) or len(vertex_influences) != len(positions):
        raise ValueError("mesh attribute counts do not match topology")
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
                out_source_loops.append(loop_index)
                out_influences.append(influences)
            indices.append(wedge)
    return WedgeMesh(
        tuple(out_positions), tuple(out_normals), tuple(out_uvs), tuple(out_weights),
        tuple(out_bones), tuple(indices), tuple(int(value) for value in material_ids),
        bone_names, tuple(out_source_vertices), tuple(out_source_loops), tuple(out_influences),
    )
