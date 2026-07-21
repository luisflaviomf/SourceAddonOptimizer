from __future__ import annotations

from collections import defaultdict
import math
import struct
from typing import Iterable, Sequence

from .contracts import MaximumProfile, RegionBudget, RiskFeatures
from .materials import MaterialSemantics
from .regions import SmdRegion
from .smd import SmdTriangle, SmdVertex


Vector3 = tuple[float, float, float]
CANONICAL_VIEWS: tuple[Vector3, ...] = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
    (math.sqrt(0.5), math.sqrt(0.5), 0.0),
    (-math.sqrt(0.5), math.sqrt(0.5), 0.0),
    (math.sqrt(0.5), -math.sqrt(0.5), 0.0),
    (-math.sqrt(0.5), -math.sqrt(0.5), 0.0),
)
_VISUAL_PRIORITY_TOKENS = (
    "wheel", "tire", "tyre", "rim", "glass", "lens", "light", "lamp",
    "steer", "exhaust", "pipe", "tube", "gauge", "dial", "scope", "sight",
    "windscreen", "windshield",
)


def _sub(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _dot(left: Vector3, right: Vector3) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(value: Vector3) -> float:
    return math.sqrt(_dot(value, value))


def _normal(triangle: SmdTriangle) -> tuple[Vector3, float]:
    first, second, third = (vertex.position for vertex in triangle.vertices)
    cross = _cross(_sub(second, first), _sub(third, first))
    magnitude = _length(cross)
    if magnitude <= 1e-15:
        return (0.0, 0.0, 1.0), 0.0
    return tuple(value / magnitude for value in cross), magnitude * 0.5  # type: ignore[return-value]


def _position_word(value: Vector3) -> bytes:
    return struct.pack("<3f", *(0.0 if component == 0.0 else component for component in value))


def _weighted_percentile(values: Iterable[tuple[float, float]], percentile: float) -> float:
    ordered = sorted((value, weight) for value, weight in values if weight > 0.0)
    if not ordered:
        return 0.0
    target = sum(weight for _value, weight in ordered) * percentile
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= target:
            return value
    return ordered[-1][0]


def _influence_map(vertex: SmdVertex) -> dict[int, float]:
    result: dict[int, float] = {}
    for influence in vertex.influences:
        if influence.weight > 0.0:
            result[influence.bone] = result.get(influence.bone, 0.0) + influence.weight
    total = sum(result.values())
    return {bone: weight / total for bone, weight in result.items()} if total > 0.0 else {}


def _skin_distance(left: SmdVertex, right: SmdVertex) -> float:
    left_map = _influence_map(left)
    right_map = _influence_map(right)
    return 0.5 * sum(abs(left_map.get(bone, 0.0) - right_map.get(bone, 0.0)) for bone in set(left_map) | set(right_map))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def measure_risk(
    region: SmdRegion,
    semantics: MaterialSemantics,
    canonical_views: Sequence[Vector3],
) -> RiskFeatures:
    if not region.triangles or not canonical_views:
        raise ValueError("risk measurement requires geometry and canonical views")
    face_data = tuple(_normal(triangle) for triangle in region.triangles)
    edges: dict[tuple[bytes, bytes], list[tuple[int, SmdVertex, SmdVertex, float]]] = defaultdict(list)
    position_vertices: dict[bytes, list[SmdVertex]] = defaultdict(list)
    for triangle_index, triangle in enumerate(region.triangles):
        for vertex in triangle.vertices:
            position_vertices[_position_word(vertex.position)].append(vertex)
        for first_index, second_index in ((0, 1), (1, 2), (2, 0)):
            first = triangle.vertices[first_index]
            second = triangle.vertices[second_index]
            first_word = _position_word(first.position)
            second_word = _position_word(second.position)
            key = tuple(sorted((first_word, second_word)))
            edges[key].append((triangle_index, first, second, _length(_sub(first.position, second.position))))

    curvature: list[tuple[float, float]] = []
    hard_length = 0.0
    uv_seam_length = 0.0
    total_edge_length = sum(owners[0][3] for owners in edges.values()) or 1.0
    skin_gradient = 0.0
    incident_length_by_position: dict[bytes, float] = defaultdict(float)
    for (first_word, second_word), owners in edges.items():
        edge_length = owners[0][3]
        incident_length_by_position[first_word] += edge_length
        incident_length_by_position[second_word] += edge_length
        if len(owners) == 2:
            first_normal = face_data[owners[0][0]][0]
            second_normal = face_data[owners[1][0]][0]
            angle = math.acos(max(-1.0, min(1.0, _dot(first_normal, second_normal))))
            curvature.append((angle / math.pi, owners[0][3]))
        first_owner = owners[0]
        skin_gradient += max(_skin_distance(owner[1], owner[2]) for owner in owners) * first_owner[3]

    for position_word, vertices in position_vertices.items():
        normal_words = {
            struct.pack("<3f", *(0.0 if component == 0.0 else component for component in vertex.normal))
            for vertex in vertices
        }
        uv_words = {
            struct.pack("<2f", *(0.0 if component == 0.0 else component for component in vertex.uv))
            for vertex in vertices
        }
        incident = incident_length_by_position[position_word]
        if len(normal_words) > 1:
            hard_length += incident
        if len(uv_words) > 1:
            uv_seam_length += incident

    silhouette_total = 0.0
    for view in canonical_views:
        for owners in edges.values():
            if len(owners) == 1:
                silhouette_total += owners[0][3]
            elif len(owners) == 2:
                first = _dot(face_data[owners[0][0]][0], view)
                second = _dot(face_data[owners[1][0]][0], view)
                if first * second < -1e-10:
                    silhouette_total += owners[0][3]
    silhouette_fraction = _clamp01(silhouette_total / (total_edge_length * len(canonical_views)))

    entropy_total = 0.0
    vertex_count = 0
    for vertices in position_vertices.values():
        weights = tuple(_influence_map(vertices[0]).values())
        if weights:
            entropy_total += -sum(weight * math.log(weight) for weight in weights if weight > 0.0) / math.log(3.0)
            vertex_count += 1
    skinning_risk = _clamp01(0.55 * entropy_total / max(1, vertex_count) + 0.45 * skin_gradient / total_edge_length)
    curvature_p95 = _weighted_percentile(curvature, 0.95)
    hard_density = _clamp01(hard_length / total_edge_length)
    uv_density = _clamp01(uv_seam_length / total_edge_length)
    score = _clamp01(
        0.22 * curvature_p95
        + 0.18 * silhouette_fraction
        + 0.12 * hard_density
        + 0.12 * uv_density
        + 0.14 * semantics.risk
        + 0.14 * skinning_risk
        + 0.08 * (1.0 - semantics.confidence)
    )
    return RiskFeatures(
        curvature_p95,
        silhouette_fraction,
        hard_density,
        uv_density,
        semantics.risk,
        skinning_risk,
        semantics.confidence,
        score,
        0.08 + 0.57 * score,
    )


def budget_for_risk(profile: MaximumProfile, features: RiskFeatures) -> RegionBudget:
    scale = 1.0 - 0.55 * _clamp01(features.score)
    limits = profile.limits
    return RegionBudget(
        surface_p95=limits.surface_p95 * scale,
        surface_max=limits.surface_max * scale,
        normal_p95_degrees=limits.normal_p95_degrees * scale,
        normal_max_degrees=limits.normal_max_degrees * scale,
        silhouette_iou_loss=limits.silhouette_iou_loss * scale,
        silhouette_boundary_p95_px=limits.silhouette_boundary_p95_px * scale,
        uv_p95=limits.uv_p95 * scale,
        material_boundary_p95_px=limits.material_boundary_p95_px * scale,
        skinning_p95=limits.skinning_p95 * scale,
        skinning_max=limits.skinning_max * scale,
    )


def requires_adaptive_validation(
    source_name: str,
    material: str,
    triangle_count: int,
    features: RiskFeatures,
    semantics: MaterialSemantics,
) -> bool:
    identity = f"{source_name} {material}".replace("\\", "/").casefold()
    if any(token in identity for token in _VISUAL_PRIORITY_TOKENS):
        return True
    if semantics.requires_render or semantics.confidence < 0.75:
        return True
    if features.skinning_risk > 0.08:
        return True
    significant = triangle_count >= 5000
    if significant and features.curvature_p95_norm >= 0.025:
        return True
    return (
        triangle_count >= 5000
        and features.silhouette_fraction >= 0.35
    )
