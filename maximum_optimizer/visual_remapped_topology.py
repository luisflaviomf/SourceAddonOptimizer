from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
from typing import Literal

from .remapped_topology import (
    RemappedMaterialProof,
    _RemappedAnalysis,
    _boundary_payload,
    _canonical_digest,
    _is_sha,
    _sha256,
    _validate_remapped_topology_smd_common,
)


STRATEGY = "meshopt-remapped-visual-v1"
TRANSFER = "visual-remapped-topology-v1"

BoundaryPosition = tuple[str, str, str]
BoundaryEdge = tuple[BoundaryPosition, BoundaryPosition]
BoundarySet = tuple[BoundaryEdge, ...]


def _is_boundary_set(value: object) -> bool:
    if type(value) is not tuple:
        return False
    decoded_edges = []
    for edge in value:
        if type(edge) is not tuple or len(edge) != 2:
            return False
        decoded_positions = []
        for position in edge:
            if type(position) is not tuple or len(position) != 3:
                return False
            decoded = []
            for coordinate in position:
                if type(coordinate) is not str:
                    return False
                try:
                    parsed = float.fromhex(coordinate)
                except ValueError:
                    return False
                if not math.isfinite(parsed) or parsed.hex() != coordinate:
                    return False
                decoded.append(parsed)
            decoded_positions.append(tuple(decoded))
        decoded_edge = (decoded_positions[0], decoded_positions[1])
        if decoded_edge[0] > decoded_edge[1]:
            return False
        decoded_edges.append(decoded_edge)
    return value == _encode_boundary_set(decoded_edges)


def _encoded_boundary_payload(
    component: tuple[int, int], edges: BoundarySet,
) -> list[dict[str, object]]:
    return [{
        "component": list(component),
        "edges": [[list(left), list(right)] for left, right in edges],
    }]


def _encoded_boundary_digest(
    component: tuple[int, int], edges: BoundarySet,
) -> str:
    return _canonical_digest(_encoded_boundary_payload(component, edges))


@dataclass(frozen=True)
class VisualRemappedComponentProof:
    ordinal: int
    material_ordinal: int
    material: str
    source_first_triangle: int
    source_triangles: int
    output_triangles: int
    covered: Literal[True]
    boundary_changed: bool
    source_boundary_edges: int
    output_boundary_edges: int
    retained_boundary_edges: int
    removed_boundary_edges: int
    added_boundary_edges: int
    source_boundary_sha256: str
    output_boundary_sha256: str
    retained_boundary_sha256: str
    removed_boundary_sha256: str
    added_boundary_sha256: str
    source_boundary_set: BoundarySet
    output_boundary_set: BoundarySet
    retained_boundary_set: BoundarySet
    removed_boundary_set: BoundarySet
    added_boundary_set: BoundarySet
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
            "ordinal", "material_ordinal", "source_first_triangle",
            "source_triangles", "output_triangles", "source_boundary_edges",
            "output_boundary_edges", "retained_boundary_edges",
            "removed_boundary_edges", "added_boundary_edges",
            "source_nonmanifold_excess", "output_nonmanifold_excess",
            "source_max_edge_valence", "output_max_edge_valence",
            "source_direction_conflicts", "output_direction_conflicts",
            "source_orientation_conflicts", "output_orientation_conflicts",
        )
        digest_names = (
            "source_boundary_sha256", "output_boundary_sha256",
            "retained_boundary_sha256", "removed_boundary_sha256",
            "added_boundary_sha256",
        )
        boundary_names = (
            "source_boundary_set", "output_boundary_set",
            "retained_boundary_set", "removed_boundary_set",
            "added_boundary_set",
        )
        component = (self.material_ordinal, self.source_first_triangle)
        boundary_sets_valid = all(
            _is_boundary_set(getattr(self, name)) for name in boundary_names
        )
        source = set(self.source_boundary_set) if boundary_sets_valid else set()
        output = set(self.output_boundary_set) if boundary_sets_valid else set()
        retained = set(self.retained_boundary_set) if boundary_sets_valid else set()
        removed = set(self.removed_boundary_set) if boundary_sets_valid else set()
        added = set(self.added_boundary_set) if boundary_sets_valid else set()
        if (
            any(
                type(getattr(self, name)) is not int or getattr(self, name) < 0
                for name in integer_names
            )
            or type(self.material) is not str
            or not self.material
            or self.covered is not True
            or type(self.boundary_changed) is not bool
            or not 0 < self.output_triangles <= self.source_triangles
            or self.source_boundary_edges
            != self.retained_boundary_edges + self.removed_boundary_edges
            or self.output_boundary_edges
            != self.retained_boundary_edges + self.added_boundary_edges
            or self.boundary_changed
            != (self.removed_boundary_edges > 0 or self.added_boundary_edges > 0)
            or any(not _is_sha(getattr(self, name)) for name in digest_names)
            or not boundary_sets_valid
            or len(source) != self.source_boundary_edges
            or len(output) != self.output_boundary_edges
            or len(retained) != self.retained_boundary_edges
            or len(removed) != self.removed_boundary_edges
            or len(added) != self.added_boundary_edges
            or retained != source & output
            or removed != source - output
            or added != output - source
            or self.source_boundary_sha256
            != _encoded_boundary_digest(component, self.source_boundary_set)
            or self.output_boundary_sha256
            != _encoded_boundary_digest(component, self.output_boundary_set)
            or self.retained_boundary_sha256
            != _encoded_boundary_digest(component, self.retained_boundary_set)
            or self.removed_boundary_sha256
            != _encoded_boundary_digest(component, self.removed_boundary_set)
            or self.added_boundary_sha256
            != _encoded_boundary_digest(component, self.added_boundary_set)
            or self.source_max_edge_valence < 1
            or self.output_max_edge_valence < 1
            or self.output_nonmanifold_excess > self.source_nonmanifold_excess
            or self.output_max_edge_valence > self.source_max_edge_valence
            or self.output_direction_conflicts > self.source_direction_conflicts
            or self.output_orientation_conflicts > self.source_orientation_conflicts
        ):
            raise ValueError("visual remapped component proof is invalid")


def _aggregate_boundary_digest(
    components: tuple[VisualRemappedComponentProof, ...], field: str,
) -> str:
    payload = []
    for item in components:
        payload.extend(_encoded_boundary_payload(
            (item.material_ordinal, item.source_first_triangle),
            getattr(item, field),
        ))
    return _canonical_digest(payload)


_BOUNDARY_SET_FIELDS = (
    "source_boundary_set", "output_boundary_set", "retained_boundary_set",
    "removed_boundary_set", "added_boundary_set",
)


def _boundary_set_payload_value(edges: BoundarySet) -> list[list[list[str]]]:
    return [[list(left), list(right)] for left, right in edges]


def _boundary_set_from_payload(value: object) -> BoundarySet:
    if type(value) is not list:
        raise ValueError("visual remapped boundary set is invalid")
    try:
        result = tuple(
            (tuple(edge[0]), tuple(edge[1]))
            for edge in value
            if type(edge) is list and len(edge) == 2
        )
    except TypeError as exc:
        raise ValueError("visual remapped boundary set is invalid") from exc
    if len(result) != len(value) or not _is_boundary_set(result):
        raise ValueError("visual remapped boundary set is invalid")
    return result


def _encode_boundary_set(edges) -> BoundarySet:
    return tuple(
        (
            tuple(float(value).hex() for value in left),
            tuple(float(value).hex() for value in right),
        )
        for left, right in sorted(edges)
    )


@dataclass(frozen=True)
class VisualRemappedTopologyProof:
    schema: Literal[1]
    strategy: Literal["meshopt-remapped-visual-v1"]
    transfer: Literal["visual-remapped-topology-v1"]
    quality_status: Literal["unverified"]
    quality_claim: None
    requested_ratio: float
    source_sha256: str
    output_sha256: str
    triangles_before: int
    global_target_triangles: int
    target_reached: bool
    achieved_ratio: float
    triangles_after: int
    retained_cycles: int
    remapped_cycles: int
    source_component_count: int
    covered_component_count: int
    boundary_changed: bool
    source_boundary_edges: int
    output_boundary_edges: int
    retained_boundary_edges: int
    removed_boundary_edges: int
    added_boundary_edges: int
    source_boundary_sha256: str
    output_boundary_sha256: str
    retained_boundary_sha256: str
    removed_boundary_sha256: str
    added_boundary_sha256: str
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
    components: tuple[VisualRemappedComponentProof, ...]
    proof_sha256: str

    def __post_init__(self) -> None:
        integer_names = (
            "triangles_before", "global_target_triangles", "triangles_after",
            "retained_cycles", "remapped_cycles", "source_component_count",
            "covered_component_count", "source_boundary_edges",
            "output_boundary_edges", "retained_boundary_edges",
            "removed_boundary_edges", "added_boundary_edges",
            "source_nonmanifold_excess", "output_nonmanifold_excess",
            "source_max_edge_valence", "output_max_edge_valence",
            "source_direction_conflicts", "output_direction_conflicts",
            "source_orientation_conflicts", "output_orientation_conflicts",
        )
        digest_names = (
            "source_sha256", "output_sha256", "source_boundary_sha256",
            "output_boundary_sha256", "retained_boundary_sha256",
            "removed_boundary_sha256", "added_boundary_sha256",
            "source_corner_group_sha256", "proof_sha256",
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
            or type(self.target_reached) is not bool
            or type(self.achieved_ratio) is not float
            or not math.isfinite(self.achieved_ratio)
            or type(self.boundary_changed) is not bool
            or any(not _is_sha(getattr(self, name)) for name in digest_names)
            or any(
                type(getattr(self, name)) is not int or getattr(self, name) < 0
                for name in integer_names
            )
            or not 0 < self.triangles_after < self.triangles_before
            or self.global_target_triangles
            != max(
                len(self.materials),
                math.floor(self.triangles_before * self.requested_ratio),
            )
            or self.target_reached
            != (self.triangles_after <= self.global_target_triangles)
            or self.achieved_ratio != self.triangles_after / self.triangles_before
            or self.retained_cycles + self.remapped_cycles != self.triangles_after
            or self.covered_component_count != self.source_component_count
            or self.source_boundary_edges
            != self.retained_boundary_edges + self.removed_boundary_edges
            or self.output_boundary_edges
            != self.retained_boundary_edges + self.added_boundary_edges
            or self.boundary_changed
            != (self.removed_boundary_edges > 0 or self.added_boundary_edges > 0)
            or self.output_nonmanifold_excess > self.source_nonmanifold_excess
            or self.output_max_edge_valence > self.source_max_edge_valence
            or self.output_direction_conflicts > self.source_direction_conflicts
            or self.output_orientation_conflicts > self.source_orientation_conflicts
            or len(self.output_source_corner_ordinals) != self.triangles_after * 3
            or any(
                type(value) is not int
                or value < 0
                or value >= self.triangles_before * 3
                for value in self.output_source_corner_ordinals
            )
            or not self.materials
            or any(not isinstance(item, RemappedMaterialProof) for item in self.materials)
            or any(
                not isinstance(item, VisualRemappedComponentProof)
                for item in self.components
            )
            or tuple(item.ordinal for item in self.materials)
            != tuple(range(len(self.materials)))
            or tuple(item.ordinal for item in self.components)
            != tuple(range(len(self.components)))
            or len(self.components) != self.source_component_count
            or len({
                (item.material_ordinal, item.source_first_triangle)
                for item in self.components
            }) != len(self.components)
            or tuple(
                (item.material_ordinal, item.source_first_triangle)
                for item in self.components
            ) != tuple(sorted(
                (item.material_ordinal, item.source_first_triangle)
                for item in self.components
            ))
            or len({item.material for item in self.materials}) != len(self.materials)
            or sum(item.triangles_before for item in self.materials)
            != self.triangles_before
            or sum(item.triangles_after for item in self.materials)
            != self.triangles_after
            or sum(item.source_components for item in self.materials)
            != self.source_component_count
            or sum(item.covered_components for item in self.materials)
            != self.covered_component_count
            or sum(item.source_triangles for item in self.components)
            != self.triangles_before
            or sum(item.output_triangles for item in self.components)
            != self.triangles_after
            or sum(item.source_boundary_edges for item in self.components)
            != self.source_boundary_edges
            or sum(item.output_boundary_edges for item in self.components)
            != self.output_boundary_edges
            or sum(item.retained_boundary_edges for item in self.components)
            != self.retained_boundary_edges
            or sum(item.removed_boundary_edges for item in self.components)
            != self.removed_boundary_edges
            or sum(item.added_boundary_edges for item in self.components)
            != self.added_boundary_edges
            or self.boundary_changed != any(item.boundary_changed for item in self.components)
            or self.source_boundary_sha256
            != _aggregate_boundary_digest(self.components, "source_boundary_set")
            or self.output_boundary_sha256
            != _aggregate_boundary_digest(self.components, "output_boundary_set")
            or self.retained_boundary_sha256
            != _aggregate_boundary_digest(self.components, "retained_boundary_set")
            or self.removed_boundary_sha256
            != _aggregate_boundary_digest(self.components, "removed_boundary_set")
            or self.added_boundary_sha256
            != _aggregate_boundary_digest(self.components, "added_boundary_set")
            or sum(item.source_nonmanifold_excess for item in self.components)
            != self.source_nonmanifold_excess
            or sum(item.output_nonmanifold_excess for item in self.components)
            != self.output_nonmanifold_excess
            or max(
                (item.source_max_edge_valence for item in self.components), default=0
            )
            != self.source_max_edge_valence
            or max(
                (item.output_max_edge_valence for item in self.components), default=0
            )
            != self.output_max_edge_valence
            or sum(item.source_direction_conflicts for item in self.components)
            != self.source_direction_conflicts
            or sum(item.output_direction_conflicts for item in self.components)
            != self.output_direction_conflicts
            or sum(item.source_orientation_conflicts for item in self.components)
            != self.source_orientation_conflicts
            or sum(item.output_orientation_conflicts for item in self.components)
            != self.output_orientation_conflicts
            or any(
                item.material_ordinal >= len(self.materials)
                or self.materials[item.material_ordinal].material != item.material
                for item in self.components
            )
            or any(
                sum(
                    component.material_ordinal == item.ordinal
                    for component in self.components
                )
                != item.source_components
                for item in self.materials
            )
        ):
            raise ValueError("visual remapped topology proof relationships are invalid")


def visual_remapped_topology_proof_payload(
    proof: VisualRemappedTopologyProof, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(proof, VisualRemappedTopologyProof):
        raise TypeError("visual remapped topology proof is invalid")
    payload = asdict(proof)
    payload["output_source_corner_ordinals"] = list(
        proof.output_source_corner_ordinals
    )
    payload["materials"] = [asdict(item) for item in proof.materials]
    payload["components"] = []
    for item in proof.components:
        component = asdict(item)
        for field in _BOUNDARY_SET_FIELDS:
            component[field] = _boundary_set_payload_value(getattr(item, field))
        payload["components"].append(component)
    if not include_seal:
        payload.pop("proof_sha256")
    return payload


def visual_remapped_topology_proof_from_payload(
    value: object,
) -> VisualRemappedTopologyProof:
    expected = set(VisualRemappedTopologyProof.__dataclass_fields__)
    material_fields = set(RemappedMaterialProof.__dataclass_fields__)
    component_fields = set(VisualRemappedComponentProof.__dataclass_fields__)
    if type(value) is not dict or set(value) != expected:
        raise ValueError("visual remapped topology proof fields are invalid")
    raw_materials = value["materials"]
    raw_components = value["components"]
    raw_ordinals = value["output_source_corner_ordinals"]
    if (
        type(raw_materials) is not list
        or not raw_materials
        or any(
            type(item) is not dict or set(item) != material_fields
            for item in raw_materials
        )
        or type(raw_components) is not list
        or not raw_components
        or any(
            type(item) is not dict or set(item) != component_fields
            for item in raw_components
        )
        or type(raw_ordinals) is not list
    ):
        raise ValueError("visual remapped topology proof collections are invalid")
    copied = dict(value)
    copied["materials"] = tuple(
        RemappedMaterialProof(**item) for item in raw_materials
    )
    components = []
    for item in raw_components:
        component = dict(item)
        for field in _BOUNDARY_SET_FIELDS:
            component[field] = _boundary_set_from_payload(component[field])
        components.append(VisualRemappedComponentProof(**component))
    copied["components"] = tuple(components)
    copied["output_source_corner_ordinals"] = tuple(raw_ordinals)
    proof = VisualRemappedTopologyProof(**copied)
    if proof.proof_sha256 != _canonical_digest(
        visual_remapped_topology_proof_payload(proof, include_seal=False)
    ):
        raise ValueError("visual remapped topology proof seal is invalid")
    return proof


def _set_payload(component, edges):
    return _boundary_payload((component,), {component: frozenset(edges)})


def validate_visual_remapped_topology_smd(
    source_text: str, output_text: str, requested_ratio: float,
) -> VisualRemappedTopologyProof:
    analysis = _validate_remapped_topology_smd_common(
        source_text,
        output_text,
        requested_ratio,
        require_exact_boundary=False,
    )
    if not isinstance(analysis, _RemappedAnalysis):
        raise RuntimeError("visual remapped validator returned invalid analysis")
    ordered_components = tuple(sorted(analysis.expected_components))
    source_component_counts = Counter(analysis.index.component_for_triangle)
    output_component_counts = Counter(analysis.output_components)
    material_proofs = []
    for ordinal, material in enumerate(analysis.source_materials):
        material_components = analysis.index.components_by_material[material]
        material_proofs.append(RemappedMaterialProof(
            ordinal=ordinal,
            material=material,
            triangles_before=analysis.source_counts[material],
            triangles_after=analysis.output_counts[material],
            source_components=len(material_components),
            covered_components=sum(
                component in analysis.covered for component in material_components
            ),
        ))
    component_proofs = []
    retained_sets = {}
    removed_sets = {}
    added_sets = {}
    for ordinal, component in enumerate(ordered_components):
        source_boundary = analysis.source_stats.boundaries[component]
        output_boundary = analysis.output_stats.boundaries.get(component, frozenset())
        retained = source_boundary & output_boundary
        removed = source_boundary - output_boundary
        added = output_boundary - source_boundary
        retained_sets[component] = retained
        removed_sets[component] = removed
        added_sets[component] = added
        component_proofs.append(VisualRemappedComponentProof(
            ordinal=ordinal,
            material_ordinal=component[0],
            material=analysis.source_materials[component[0]],
            source_first_triangle=component[1],
            source_triangles=source_component_counts[component],
            output_triangles=output_component_counts[component],
            covered=True,
            boundary_changed=bool(removed or added),
            source_boundary_edges=len(source_boundary),
            output_boundary_edges=len(output_boundary),
            retained_boundary_edges=len(retained),
            removed_boundary_edges=len(removed),
            added_boundary_edges=len(added),
            source_boundary_sha256=_canonical_digest(
                _set_payload(component, source_boundary)
            ),
            output_boundary_sha256=_canonical_digest(
                _set_payload(component, output_boundary)
            ),
            retained_boundary_sha256=_canonical_digest(
                _set_payload(component, retained)
            ),
            removed_boundary_sha256=_canonical_digest(
                _set_payload(component, removed)
            ),
            added_boundary_sha256=_canonical_digest(
                _set_payload(component, added)
            ),
            source_boundary_set=_encode_boundary_set(source_boundary),
            output_boundary_set=_encode_boundary_set(output_boundary),
            retained_boundary_set=_encode_boundary_set(retained),
            removed_boundary_set=_encode_boundary_set(removed),
            added_boundary_set=_encode_boundary_set(added),
            source_nonmanifold_excess=analysis.source_stats.nonmanifold_excess[component],
            output_nonmanifold_excess=analysis.output_stats.nonmanifold_excess[component],
            source_max_edge_valence=analysis.source_stats.max_valence[component],
            output_max_edge_valence=analysis.output_stats.max_valence[component],
            source_direction_conflicts=analysis.source_stats.direction_conflicts[component],
            output_direction_conflicts=analysis.output_stats.direction_conflicts[component],
            source_orientation_conflicts=analysis.source_stats.orientation_conflicts[component],
            output_orientation_conflicts=analysis.output_stats.orientation_conflicts[component],
        ))
    source_boundaries = analysis.source_stats.boundaries
    output_boundaries = analysis.output_stats.boundaries
    values = dict(
        schema=1,
        strategy=STRATEGY,
        transfer=TRANSFER,
        quality_status="unverified",
        quality_claim=None,
        requested_ratio=analysis.ratio,
        source_sha256=_sha256(analysis.source_bytes),
        output_sha256=_sha256(analysis.output_bytes),
        triangles_before=len(analysis.source.triangles),
        global_target_triangles=analysis.global_target,
        target_reached=len(analysis.output.triangles) <= analysis.global_target,
        achieved_ratio=len(analysis.output.triangles) / len(analysis.source.triangles),
        triangles_after=len(analysis.output.triangles),
        retained_cycles=analysis.retained,
        remapped_cycles=len(analysis.output.triangles) - analysis.retained,
        source_component_count=analysis.component_count,
        covered_component_count=len(analysis.covered),
        boundary_changed=any(item.boundary_changed for item in component_proofs),
        source_boundary_edges=sum(len(value) for value in source_boundaries.values()),
        output_boundary_edges=sum(len(value) for value in output_boundaries.values()),
        retained_boundary_edges=sum(len(value) for value in retained_sets.values()),
        removed_boundary_edges=sum(len(value) for value in removed_sets.values()),
        added_boundary_edges=sum(len(value) for value in added_sets.values()),
        source_boundary_sha256=_canonical_digest(
            _boundary_payload(ordered_components, source_boundaries)
        ),
        output_boundary_sha256=_canonical_digest(
            _boundary_payload(ordered_components, output_boundaries)
        ),
        retained_boundary_sha256=_canonical_digest(
            _boundary_payload(ordered_components, retained_sets)
        ),
        removed_boundary_sha256=_canonical_digest(
            _boundary_payload(ordered_components, removed_sets)
        ),
        added_boundary_sha256=_canonical_digest(
            _boundary_payload(ordered_components, added_sets)
        ),
        source_nonmanifold_excess=sum(
            analysis.source_stats.nonmanifold_excess.values()
        ),
        output_nonmanifold_excess=sum(
            analysis.output_stats.nonmanifold_excess.values()
        ),
        source_max_edge_valence=max(
            analysis.source_stats.max_valence.values(), default=0
        ),
        output_max_edge_valence=max(
            analysis.output_stats.max_valence.values(), default=0
        ),
        source_direction_conflicts=sum(
            analysis.source_stats.direction_conflicts.values()
        ),
        output_direction_conflicts=sum(
            analysis.output_stats.direction_conflicts.values()
        ),
        source_orientation_conflicts=sum(
            analysis.source_stats.orientation_conflicts.values()
        ),
        output_orientation_conflicts=sum(
            analysis.output_stats.orientation_conflicts.values()
        ),
        source_corner_group_sha256=analysis.index.group_sha256,
        output_source_corner_ordinals=analysis.ordinals,
        materials=tuple(material_proofs),
        components=tuple(component_proofs),
        proof_sha256="0" * 64,
    )
    provisional = VisualRemappedTopologyProof(**values)
    values["proof_sha256"] = _canonical_digest(
        visual_remapped_topology_proof_payload(provisional, include_seal=False)
    )
    return VisualRemappedTopologyProof(**values)
