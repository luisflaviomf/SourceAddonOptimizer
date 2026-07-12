from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
import struct
from typing import Sequence


@dataclass(frozen=True)
class RoundComponentDecision:
    eligible: bool
    reason: str
    axis: int | None = None
    priority_vertices: tuple[int, ...] = ()


def _reject(reason: str) -> RoundComponentDecision:
    return RoundComponentDecision(False, reason)


def _position_key(position: Sequence[float]) -> bytes:
    if len(position) != 3 or not all(math.isfinite(float(value)) for value in position):
        raise ValueError("positions must contain finite float3 rows")
    words = []
    for value in position:
        word = struct.unpack("=I", struct.pack("=f", float(value)))[0]
        words.append(0 if word == 0x80000000 else word)
    return struct.pack("=III", *words)


def _rigid_signature(values: Sequence[tuple[object, float]]) -> object | None:
    positive = [(bone, float(weight)) for bone, weight in values if float(weight) > 0.0]
    if (
        len(positive) != 1
        or not math.isfinite(positive[0][1])
        or abs(positive[0][1] - 1.0) > 1e-4
    ):
        return None
    return positive[0][0]


def _normal(
    positions: Sequence[Sequence[float]], triangle: Sequence[int]
) -> tuple[float, float, float] | None:
    a, b, c = (positions[index] for index in triangle)
    ab = tuple(float(b[axis]) - float(a[axis]) for axis in range(3))
    ac = tuple(float(c[axis]) - float(a[axis]) for axis in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(value * value for value in cross))
    if length <= 1e-12 or not math.isfinite(length):
        return None
    return tuple(value / length for value in cross)  # type: ignore[return-value]


def classify_round_component(
    positions: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    influence_signatures: Sequence[Sequence[tuple[object, float]]],
    *,
    angular_bins: int = 16,
) -> RoundComponentDecision:
    """Admit only rigid, closed, thin axial components with a circular outer band."""
    if not positions or not triangles or len(influence_signatures) != len(positions):
        raise ValueError("round component arrays are empty or mismatched")
    if type(angular_bins) is not int or angular_bins < 12 or angular_bins > 64:
        raise ValueError("angular_bins must be an integer in [12, 64]")

    canonical_by_key: dict[bytes, int] = {}
    canonical_ids: list[int] = []
    members: dict[int, list[int]] = defaultdict(list)
    for index, position in enumerate(positions):
        key = _position_key(position)
        canonical = canonical_by_key.setdefault(key, index)
        canonical_ids.append(canonical)
        members[canonical].append(index)

    rigid = tuple(_rigid_signature(values) for values in influence_signatures)
    if any(value is None for value in rigid) or len(set(rigid)) != 1:
        return _reject("not-rigid")

    canonical_triangles: list[tuple[int, int, int]] = []
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    adjacency: dict[int, set[int]] = defaultdict(set)
    for triangle in triangles:
        if (
            len(triangle) != 3
            or any(type(index) is not int or index < 0 or index >= len(positions) for index in triangle)
        ):
            raise ValueError("triangles must contain valid index triples")
        canonical = tuple(canonical_ids[index] for index in triangle)
        if len(set(canonical)) != 3:
            return _reject("degenerate-topology")
        face_index = len(canonical_triangles)
        canonical_triangles.append(canonical)  # type: ignore[arg-type]
        for first, second in (
            (canonical[0], canonical[1]),
            (canonical[1], canonical[2]),
            (canonical[2], canonical[0]),
        ):
            edge = (min(first, second), max(first, second))
            edge_faces[edge].append(face_index)
            adjacency[first].add(second)
            adjacency[second].add(first)

    canonical_vertices = set(members)
    visited = set()
    queue = deque((min(canonical_vertices),))
    while queue:
        vertex = queue.popleft()
        if vertex in visited:
            continue
        visited.add(vertex)
        queue.extend(adjacency[vertex] - visited)
    if visited != canonical_vertices:
        return _reject("disconnected")
    if any(len(owners) != 2 for owners in edge_faces.values()):
        return _reject("not-closed-manifold")

    unique_positions = {index: tuple(float(value) for value in positions[index]) for index in canonical_vertices}
    minima = tuple(min(row[axis] for row in unique_positions.values()) for axis in range(3))
    maxima = tuple(max(row[axis] for row in unique_positions.values()) for axis in range(3))
    extents = tuple(maxima[axis] - minima[axis] for axis in range(3))
    axis = min(range(3), key=lambda item: (extents[item], item))
    radial_axes = tuple(item for item in range(3) if item != axis)
    radial_extents = tuple(extents[item] for item in radial_axes)
    if (
        min(radial_extents) <= 1e-9
        or extents[axis] > max(radial_extents) * 0.5
        or min(radial_extents) / max(radial_extents) < 0.75
    ):
        return _reject("not-thin-axial")

    center = tuple((minima[item] + maxima[item]) * 0.5 for item in range(3))
    radii = {
        index: math.hypot(
            row[radial_axes[0]] - center[radial_axes[0]],
            row[radial_axes[1]] - center[radial_axes[1]],
        )
        for index, row in unique_positions.items()
    }
    maximum_radius = max(radii.values())
    outer = {index for index, radius in radii.items() if radius >= maximum_radius * 0.90}
    occupied = set()
    for index in outer:
        row = unique_positions[index]
        angle = math.atan2(
            row[radial_axes[1]] - center[radial_axes[1]],
            row[radial_axes[0]] - center[radial_axes[0]],
        ) % (2.0 * math.pi)
        occupied.add(min(angular_bins - 1, int(angle * angular_bins / (2.0 * math.pi))))
    if len(occupied) < math.ceil(angular_bins * 0.75):
        return _reject("insufficient-angular-coverage")
    outer_radii = tuple(radii[index] for index in outer)
    average_radius = sum(outer_radii) / len(outer_radii)
    radial_cv = math.sqrt(
        sum((radius - average_radius) ** 2 for radius in outer_radii) / len(outer_radii)
    ) / average_radius
    if radial_cv > 0.06:
        return _reject("irregular-outer-band")

    normals = tuple(_normal(positions, triangle) for triangle in triangles)
    if any(value is None for value in normals):
        return _reject("degenerate-geometry")
    planar_limit = math.cos(math.radians(1.0))
    if not any(
        sum(a * b for a, b in zip(normals[owners[0]], normals[owners[1]])) >= planar_limit  # type: ignore[arg-type]
        for owners in edge_faces.values()
    ):
        return _reject("no-planar-interior")

    priority = tuple(sorted(index for canonical in outer for index in members[canonical]))
    return RoundComponentDecision(True, "eligible", axis, priority)
