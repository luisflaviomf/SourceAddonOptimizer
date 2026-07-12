from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from typing import Sequence

from maximum_optimizer.smd_contract import POSITION_SERIALIZATION_TOLERANCE


SIGNIFICANT_COMPONENT_AREA_FRACTION = 0.05
SIGNIFICANT_COMPONENT_EXTENT_FRACTION = 0.10


@dataclass(frozen=True)
class RoundComponentAudit:
    index: int
    canonical_vertices: int
    source_vertices: int
    triangles: int
    area_fraction: float
    extent_fraction: float
    significant: bool
    eligible: bool
    reason: str
    axis: int | None = None
    priority_vertices: int = 0


@dataclass(frozen=True)
class RoundComponentDecision:
    eligible: bool
    reason: str
    axis: int | None = None
    priority_vertices: tuple[int, ...] = ()
    position_tolerance: float = POSITION_SERIALIZATION_TOLERANCE
    components: tuple[RoundComponentAudit, ...] = ()


def _reject(
    reason: str, components: Sequence[RoundComponentAudit] = ()
) -> RoundComponentDecision:
    return RoundComponentDecision(False, reason, components=tuple(components))


def _canonicalize_positions(
    positions: Sequence[Sequence[float]], tolerance: float
) -> tuple[list[int], dict[int, list[int]]]:
    """Weld source wedges deterministically using the audited SMD position tolerance."""
    cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    canonical_ids: list[int] = []
    members: dict[int, list[int]] = defaultdict(list)
    rows: list[tuple[float, float, float]] = []
    for index, position in enumerate(positions):
        if len(position) != 3 or not all(math.isfinite(float(value)) for value in position):
            raise ValueError("positions must contain finite float3 rows")
        row = tuple(float(value) for value in position)
        rows.append(row)  # type: ignore[arg-type]
        cell = tuple(math.floor(value / tolerance) for value in row)
        matches = []
        for x in range(cell[0] - 1, cell[0] + 2):
            for y in range(cell[1] - 1, cell[1] + 2):
                for z in range(cell[2] - 1, cell[2] + 2):
                    for candidate in cells.get((x, y, z), ()):
                        if all(
                            abs(row[axis] - rows[candidate][axis]) <= tolerance
                            for axis in range(3)
                        ):
                            matches.append(candidate)
        canonical = min(matches) if matches else index
        canonical_ids.append(canonical)
        members[canonical].append(index)
        if canonical == index:
            cells[cell].append(index)
    return canonical_ids, members


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


def _triangle_area(positions: Sequence[Sequence[float]], triangle: Sequence[int]) -> float:
    a, b, c = (positions[index] for index in triangle)
    ab = tuple(float(b[axis]) - float(a[axis]) for axis in range(3))
    ac = tuple(float(c[axis]) - float(a[axis]) for axis in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return math.sqrt(sum(value * value for value in cross)) * 0.5


def _component_geometry(
    positions: Sequence[Sequence[float]],
    triangles: Sequence[tuple[int, int, int]],
    vertices: set[int],
    angular_bins: int,
) -> tuple[bool, str, int | None, set[int]]:
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_index, triangle in enumerate(triangles):
        for first, second in (
            (triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])
        ):
            edge_faces[(min(first, second), max(first, second))].append(face_index)
    if any(len(owners) != 2 for owners in edge_faces.values()):
        return False, "not-closed-manifold", None, set()

    unique_positions = {index: tuple(float(value) for value in positions[index]) for index in vertices}
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
        return False, "not-thin-axial", None, set()

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
        return False, "insufficient-angular-coverage", None, set()
    outer_radii = tuple(radii[index] for index in outer)
    average_radius = sum(outer_radii) / len(outer_radii)
    radial_cv = math.sqrt(
        sum((radius - average_radius) ** 2 for radius in outer_radii) / len(outer_radii)
    ) / average_radius
    if radial_cv > 0.06:
        return False, "irregular-outer-band", None, set()

    normals = tuple(_normal(positions, triangle) for triangle in triangles)
    if any(value is None for value in normals):
        return False, "degenerate-geometry", None, set()
    planar_limit = math.cos(math.radians(1.0))
    if not any(
        sum(a * b for a, b in zip(normals[owners[0]], normals[owners[1]])) >= planar_limit  # type: ignore[arg-type]
        for owners in edge_faces.values()
    ):
        return False, "no-planar-interior", None, set()
    return True, "eligible", axis, outer


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

    canonical_ids, members = _canonicalize_positions(
        positions, POSITION_SERIALIZATION_TOLERANCE
    )

    rigid = tuple(_rigid_signature(values) for values in influence_signatures)
    if any(value is None for value in rigid):
        return _reject("not-rigid")

    canonical_triangles: list[tuple[int, int, int]] = []
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
        canonical_triangles.append(canonical)  # type: ignore[arg-type]
        for first, second in (
            (canonical[0], canonical[1]),
            (canonical[1], canonical[2]),
            (canonical[2], canonical[0]),
        ):
            adjacency[first].add(second)
            adjacency[second].add(first)

    unvisited = set(adjacency)
    component_vertices: list[set[int]] = []
    while unvisited:
        visited: set[int] = set()
        queue = deque((min(unvisited),))
        while queue:
            vertex = queue.popleft()
            if vertex in visited:
                continue
            visited.add(vertex)
            queue.extend(adjacency[vertex] - visited)
        component_vertices.append(visited)
        unvisited -= visited

    component_by_vertex = {
        vertex: component_index
        for component_index, vertices in enumerate(component_vertices)
        for vertex in vertices
    }
    grouped_triangles: list[list[tuple[int, int, int]]] = [
        [] for _ in component_vertices
    ]
    for triangle in canonical_triangles:
        grouped_triangles[component_by_vertex[triangle[0]]].append(triangle)
    component_triangles = [tuple(rows) for rows in grouped_triangles]
    component_areas = [
        sum(_triangle_area(positions, triangle) for triangle in rows)
        for rows in component_triangles
    ]
    total_area = sum(component_areas)
    used_vertices = set(adjacency)
    global_minima = tuple(
        min(float(positions[index][axis]) for index in used_vertices) for axis in range(3)
    )
    global_maxima = tuple(
        max(float(positions[index][axis]) for index in used_vertices) for axis in range(3)
    )
    global_extent = max(global_maxima[axis] - global_minima[axis] for axis in range(3))

    audits: list[RoundComponentAudit] = []
    priority_vertices: set[int] = set()
    admitted_axes: set[int] = set()
    significant_rejections: list[tuple[int, str]] = []
    for component_index, (vertices, rows, area) in enumerate(
        zip(component_vertices, component_triangles, component_areas)
    ):
        local_minima = tuple(
            min(float(positions[index][axis]) for index in vertices) for axis in range(3)
        )
        local_maxima = tuple(
            max(float(positions[index][axis]) for index in vertices) for axis in range(3)
        )
        local_extent = max(local_maxima[axis] - local_minima[axis] for axis in range(3))
        area_fraction = area / total_area if total_area > 0.0 else 0.0
        extent_fraction = local_extent / global_extent if global_extent > 0.0 else 0.0
        significant = (
            area_fraction >= SIGNIFICANT_COMPONENT_AREA_FRACTION
            or extent_fraction >= SIGNIFICANT_COMPONENT_EXTENT_FRACTION
        )
        source_vertices = {source for canonical in vertices for source in members[canonical]}
        if len({rigid[source] for source in source_vertices}) != 1:
            eligible, reason, axis, outer = False, "not-rigid", None, set()
        else:
            eligible, reason, axis, outer = _component_geometry(
                positions, rows, vertices, angular_bins
            )
        component_priority = (
            {source for canonical in outer for source in members[canonical]}
            if eligible else source_vertices
        )
        audits.append(RoundComponentAudit(
            index=component_index,
            canonical_vertices=len(vertices),
            source_vertices=len(source_vertices),
            triangles=len(rows),
            area_fraction=area_fraction,
            extent_fraction=extent_fraction,
            significant=significant,
            eligible=eligible,
            reason=reason,
            axis=axis,
            priority_vertices=len(component_priority),
        ))
        if not eligible and significant:
            significant_rejections.append((component_index, reason))
        priority_vertices.update(component_priority)
        if eligible and axis is not None:
            admitted_axes.add(axis)

    if significant_rejections:
        component_index, reason = significant_rejections[0]
        rejection = (
            reason
            if len(component_vertices) == 1
            else f"significant-component-{component_index}:{reason}"
        )
        return _reject(rejection, audits)
    if not admitted_axes:
        reason = audits[0].reason if len(audits) == 1 else "no-eligible-round-component"
        return _reject(reason, audits)
    if len(admitted_axes) != 1:
        return _reject("inconsistent-round-axes", audits)
    axis = next(iter(admitted_axes))
    return RoundComponentDecision(
        True,
        "eligible",
        axis,
        tuple(sorted(priority_vertices)),
        POSITION_SERIALIZATION_TOLERANCE,
        tuple(audits),
    )
