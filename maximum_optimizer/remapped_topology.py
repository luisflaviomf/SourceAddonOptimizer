from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Literal, Sequence

from .smd_contract import ParsedSmd, SmdCorner, SmdTriangle, parse_smd_triangles


STRATEGY = "meshopt-remapped-topology-v1"
TRANSFER = "remapped-topology-v1"
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_TRIANGLES = 1_000_000
MAX_MATERIALS = 4_096
MAX_COMPONENTS = 100_000
MAX_CORNER_TOKENS = 64
DEGENERATE_CROSS_SQUARED = 1e-30


Position = tuple[float, float, float]
Edge = tuple[Position, Position]
ComponentKey = tuple[int, int]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: object) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


def _is_sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.casefold()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class RemappedMaterialProof:
    ordinal: int
    material: str
    triangles_before: int
    triangles_after: int
    source_components: int
    covered_components: int

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or self.ordinal < 0
            or type(self.material) is not str
            or not self.material
            or any(type(value) is not int for value in (
                self.triangles_before,
                self.triangles_after,
                self.source_components,
                self.covered_components,
            ))
            or not 0 < self.triangles_after <= self.triangles_before
            or not 0 < self.covered_components == self.source_components
        ):
            raise ValueError("remapped material proof is invalid")


@dataclass(frozen=True)
class RemappedComponentProof:
    ordinal: int
    material_ordinal: int
    material: str
    source_first_triangle: int
    source_triangles: int
    output_triangles: int
    covered: Literal[True]
    source_boundary_edges: int
    output_boundary_edges: int
    source_boundary_sha256: str
    output_boundary_sha256: str
    source_nonmanifold_excess: int
    output_nonmanifold_excess: int
    source_max_edge_valence: int
    output_max_edge_valence: int
    source_direction_conflicts: int
    output_direction_conflicts: int
    source_orientation_conflicts: int
    output_orientation_conflicts: int

    def __post_init__(self) -> None:
        integer_names = (
            "ordinal", "material_ordinal", "source_first_triangle", "source_triangles",
            "output_triangles", "source_boundary_edges", "output_boundary_edges",
            "source_nonmanifold_excess", "output_nonmanifold_excess",
            "source_max_edge_valence", "output_max_edge_valence",
            "source_direction_conflicts", "output_direction_conflicts",
            "source_orientation_conflicts", "output_orientation_conflicts",
        )
        if (
            any(type(getattr(self, name)) is not int or getattr(self, name) < 0 for name in integer_names)
            or type(self.material) is not str
            or not self.material
            or self.covered is not True
            or not 0 < self.output_triangles <= self.source_triangles
            or self.source_boundary_edges != self.output_boundary_edges
            or not _is_sha(self.source_boundary_sha256)
            or self.source_boundary_sha256 != self.output_boundary_sha256
            or self.output_nonmanifold_excess > self.source_nonmanifold_excess
            or self.output_max_edge_valence > self.source_max_edge_valence
            or self.output_direction_conflicts > self.source_direction_conflicts
            or self.output_orientation_conflicts > self.source_orientation_conflicts
        ):
            raise ValueError("remapped component proof is invalid")


@dataclass(frozen=True)
class RemappedTopologyProof:
    schema: Literal[1]
    strategy: Literal["meshopt-remapped-topology-v1"]
    transfer: Literal["remapped-topology-v1"]
    quality_status: Literal["unverified"]
    quality_claim: None
    requested_ratio: float
    source_sha256: str
    output_sha256: str
    triangles_before: int
    global_target_triangles: int
    triangles_after: int
    retained_cycles: int
    remapped_cycles: int
    source_component_count: int
    covered_component_count: int
    source_boundary_edges: int
    output_boundary_edges: int
    source_boundary_sha256: str
    output_boundary_sha256: str
    source_nonmanifold_excess: int
    output_nonmanifold_excess: int
    source_max_edge_valence: int
    output_max_edge_valence: int
    source_direction_conflicts: int
    output_direction_conflicts: int
    source_orientation_conflicts: int
    output_orientation_conflicts: int
    source_corner_group_sha256: str
    output_source_corner_ordinals: tuple[int, ...]
    materials: tuple[RemappedMaterialProof, ...]
    components: tuple[RemappedComponentProof, ...]
    proof_sha256: str

    def __post_init__(self) -> None:
        integer_names = (
            "triangles_before", "global_target_triangles", "triangles_after",
            "retained_cycles", "remapped_cycles",
            "source_component_count", "covered_component_count", "source_boundary_edges",
            "output_boundary_edges", "source_nonmanifold_excess",
            "output_nonmanifold_excess", "source_max_edge_valence",
            "output_max_edge_valence", "source_direction_conflicts",
            "output_direction_conflicts", "source_orientation_conflicts",
            "output_orientation_conflicts",
        )
        if (
            type(self.schema) is not int
            or self.schema != 1
            or self.strategy != STRATEGY
            or self.transfer != TRANSFER
            or self.quality_status != "unverified"
            or self.quality_claim is not None
            or type(self.requested_ratio) is not float
            or not math.isfinite(self.requested_ratio)
            or not 0.0 < self.requested_ratio <= 1.0
            or not all(_is_sha(value) for value in (
                self.source_sha256,
                self.output_sha256,
                self.source_boundary_sha256,
                self.output_boundary_sha256,
                self.source_corner_group_sha256,
                self.proof_sha256,
            ))
            or any(type(getattr(self, name)) is not int or getattr(self, name) < 0 for name in integer_names)
            or not 0 < self.triangles_after < self.triangles_before
            or self.global_target_triangles
            != max(len(self.materials), math.floor(self.triangles_before * self.requested_ratio))
            or self.triangles_after > self.global_target_triangles
            or self.retained_cycles + self.remapped_cycles != self.triangles_after
            or self.covered_component_count != self.source_component_count
            or self.source_boundary_edges != self.output_boundary_edges
            or self.source_boundary_sha256 != self.output_boundary_sha256
            or self.output_nonmanifold_excess > self.source_nonmanifold_excess
            or self.output_max_edge_valence > self.source_max_edge_valence
            or self.output_direction_conflicts > self.source_direction_conflicts
            or self.output_orientation_conflicts > self.source_orientation_conflicts
            or len(self.output_source_corner_ordinals) != self.triangles_after * 3
            or any(type(value) is not int or value < 0 for value in self.output_source_corner_ordinals)
            or not self.materials
            or any(not isinstance(item, RemappedMaterialProof) for item in self.materials)
            or any(not isinstance(item, RemappedComponentProof) for item in self.components)
            or tuple(item.ordinal for item in self.materials) != tuple(range(len(self.materials)))
            or tuple(item.ordinal for item in self.components) != tuple(range(len(self.components)))
            or len(self.components) != self.source_component_count
            or len({(item.material_ordinal, item.source_first_triangle) for item in self.components}) != len(self.components)
            or sum(item.triangles_before for item in self.materials) != self.triangles_before
            or sum(item.triangles_after for item in self.materials) != self.triangles_after
            or sum(item.source_components for item in self.materials) != self.source_component_count
            or sum(item.covered_components for item in self.materials) != self.covered_component_count
            or len({item.material for item in self.materials}) != len(self.materials)
            or any(value >= self.triangles_before * 3 for value in self.output_source_corner_ordinals)
            or sum(item.source_triangles for item in self.components) != self.triangles_before
            or sum(item.output_triangles for item in self.components) != self.triangles_after
            or sum(item.source_boundary_edges for item in self.components) != self.source_boundary_edges
            or sum(item.output_boundary_edges for item in self.components) != self.output_boundary_edges
            or sum(item.source_nonmanifold_excess for item in self.components) != self.source_nonmanifold_excess
            or sum(item.output_nonmanifold_excess for item in self.components) != self.output_nonmanifold_excess
            or max((item.source_max_edge_valence for item in self.components), default=0) != self.source_max_edge_valence
            or max((item.output_max_edge_valence for item in self.components), default=0) != self.output_max_edge_valence
            or sum(item.source_direction_conflicts for item in self.components) != self.source_direction_conflicts
            or sum(item.output_direction_conflicts for item in self.components) != self.output_direction_conflicts
            or sum(item.source_orientation_conflicts for item in self.components) != self.source_orientation_conflicts
            or sum(item.output_orientation_conflicts for item in self.components) != self.output_orientation_conflicts
            or any(
                item.material_ordinal >= len(self.materials)
                or self.materials[item.material_ordinal].material != item.material
                for item in self.components
            )
            or any(
                sum(component.material_ordinal == item.ordinal for component in self.components)
                != item.source_components
                for item in self.materials
            )
        ):
            raise ValueError("remapped topology proof relationships are invalid")


def remapped_topology_proof_payload(
    proof: RemappedTopologyProof, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(proof, RemappedTopologyProof):
        raise TypeError("remapped topology proof is invalid")
    payload = asdict(proof)
    payload["output_source_corner_ordinals"] = list(proof.output_source_corner_ordinals)
    payload["materials"] = [asdict(item) for item in proof.materials]
    payload["components"] = [asdict(item) for item in proof.components]
    if not include_seal:
        payload.pop("proof_sha256")
    return payload


def remapped_topology_proof_from_payload(value: object) -> RemappedTopologyProof:
    expected = {
        "schema", "strategy", "transfer", "quality_status", "quality_claim",
        "requested_ratio", "source_sha256", "output_sha256", "triangles_before",
        "global_target_triangles", "triangles_after", "retained_cycles", "remapped_cycles",
        "source_component_count",
        "covered_component_count", "source_boundary_edges", "output_boundary_edges",
        "source_boundary_sha256", "output_boundary_sha256",
        "source_nonmanifold_excess", "output_nonmanifold_excess",
        "source_max_edge_valence", "output_max_edge_valence", "source_direction_conflicts",
        "output_direction_conflicts", "source_orientation_conflicts",
        "output_orientation_conflicts", "source_corner_group_sha256",
        "output_source_corner_ordinals", "materials", "components", "proof_sha256",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("remapped topology proof fields are invalid")
    material_fields = {
        "ordinal", "material", "triangles_before", "triangles_after",
        "source_components", "covered_components",
    }
    component_fields = {
        "ordinal", "material_ordinal", "material", "source_first_triangle",
        "source_triangles", "output_triangles", "covered", "source_boundary_edges",
        "output_boundary_edges", "source_boundary_sha256", "output_boundary_sha256",
        "source_nonmanifold_excess", "output_nonmanifold_excess",
        "source_max_edge_valence", "output_max_edge_valence",
        "source_direction_conflicts", "output_direction_conflicts",
        "source_orientation_conflicts", "output_orientation_conflicts",
    }
    raw_materials = value["materials"]
    raw_components = value["components"]
    raw_ordinals = value["output_source_corner_ordinals"]
    if (
        type(raw_materials) is not list
        or not raw_materials
        or any(type(item) is not dict or set(item) != material_fields for item in raw_materials)
        or type(raw_components) is not list
        or not raw_components
        or any(type(item) is not dict or set(item) != component_fields for item in raw_components)
        or type(raw_ordinals) is not list
    ):
        raise ValueError("remapped topology proof collections are invalid")
    copied = dict(value)
    copied["materials"] = tuple(RemappedMaterialProof(**item) for item in raw_materials)
    copied["components"] = tuple(RemappedComponentProof(**item) for item in raw_components)
    copied["output_source_corner_ordinals"] = tuple(raw_ordinals)
    proof = RemappedTopologyProof(**copied)
    if proof.proof_sha256 != _canonical_digest(
        remapped_topology_proof_payload(proof, include_seal=False)
    ):
        raise ValueError("remapped topology proof seal is invalid")
    return proof


def _prefix(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip().casefold() == "triangles":
            return "\n".join(item.rstrip("\r\n") for item in lines[: index + 1]) + "\n"
    raise ValueError("remapped SMD triangles section is missing")


def _require_eof(parsed: ParsedSmd) -> None:
    last_corner = parsed.triangles[-1].corners[-1].line_index
    tail = parsed.lines[last_corner + 1 :]
    if not tail or tail[0].strip().casefold() != "end" or any(line.strip() for line in tail[1:]):
        raise ValueError("remapped SMD has trailing or malformed content")


def _position(corner: SmdCorner) -> Position:
    return corner.position


def _edge(left: Position, right: Position) -> Edge:
    return (left, right) if left <= right else (right, left)


def _rotations(row: tuple[tuple[str, ...], ...]) -> tuple[tuple[tuple[str, ...], ...], ...]:
    return tuple(row[offset:] + row[:offset] for offset in range(3))


def _undirected_cycle(triangle: SmdTriangle) -> tuple[tuple[str, ...], ...]:
    row = tuple(corner.tokens for corner in triangle.corners)
    reverse = (row[0], row[2], row[1])
    return min((*_rotations(row), *_rotations(reverse)))


def _oriented_cycles(triangle: SmdTriangle) -> tuple[tuple[tuple[str, ...], ...], ...]:
    return _rotations(tuple(corner.tokens for corner in triangle.corners))


def _cross_squared(triangle: SmdTriangle) -> float:
    a, b, c = (corner.position for corner in triangle.corners)
    ab = tuple(b[axis] - a[axis] for axis in range(3))
    ac = tuple(c[axis] - a[axis] for axis in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return sum(value * value for value in cross)


def _opposite_all_normals(triangle: SmdTriangle) -> bool:
    a, b, c = (corner.position for corner in triangle.corners)
    ab = tuple(b[axis] - a[axis] for axis in range(3))
    ac = tuple(c[axis] - a[axis] for axis in range(3))
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return all(
        sum(normal[axis] * corner.normal[axis] for axis in range(3)) < 0.0
        for corner in triangle.corners
    )


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left = self.find(left)
        right = self.find(right)
        if left != right:
            if left > right:
                left, right = right, left
            self.parent[right] = left


@dataclass(frozen=True)
class _SourceIndex:
    material_order: tuple[str, ...]
    component_for_triangle: tuple[ComponentKey, ...]
    components_by_material: dict[str, tuple[ComponentKey, ...]]
    corner_components: dict[tuple[str, tuple[str, ...]], frozenset[ComponentKey]]
    corner_ordinals: dict[tuple[str, ComponentKey, tuple[str, ...]], tuple[int, ...]]
    group_sha256: str


def _source_index(source: ParsedSmd) -> _SourceIndex:
    material_order = tuple(dict.fromkeys(triangle.material for triangle in source.triangles))
    material_ordinal = {material: ordinal for ordinal, material in enumerate(material_order)}
    component_for_triangle: list[ComponentKey | None] = [None] * len(source.triangles)
    components_by_material: dict[str, tuple[ComponentKey, ...]] = {}
    for material in material_order:
        globals_ = [
            ordinal for ordinal, triangle in enumerate(source.triangles)
            if triangle.material == material
        ]
        union = _UnionFind(len(globals_))
        position_rows: dict[Position, list[int]] = defaultdict(list)
        for local, global_ in enumerate(globals_):
            for corner in source.triangles[global_].corners:
                position_rows[_position(corner)].append(local)
        for rows in position_rows.values():
            for local in rows[1:]:
                union.union(rows[0], local)
        members: dict[int, list[int]] = defaultdict(list)
        for local, global_ in enumerate(globals_):
            members[union.find(local)].append(global_)
        roots = sorted(members, key=lambda root: min(members[root]))
        components: list[ComponentKey] = []
        for root in roots:
            component = (material_ordinal[material], min(members[root]))
            components.append(component)
            for global_ in members[root]:
                component_for_triangle[global_] = component
        components_by_material[material] = tuple(components)
    if any(component is None for component in component_for_triangle):
        raise RuntimeError("remapped source component indexing is incomplete")

    corner_components: dict[tuple[str, tuple[str, ...]], set[ComponentKey]] = defaultdict(set)
    corner_ordinals: dict[tuple[str, ComponentKey, tuple[str, ...]], list[int]] = defaultdict(list)
    cycle_digests = tuple(
        _canonical_digest({
            "material": triangle.material,
            "tokens": [list(corner.tokens) for corner in triangle.corners],
        })
        for triangle in source.triangles
    )
    for triangle_ordinal, triangle in enumerate(source.triangles):
        component = component_for_triangle[triangle_ordinal]
        assert component is not None
        for corner_index, corner in enumerate(triangle.corners):
            ordinal = triangle_ordinal * 3 + corner_index
            key = (triangle.material, corner.tokens)
            group = (triangle.material, component, corner.tokens)
            corner_components[key].add(component)
            corner_ordinals[group].append(ordinal)
    group_payload = []
    for material, component, tokens in sorted(
        corner_ordinals,
        key=lambda item: (material_ordinal[item[0]], item[1], item[2]),
    ):
        source_ordinals = tuple(sorted(corner_ordinals[(material, component, tokens)]))
        incidents = tuple(
            (ordinal, ordinal // 3, cycle_digests[ordinal // 3])
            for ordinal in source_ordinals
        )
        group_payload.append({
            "material": material,
            "component": list(component),
            "token_sha256": _canonical_digest(list(tokens)),
            "occurrence_count": len(source_ordinals),
            "source_ordinals_sha256": _canonical_digest(list(source_ordinals)),
            "incident_multiset_sha256": _canonical_digest(incidents),
        })
    return _SourceIndex(
        material_order,
        tuple(component for component in component_for_triangle if component is not None),
        components_by_material,
        {key: frozenset(value) for key, value in corner_components.items()},
        {key: tuple(sorted(value)) for key, value in corner_ordinals.items()},
        _canonical_digest(group_payload),
    )


@dataclass(frozen=True)
class _TopologyStats:
    boundaries: dict[ComponentKey, frozenset[Edge]]
    nonmanifold_excess: dict[ComponentKey, int]
    max_valence: dict[ComponentKey, int]
    direction_conflicts: dict[ComponentKey, int]
    orientation_conflicts: dict[ComponentKey, int]


def _topology_stats(
    triangles: Sequence[SmdTriangle], components: Sequence[ComponentKey],
) -> _TopologyStats:
    edges: dict[ComponentKey, Counter[Edge]] = defaultdict(Counter)
    directed: dict[ComponentKey, Counter[tuple[Position, Position]]] = defaultdict(Counter)
    orientations: dict[ComponentKey, int] = defaultdict(int)
    for triangle, component in zip(triangles, components):
        positions = tuple(_position(corner) for corner in triangle.corners)
        for left, right in (
            (positions[0], positions[1]),
            (positions[1], positions[2]),
            (positions[2], positions[0]),
        ):
            edges[component][_edge(left, right)] += 1
            directed[component][(left, right)] += 1
        orientations[component] += int(_opposite_all_normals(triangle))
    all_components = tuple(dict.fromkeys(components))
    boundaries = {}
    excess = {}
    maximum = {}
    conflicts = {}
    for component in all_components:
        counts = edges[component]
        boundaries[component] = frozenset(edge for edge, count in counts.items() if count == 1)
        excess[component] = sum(max(0, count - 2) for count in counts.values())
        maximum[component] = max(counts.values(), default=0)
        conflicts[component] = sum(
            count == 2 and (
                directed[component][edge] == 2
                or directed[component][(edge[1], edge[0])] == 2
            )
            for edge, count in counts.items()
        )
    return _TopologyStats(
        boundaries,
        excess,
        maximum,
        conflicts,
        {component: orientations[component] for component in all_components},
    )


def _boundary_payload(
    components: Sequence[ComponentKey], boundaries: dict[ComponentKey, frozenset[Edge]],
) -> list[dict[str, object]]:
    return [
        {
            "component": list(component),
            "edges": [
                [[float(value).hex() for value in left], [float(value).hex() for value in right]]
                for left, right in sorted(boundaries.get(component, frozenset()))
            ],
        }
        for component in components
    ]


def _validate_caps(name: str, text: str) -> bytes:
    if type(text) is not str:
        raise TypeError(f"{name} SMD text must be str")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError(f"{name} SMD exceeds remapped byte cap")
    return encoded


def validate_remapped_topology_smd(
    source_text: str, output_text: str, requested_ratio: float,
) -> RemappedTopologyProof:
    source_bytes = _validate_caps("source", source_text)
    output_bytes = _validate_caps("output", output_text)
    if (
        type(requested_ratio) not in (int, float)
        or isinstance(requested_ratio, bool)
        or not math.isfinite(float(requested_ratio))
        or not 0.0 < float(requested_ratio) <= 1.0
    ):
        raise ValueError("remapped requested ratio is invalid")
    ratio = float(requested_ratio)
    if _prefix(source_text) != _prefix(output_text):
        raise RuntimeError("remapped output changed nodes or skeleton frames")
    source = parse_smd_triangles(source_text)
    output = parse_smd_triangles(output_text)
    _require_eof(source)
    _require_eof(output)
    if len(source.triangles) > MAX_TRIANGLES or len(output.triangles) > MAX_TRIANGLES:
        raise ValueError("remapped SMD exceeds triangle cap")
    if not 0 < len(output.triangles) < len(source.triangles):
        raise ValueError("remapped output did not strictly reduce triangles")
    if any(len(corner.tokens) > MAX_CORNER_TOKENS for parsed in (source, output) for triangle in parsed.triangles for corner in triangle.corners):
        raise ValueError("remapped SMD corner exceeds token cap")
    source_materials = tuple(dict.fromkeys(triangle.material for triangle in source.triangles))
    output_materials = tuple(dict.fromkeys(triangle.material for triangle in output.triangles))
    if len(source_materials) > MAX_MATERIALS:
        raise ValueError("remapped SMD exceeds material cap")
    if output_materials != source_materials:
        raise RuntimeError("remapped output changed material spelling or order")

    index = _source_index(source)
    component_count = sum(len(value) for value in index.components_by_material.values())
    if component_count > MAX_COMPONENTS:
        raise ValueError("remapped source exceeds component cap")
    source_counts = Counter(triangle.material for triangle in source.triangles)
    output_counts = Counter(triangle.material for triangle in output.triangles)
    global_target = max(
        len(source_materials), math.floor(len(source.triangles) * ratio)
    )
    if len(output.triangles) > global_target:
        raise ValueError("remapped output exceeds deterministic global ratio target")
    if any(
        not 0 < output_counts[material] <= source_counts[material]
        for material in source_materials
    ):
        raise ValueError("remapped output has an invalid material allocation")

    source_oriented_cycles = {
        (triangle.material, cycle)
        for triangle in source.triangles
        for cycle in _oriented_cycles(triangle)
    }
    output_cycles: set[tuple[str, tuple[tuple[str, ...], ...]]] = set()
    output_components: list[ComponentKey] = []
    ordinals: list[int] = []
    group_uses: Counter[tuple[str, ComponentKey, tuple[str, ...]]] = Counter()
    retained = 0
    for triangle in output.triangles:
        if len({corner.tokens for corner in triangle.corners}) != 3:
            raise RuntimeError("remapped output triangle repeats an exact corner payload")
        if _cross_squared(triangle) <= DEGENERATE_CROSS_SQUARED:
            raise RuntimeError("remapped output contains a degenerate triangle")
        cycle = _undirected_cycle(triangle)
        cycle_key = (triangle.material, cycle)
        if cycle_key in output_cycles:
            raise RuntimeError("remapped output duplicates an oriented or reversed triangle")
        output_cycles.add(cycle_key)
        component_sets = []
        for corner in triangle.corners:
            candidates = index.corner_components.get((triangle.material, corner.tokens))
            if not candidates:
                raise RuntimeError("remapped output corner is not an exact same-material source payload")
            component_sets.append(set(candidates))
        common = set.intersection(*component_sets)
        if len(common) != 1:
            raise RuntimeError("remapped output corner provenance crosses or ambiguously selects components")
        component = next(iter(common))
        output_components.append(component)
        for corner in triangle.corners:
            group = (triangle.material, component, corner.tokens)
            candidates = index.corner_ordinals.get(group)
            if not candidates:
                raise RuntimeError("remapped output corner provenance group is missing")
            ordinal = candidates[group_uses[group] % len(candidates)]
            group_uses[group] += 1
            ordinals.append(ordinal)
        retained += int(
            (triangle.material, tuple(corner.tokens for corner in triangle.corners))
            in source_oriented_cycles
        )

    covered = frozenset(output_components)
    expected_components = frozenset(index.component_for_triangle)
    if covered != expected_components:
        raise RuntimeError("remapped output does not cover every source component")
    source_stats = _topology_stats(source.triangles, index.component_for_triangle)
    output_stats = _topology_stats(output.triangles, output_components)
    for component in sorted(expected_components):
        if output_stats.boundaries.get(component, frozenset()) != source_stats.boundaries[component]:
            raise RuntimeError("remapped output changed exact component boundary edges")
        if output_stats.nonmanifold_excess.get(component, 0) > source_stats.nonmanifold_excess[component]:
            raise RuntimeError("remapped output increased component nonmanifold excess")
        if output_stats.max_valence.get(component, 0) > source_stats.max_valence[component]:
            raise RuntimeError("remapped output increased component maximum edge valence")
        if output_stats.direction_conflicts.get(component, 0) > source_stats.direction_conflicts[component]:
            raise RuntimeError("remapped output increased component direction conflicts")
        if output_stats.orientation_conflicts.get(component, 0) > source_stats.orientation_conflicts[component]:
            raise RuntimeError("remapped output increased component orientation conflicts")

    material_proofs = []
    for ordinal, material in enumerate(source_materials):
        material_components = index.components_by_material[material]
        material_proofs.append(RemappedMaterialProof(
            ordinal=ordinal,
            material=material,
            triangles_before=source_counts[material],
            triangles_after=output_counts[material],
            source_components=len(material_components),
            covered_components=sum(component in covered for component in material_components),
        ))
    ordered_components = tuple(sorted(expected_components))
    source_component_counts = Counter(index.component_for_triangle)
    output_component_counts = Counter(output_components)
    component_proofs = []
    for ordinal, component in enumerate(ordered_components):
        material = source_materials[component[0]]
        source_boundary_payload = _boundary_payload((component,), source_stats.boundaries)
        output_boundary_payload = _boundary_payload((component,), output_stats.boundaries)
        component_proofs.append(RemappedComponentProof(
            ordinal=ordinal,
            material_ordinal=component[0],
            material=material,
            source_first_triangle=component[1],
            source_triangles=source_component_counts[component],
            output_triangles=output_component_counts[component],
            covered=True,
            source_boundary_edges=len(source_stats.boundaries[component]),
            output_boundary_edges=len(output_stats.boundaries[component]),
            source_boundary_sha256=_canonical_digest(source_boundary_payload),
            output_boundary_sha256=_canonical_digest(output_boundary_payload),
            source_nonmanifold_excess=source_stats.nonmanifold_excess[component],
            output_nonmanifold_excess=output_stats.nonmanifold_excess[component],
            source_max_edge_valence=source_stats.max_valence[component],
            output_max_edge_valence=output_stats.max_valence[component],
            source_direction_conflicts=source_stats.direction_conflicts[component],
            output_direction_conflicts=output_stats.direction_conflicts[component],
            source_orientation_conflicts=source_stats.orientation_conflicts[component],
            output_orientation_conflicts=output_stats.orientation_conflicts[component],
        ))
    source_boundary_sha256 = _canonical_digest(
        _boundary_payload(ordered_components, source_stats.boundaries)
    )
    output_boundary_sha256 = _canonical_digest(
        _boundary_payload(ordered_components, output_stats.boundaries)
    )
    values = dict(
        schema=1,
        strategy=STRATEGY,
        transfer=TRANSFER,
        quality_status="unverified",
        quality_claim=None,
        requested_ratio=ratio,
        source_sha256=_sha256(source_bytes),
        output_sha256=_sha256(output_bytes),
        triangles_before=len(source.triangles),
        global_target_triangles=global_target,
        triangles_after=len(output.triangles),
        retained_cycles=retained,
        remapped_cycles=len(output.triangles) - retained,
        source_component_count=component_count,
        covered_component_count=len(covered),
        source_boundary_edges=sum(len(value) for value in source_stats.boundaries.values()),
        output_boundary_edges=sum(len(value) for value in output_stats.boundaries.values()),
        source_boundary_sha256=source_boundary_sha256,
        output_boundary_sha256=output_boundary_sha256,
        source_nonmanifold_excess=sum(source_stats.nonmanifold_excess.values()),
        output_nonmanifold_excess=sum(output_stats.nonmanifold_excess.values()),
        source_max_edge_valence=max(source_stats.max_valence.values(), default=0),
        output_max_edge_valence=max(output_stats.max_valence.values(), default=0),
        source_direction_conflicts=sum(source_stats.direction_conflicts.values()),
        output_direction_conflicts=sum(output_stats.direction_conflicts.values()),
        source_orientation_conflicts=sum(source_stats.orientation_conflicts.values()),
        output_orientation_conflicts=sum(output_stats.orientation_conflicts.values()),
        source_corner_group_sha256=index.group_sha256,
        output_source_corner_ordinals=tuple(ordinals),
        materials=tuple(material_proofs),
        components=tuple(component_proofs),
        proof_sha256="0" * 64,
    )
    provisional = RemappedTopologyProof(**values)
    values["proof_sha256"] = _canonical_digest(
        remapped_topology_proof_payload(provisional, include_seal=False)
    )
    return RemappedTopologyProof(**values)
