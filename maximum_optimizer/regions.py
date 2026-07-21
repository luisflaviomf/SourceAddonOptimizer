from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import struct
from typing import Literal

from .contracts import RegionKey
from .qc_graph import QcOccurrence
from .smd import SmdDocument, SmdTriangle


Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class SmdRegion:
    key: RegionKey
    material: str
    local_ordinal: int
    triangle_ordinals: tuple[int, ...]
    triangles: tuple[SmdTriangle, ...]
    centroid: Vector3
    bounds_min: Vector3
    bounds_max: Vector3
    bone_ids: tuple[int, ...]


@dataclass(frozen=True)
class RegionGraph:
    occurrence: QcOccurrence
    regions: tuple[SmdRegion, ...]
    nonmanifold: bool


@dataclass(frozen=True)
class RegionPair:
    original: SmdRegion
    normal: SmdRegion
    confident: bool = True
    reason: str = ""


@dataclass(frozen=True)
class RegionCorrespondence:
    status: Literal["mapped", "ambiguous"]
    fallback: Literal["none", "normal-source"]
    pairs: tuple[RegionPair, ...]
    reason: str


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, value: int) -> int:
        while self._parent[value] != value:
            self._parent[value] = self._parent[self._parent[value]]
            value = self._parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def _position_word(position: Vector3) -> bytes:
    normalized = tuple(0.0 if value == 0.0 else float(value) for value in position)
    return struct.pack("<3f", *normalized)


def _component_region(
    occurrence: QcOccurrence,
    material: str,
    local_ordinal: int,
    ordinals: tuple[int, ...],
    document: SmdDocument,
) -> SmdRegion:
    triangles = tuple(document.triangles[index] for index in ordinals)
    positions = tuple(vertex.position for triangle in triangles for vertex in triangle.vertices)
    centroid = tuple(sum(position[axis] for position in positions) / len(positions) for axis in range(3))
    bounds_min = tuple(min(position[axis] for position in positions) for axis in range(3))
    bounds_max = tuple(max(position[axis] for position in positions) for axis in range(3))
    bones = tuple(sorted({influence.bone for triangle in triangles for vertex in triangle.vertices for influence in vertex.influences if influence.weight > 0.0}))
    signature = {
        "triangles": len(triangles),
        "centroid": [round(value, 6) for value in centroid],
        "bounds_min": [round(value, 6) for value in bounds_min],
        "bounds_max": [round(value, 6) for value in bounds_max],
        "bones": list(bones),
    }
    payload = {
        "qc": occurrence.qc_path.as_posix().casefold(),
        "directive": occurrence.directive.casefold(),
        "line": occurrence.line,
        "source": occurrence.source_path.as_posix().casefold(),
        "material": material.replace("\\", "/").casefold(),
        "component": signature,
        "ordinal": local_ordinal,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return SmdRegion(
        RegionKey(f"r-{digest}"),
        material,
        local_ordinal,
        ordinals,
        triangles,
        centroid,  # type: ignore[arg-type]
        bounds_min,  # type: ignore[arg-type]
        bounds_max,  # type: ignore[arg-type]
        bones,
    )


def build_region_graph(document: SmdDocument, occurrence: QcOccurrence) -> RegionGraph:
    grouped: dict[str, list[int]] = {}
    material_names: dict[str, str] = {}
    for ordinal, triangle in enumerate(document.triangles):
        key = triangle.material.replace("\\", "/").casefold()
        grouped.setdefault(key, []).append(ordinal)
        material_names.setdefault(key, triangle.material)

    pending: list[tuple[str, tuple[int, ...], tuple[float, ...]]] = []
    nonmanifold = False
    for material_key, ordinals in grouped.items():
        dsu = _DisjointSet(len(ordinals))
        by_position: dict[bytes, int] = {}
        edge_counts: dict[tuple[bytes, bytes], int] = {}
        for local_index, ordinal in enumerate(ordinals):
            triangle = document.triangles[ordinal]
            words = tuple(_position_word(vertex.position) for vertex in triangle.vertices)
            for word in words:
                previous = by_position.setdefault(word, local_index)
                dsu.union(local_index, previous)
            for first, second in ((words[0], words[1]), (words[1], words[2]), (words[2], words[0])):
                edge = tuple(sorted((first, second)))
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
        nonmanifold = nonmanifold or any(count > 2 for count in edge_counts.values())
        components: dict[int, list[int]] = {}
        for local_index, ordinal in enumerate(ordinals):
            components.setdefault(dsu.find(local_index), []).append(ordinal)
        for component in components.values():
            positions = tuple(
                vertex.position
                for ordinal in component
                for vertex in document.triangles[ordinal].vertices
            )
            sort_key = tuple(
                round(sum(position[axis] for position in positions) / len(positions), 9)
                for axis in range(3)
            ) + (len(component), min(component))
            pending.append((material_key, tuple(sorted(component)), sort_key))

    pending.sort(key=lambda item: (item[0], item[2]))
    material_ordinals: dict[str, int] = {}
    regions: list[SmdRegion] = []
    for material_key, ordinals, _sort_key in pending:
        local_ordinal = material_ordinals.get(material_key, 0)
        material_ordinals[material_key] = local_ordinal + 1
        regions.append(
            _component_region(
                occurrence,
                material_names[material_key],
                local_ordinal,
                ordinals,
                document,
            )
        )
    return RegionGraph(occurrence, tuple(regions), nonmanifold)


def _distance(left: Vector3, right: Vector3) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _mapping_score(original: SmdRegion, normal: SmdRegion, scale: float) -> float:
    original_extent = tuple(original.bounds_max[index] - original.bounds_min[index] for index in range(3))
    normal_extent = tuple(normal.bounds_max[index] - normal.bounds_min[index] for index in range(3))
    triangle_delta = abs(len(original.triangles) - len(normal.triangles)) / max(1, len(original.triangles))
    return (
        _distance(original.centroid, normal.centroid) / scale
        + _distance(original_extent, normal_extent) / scale
        + 0.1 * triangle_delta
    )


def correspond_graphs(original: RegionGraph, normal: RegionGraph) -> RegionCorrespondence:
    if original.occurrence != normal.occurrence:
        return RegionCorrespondence("ambiguous", "normal-source", (), "QC occurrence differs")
    original_by_material: dict[str, list[SmdRegion]] = {}
    normal_by_material: dict[str, list[SmdRegion]] = {}
    for region in original.regions:
        original_by_material.setdefault(region.material.casefold(), []).append(region)
    for region in normal.regions:
        normal_by_material.setdefault(region.material.casefold(), []).append(region)
    all_positions = tuple(
        vertex.position
        for region in original.regions
        for triangle in region.triangles
        for vertex in triangle.vertices
    )
    bounds_min = tuple(min(position[axis] for position in all_positions) for axis in range(3))
    bounds_max = tuple(max(position[axis] for position in all_positions) for axis in range(3))
    scale = max(_distance(bounds_min, bounds_max), 1e-9)
    pairs: list[RegionPair] = []
    fallback_reasons: list[str] = []
    for material in sorted(original_by_material):
        originals = original_by_material[material]
        normals = normal_by_material.get(material, [])
        if len(originals) != len(normals):
            reason = f"component count differs for material {material}"
            fallback_reasons.append(reason)
            pairs.extend(
                RegionPair(original_region, original_region, False, reason)
                for original_region in originals
            )
            continue
        unused = set(range(len(normals)))
        for original_region in originals:
            candidates = sorted(
                (
                    (_mapping_score(original_region, normals[index], scale), index)
                    for index in unused
                    if original_region.bone_ids == normals[index].bone_ids
                ),
                key=lambda item: (item[0], item[1]),
            )
            if not candidates or candidates[0][0] > 0.35:
                reason = f"no confident component match for material {material}"
                fallback_reasons.append(reason)
                pairs.append(RegionPair(original_region, original_region, False, reason))
                continue
            if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) <= 1e-9:
                reason = f"tied component match for material {material}"
                fallback_reasons.append(reason)
                pairs.append(RegionPair(original_region, original_region, False, reason))
                continue
            index = candidates[0][1]
            unused.remove(index)
            pairs.append(RegionPair(original_region, normals[index]))
    extra_materials = sorted(set(normal_by_material) - set(original_by_material))
    if extra_materials:
        fallback_reasons.append(f"ignored normal-only materials: {', '.join(extra_materials)}")
    reason = f"{len(set(fallback_reasons))} local fallback reason(s)" if fallback_reasons else ""
    return RegionCorrespondence("mapped", "none", tuple(pairs), reason)
