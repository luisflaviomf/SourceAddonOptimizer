from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import threading
from typing import Mapping

from .adaptive_metrics_factory import _bound_graph_digest, _root_graph
from .composite import (
    build_adaptive_direct_state_inventory,
    build_adaptive_direct_metric_occurrence_classification,
    build_adaptive_direct_state_inventory_row,
    candidate_spec_sha256,
    optimizer_contract_sha256,
    revalidate_recovery_snapshot,
)
from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveDirectStateInventory,
    CandidateSpec,
    FamilyManifest,
    RecoverySourceSnapshot,
    SourceFileProof,
    adaptive_metric_occurrence_key,
    require_canonical_relative,
)
from .qc_graph import QcGraph
from .qc_states import QcActiveOccurrence, enumerate_qc_states
from .reporting import canonical_json
from .smd_state_contracts import (
    SmdAnimationPairInput,
    build_smd_equivalence_contract,
    build_smd_pose_contract,
    build_smd_skeleton_pair_contract,
    build_smd_skeleton_contract,
)
from .source_components import (
    SourceComponentManifest,
    source_component_manifest_payload,
)
from .source_materials import (
    SourceUnionMaterialContract,
    source_union_material_contract_payload,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DEPENDENCY_LIMIT = 256


def _canonical_tuple(values: object, label: str) -> tuple[str, ...]:
    result = tuple(values) if isinstance(values, (tuple, list)) else ()
    if (
        not result or len(result) > _DEPENDENCY_LIMIT
        or any(type(item) is not str or not item for item in result)
        or result != tuple(sorted(result, key=lambda item: (item.casefold(), item)))
        or len({item.casefold() for item in result}) != len(result)
    ):
        raise ValueError(f"{label} are not a complete canonical dependency set")
    return result


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a SHA-256 digest")
    return value


@dataclass(frozen=True)
class AdaptiveStateSourceDependencies:
    source_identity: str
    source_size: int
    source_sha256: str
    component_manifest: SourceComponentManifest
    material_contract: SourceUnionMaterialContract
    dependency_sha256: str

    def __post_init__(self) -> None:
        identity = require_canonical_relative(
            self.source_identity, "adaptive state dependency source"
        )
        if type(self.source_size) is not int or self.source_size < 0:
            raise ValueError("adaptive state dependency source size is invalid")
        _sha256(self.source_sha256, "adaptive state dependency source hash")
        if not isinstance(self.component_manifest, SourceComponentManifest) or not isinstance(
            self.material_contract, SourceUnionMaterialContract
        ):
            raise TypeError("adaptive state dependencies require typed component/material contracts")
        components = _canonical_tuple(
            tuple(item.component_key for item in self.component_manifest.components),
            "component dependencies",
        )
        materials = _canonical_tuple(
            tuple(item.material_region_key for item in self.material_contract.bindings),
            "material dependencies",
        )
        if (
            self.component_manifest.source_sha256 != self.source_sha256
            or self.material_contract.source_identity != identity
            or self.material_contract.filtered_source_sha256
            != self.component_manifest.filtered_source_sha256
        ):
            raise ValueError("adaptive state typed dependencies differ from source bytes or identity")
        _sha256(self.dependency_sha256, "adaptive state dependency seal")
        object.__setattr__(self, "source_identity", identity)
        if hashlib.sha256(canonical_json(_dependency_payload(self)).encode("utf-8")).hexdigest() != self.dependency_sha256:
            raise ValueError("adaptive state dependency seal mismatch")

    @property
    def component_keys(self) -> tuple[str, ...]:
        return tuple(item.component_key for item in self.component_manifest.components)

    @property
    def material_region_keys(self) -> tuple[str, ...]:
        return tuple(item.material_region_key for item in self.material_contract.bindings)

    @property
    def component_manifest_sha256(self) -> str:
        return self.component_manifest.component_manifest_sha256

    @property
    def material_contract_sha256(self) -> str:
        return self.material_contract.material_contract_sha256

    @classmethod
    def create(cls, **values) -> "AdaptiveStateSourceDependencies":
        raw = dict(values)
        if set(raw) != {
            "source_identity", "source_size", "source_sha256",
            "component_manifest", "material_contract",
        }:
            raise TypeError("adaptive state dependencies require typed component/material authority")
        provisional = object.__new__(cls)
        for name, value in raw.items():
            object.__setattr__(provisional, name, value)
        raw["dependency_sha256"] = hashlib.sha256(
            canonical_json(_dependency_payload(provisional)).encode("utf-8")
        ).hexdigest()
        return cls(**raw)


def _dependency_payload(value: AdaptiveStateSourceDependencies) -> dict[str, object]:
    return {
        "source_identity": value.source_identity,
        "source_size": value.source_size,
        "source_sha256": value.source_sha256,
        "component_manifest": source_component_manifest_payload(value.component_manifest),
        "material_contract": source_union_material_contract_payload(value.material_contract),
    }


def _absolute_equal(left: Path, right: Path) -> bool:
    return Path(os.path.abspath(left)) == Path(os.path.abspath(right))


def _visual_proofs(snapshot: RecoverySourceSnapshot) -> dict[str, SourceFileProof]:
    result = {
        item.file_identity: item
        for item in snapshot.source_manifest.files
        if item.kind == "visual-source"
    }
    if not result:
        raise ValueError("adaptive state inventory has no visual source proofs")
    return result


def _relative(root: Path, path: Path, label: str) -> str:
    try:
        value = Path(path).resolve(strict=True).relative_to(
            Path(root).resolve(strict=True)
        ).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} escapes the sealed source root") from exc
    return require_canonical_relative(value, label)


def _graph_occurrences(
    graph: QcGraph, proofs: Mapping[str, SourceFileProof]
) -> dict[str, tuple[tuple[str, str, int, str], ...]]:
    by_path = {item.relative_path: item for item in proofs.values()}
    result: dict[str, list[tuple[str, str, int, str]]] = {
        identity: [] for identity in proofs
    }
    for reference in graph.references:
        if reference.role != "visual":
            continue
        relative = _relative(graph.family_root, reference.source_path, "visual source")
        proof = by_path.get(relative)
        if proof is None:
            raise ValueError("QC visual occurrence has no sealed source proof")
        graph_relative = _relative(
            graph.family_root, reference.graph_file, "visual occurrence graph"
        )
        result[proof.file_identity].append((
            graph_relative, reference.directive, reference.line, proof.file_identity,
        ))
    canonical: dict[str, tuple[tuple[str, str, int, str], ...]] = {}
    for identity, values in result.items():
        ordered = tuple(sorted(values, key=lambda item: (
            item[0].casefold(), item[1], item[2], item[3].casefold()
        )))
        if not ordered or len(set(ordered)) != len(ordered):
            raise ValueError("QC visual occurrence union is incomplete or ambiguous")
        canonical[identity] = ordered
    return canonical


def _validate_metrics_sources(
    metrics: AdaptiveCandidateMetricsProof,
    original_proofs: Mapping[str, SourceFileProof],
    candidate_proofs: Mapping[str, SourceFileProof],
    occurrences: Mapping[str, tuple[tuple[str, str, int, str], ...]],
) -> None:
    metric_ids = tuple(item.source_identity for item in metrics.sources)
    expected_ids = tuple(sorted(original_proofs, key=lambda item: (item.casefold(), item)))
    if metric_ids != expected_ids or set(candidate_proofs) != set(expected_ids):
        raise ValueError("adaptive metrics/source proof union differs")
    for metric in metrics.sources:
        source = original_proofs[metric.source_identity]
        output = candidate_proofs[metric.source_identity]
        if (
            (metric.source_relative_path, metric.source_size, metric.source_sha256)
            != (source.relative_path, source.size, source.sha256)
            or (metric.output_relative_path, metric.output_size, metric.output_sha256)
            != (output.relative_path, output.size, output.sha256)
        ):
            raise ValueError("adaptive metrics source bytes differ from sealed proofs")
        metric_occurrences = tuple(
            (item.graph_relative_path, item.directive, item.line, item.logical_path)
            for item in metric.occurrences
        )
        if metric_occurrences != occurrences[metric.source_identity]:
            raise ValueError("adaptive metrics occurrence union differs from sealed QC graph")


def _validate_dependencies(
    dependencies: Mapping[str, AdaptiveStateSourceDependencies],
    proofs: Mapping[str, SourceFileProof],
) -> dict[str, AdaptiveStateSourceDependencies]:
    if not isinstance(dependencies, Mapping):
        raise TypeError("adaptive state dependencies must be a mapping")
    copied = dict(dependencies)
    if set(copied) != set(proofs):
        raise ValueError("adaptive state dependency/source union differs")
    for identity, dependency in copied.items():
        if not isinstance(dependency, AdaptiveStateSourceDependencies):
            raise TypeError("adaptive state dependency value is not typed")
        proof = proofs[identity]
        if dependency.source_identity != identity:
            raise ValueError("adaptive state dependency identity differs")
        if (dependency.source_size, dependency.source_sha256) != (
            proof.size, proof.sha256
        ):
            raise ValueError("adaptive state dependency bytes differ from source proof")
    return copied


def _validate_animation_pairs(
    pairs: Mapping[str, SmdAnimationPairInput],
    original_snapshot: RecoverySourceSnapshot,
    candidate_snapshot: RecoverySourceSnapshot,
) -> dict[str, SmdAnimationPairInput]:
    if not isinstance(pairs, Mapping):
        raise TypeError("adaptive animation pairs must be a mapping")
    copied = dict(pairs)
    original_members = {
        item: item for item in original_snapshot.source_manifest.files
        if item.kind == "animation-source"
    }
    candidate_members = {
        item: item for item in candidate_snapshot.source_manifest.files
        if item.kind == "animation-source"
    }
    original_root = Path(original_snapshot.source_root)
    candidate_root = Path(candidate_snapshot.source_root)
    for pair in copied.values():
        if not isinstance(pair, SmdAnimationPairInput):
            raise TypeError("adaptive animation pair is not typed")
        if (
            pair.original_proof not in original_members
            or pair.candidate_proof not in candidate_members
            or not _absolute_equal(pair.original_root, original_root)
            or not _absolute_equal(pair.candidate_root, candidate_root)
        ):
            raise ValueError("adaptive animation pair is not an exact snapshot member")
        expected_original = original_root.joinpath(
            *PurePosixPath(pair.original_proof.relative_path).parts
        )
        expected_candidate = candidate_root.joinpath(
            *PurePosixPath(pair.candidate_proof.relative_path).parts
        )
        if (
            not _absolute_equal(pair.original_path, expected_original)
            or not _absolute_equal(pair.candidate_path, expected_candidate)
        ):
            raise ValueError("adaptive animation pair path differs from snapshot proof")
    return copied


def _occurrence_key(state_key: str, active: QcActiveOccurrence) -> str:
    payload = {
        "state_key": state_key,
        "graph_relative_path": active.graph_relative_path,
        "directive": active.directive,
        "line": active.line,
        "source_identity": active.source_identity,
        "occurrence_ordinal": active.occurrence_ordinal,
        "base_occurrence_ordinal": active.base_occurrence_ordinal,
        "replacement_original_identity": active.replacement_original_identity,
    }
    return "occ-" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()


def build_production_adaptive_direct_state_inventory(
    *,
    manifest: FamilyManifest,
    spec: CandidateSpec,
    candidate_cache_digest: str,
    original_snapshot: RecoverySourceSnapshot,
    candidate_snapshot: RecoverySourceSnapshot,
    metrics_proof: AdaptiveCandidateMetricsProof,
    dependencies: Mapping[str, AdaptiveStateSourceDependencies],
    animation_pairs: Mapping[str, SmdAnimationPairInput],
    cancel_event: threading.Event | None = None,
) -> AdaptiveDirectStateInventory:
    """Build the fail-closed active-state inventory from sealed production inputs."""
    if not isinstance(manifest, FamilyManifest) or not isinstance(spec, CandidateSpec):
        raise TypeError("adaptive state inventory production identity is invalid")
    if spec.strategy != "blender-adaptive-v1":
        raise ValueError("adaptive state inventory requires blender-adaptive-v1")
    if (
        not isinstance(original_snapshot, RecoverySourceSnapshot)
        or not isinstance(candidate_snapshot, RecoverySourceSnapshot)
        or not isinstance(metrics_proof, AdaptiveCandidateMetricsProof)
    ):
        raise TypeError("adaptive state inventory requires typed sealed evidence")
    if original_snapshot.kind != "original" or candidate_snapshot.kind != "candidate":
        raise ValueError("adaptive state inventory snapshot roles are invalid")
    if not _absolute_equal(manifest.source_dir, original_snapshot.source_root):
        raise ValueError("original snapshot root differs from family manifest")

    contract = optimizer_contract_sha256(spec)
    for snapshot in (original_snapshot, candidate_snapshot):
        if (
            snapshot.family_id != manifest.family_id
            or snapshot.family_input_sha256 != manifest.input_hash
            or snapshot.optimizer_contract_sha256 != contract
        ):
            raise ValueError("adaptive state inventory snapshot binding differs")
    if (
        original_snapshot.whole_profile_sha256
        != candidate_snapshot.whole_profile_sha256
        or original_snapshot.focused_profile_sha256
        != candidate_snapshot.focused_profile_sha256
        or original_snapshot.dependency_proof_sha256
        != candidate_snapshot.dependency_proof_sha256
        or candidate_snapshot.candidate_id != spec.candidate_id
        or candidate_snapshot.candidate_cache_digest != candidate_cache_digest
    ):
        raise ValueError("adaptive state inventory snapshot provenance differs")

    spec_digest = candidate_spec_sha256(spec)
    if (
        metrics_proof.family_id != manifest.family_id
        or metrics_proof.family_input_sha256 != manifest.input_hash
        or metrics_proof.candidate_id != spec.candidate_id
        or metrics_proof.candidate_cache_digest != candidate_cache_digest
        or metrics_proof.base_spec_sha256 != spec_digest
        or metrics_proof.source_manifest_sha256
        != candidate_snapshot.source_manifest.digest
        or metrics_proof.source_snapshot_sha256
        != candidate_snapshot.snapshot_sha256
    ):
        raise ValueError("adaptive metrics/inventory production binding differs")

    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(candidate_snapshot, cancel_event)
    original_graph = _root_graph(
        Path(original_snapshot.source_root), manifest.model_rel, optimized=False,
        source_manifest=original_snapshot.source_manifest,
        cancel_event=cancel_event,
    )
    candidate_graph = _root_graph(
        Path(candidate_snapshot.source_root), manifest.model_rel, optimized=True,
        source_manifest=candidate_snapshot.source_manifest,
        cancel_event=cancel_event,
    )
    if (
        _bound_graph_digest(
            original_graph, original_snapshot.source_manifest, cancel_event
        ) != metrics_proof.original_graph_sha256
        or _bound_graph_digest(
            candidate_graph, candidate_snapshot.source_manifest, cancel_event
        ) != metrics_proof.candidate_graph_sha256
    ):
        raise ValueError("adaptive state inventory QC graph binding differs")

    # This bound is deliberately evaluated before any visual SMD contract read.
    states = enumerate_qc_states(original_graph)
    original_proofs = _visual_proofs(original_snapshot)
    candidate_proofs = _visual_proofs(candidate_snapshot)
    occurrences = _graph_occurrences(original_graph, original_proofs)
    _validate_metrics_sources(
        metrics_proof, original_proofs, candidate_proofs, occurrences
    )
    bound_dependencies = _validate_dependencies(dependencies, original_proofs)
    bound_pairs = _validate_animation_pairs(
        animation_pairs, original_snapshot, candidate_snapshot,
    )
    if not set(bound_pairs).issubset(original_proofs):
        raise ValueError("adaptive animation pair/source union differs")

    contracts = {}
    for identity in sorted(original_proofs, key=lambda item: (item.casefold(), item)):
        proof = original_proofs[identity]
        source_path = Path(original_snapshot.source_root).joinpath(
            *PurePosixPath(proof.relative_path).parts
        )
        candidate_proof = candidate_proofs[identity]
        candidate_path = Path(candidate_snapshot.source_root).joinpath(
            *PurePosixPath(candidate_proof.relative_path).parts
        )
        skeleton = build_smd_skeleton_contract(
            source_path, Path(original_snapshot.source_root), proof, cancel_event
        )
        candidate_skeleton = build_smd_skeleton_contract(
            candidate_path, Path(candidate_snapshot.source_root), candidate_proof,
            cancel_event,
        )
        skeleton_pair = build_smd_skeleton_pair_contract(
            skeleton, candidate_skeleton,
        )
        pose = build_smd_pose_contract(
            skeleton, animation_pair=bound_pairs.get(identity),
            cancel_event=cancel_event,
        )
        equivalence = build_smd_equivalence_contract(
            identity, proof, skeleton, pose
        )
        contracts[identity] = (skeleton_pair, pose, equivalence)

    rows = []
    for state in states:
        for active in state.active:
            proof = original_proofs.get(active.source_identity)
            if proof is None:
                raise ValueError("active QC state source is outside complete metric union")
            dependency = bound_dependencies[active.source_identity]
            skeleton_pair, pose, equivalence = contracts[active.source_identity]
            rows.append(build_adaptive_direct_state_inventory_row(
                occurrence_key=_occurrence_key(state.state_key, active),
                source_identity=active.source_identity,
                graph_relative_path=active.graph_relative_path,
                directive=active.directive,
                line=active.line,
                state_key=state.state_key,
                bodygroup_key=state.bodygroup_key,
                lod_key=state.lod_key,
                skin_key=state.skin_key,
                source_size=proof.size,
                source_sha256=proof.sha256,
                component_keys=dependency.component_keys,
                material_region_keys=dependency.material_region_keys,
                skeleton_contract_sha256=skeleton_pair.skeleton_pair_sha256,
                pose_keys=pose.pose_keys,
                component_manifest_sha256=dependency.component_manifest_sha256,
                material_contract_sha256=dependency.material_contract_sha256,
                pose_contract_sha256=pose.pose_contract_sha256,
                equivalence_class_sha256=equivalence.equivalence_class_sha256,
            ))
    if {item.source_identity for item in rows} != set(original_proofs):
        raise ValueError("active QC state/source union differs from complete metric union")
    rows.sort(key=lambda item: (item.occurrence_key.casefold(), item.occurrence_key))
    row_keys_by_provenance: dict[tuple[str, str, int, str], list[str]] = {}
    for row in rows:
        provenance = (
            row.graph_relative_path, row.directive, row.line, row.source_identity,
        )
        row_keys_by_provenance.setdefault(provenance, []).append(row.occurrence_key)
    active_ordinals = {
        active.occurrence_ordinal for state in states for active in state.active
    }
    visual_references = tuple(
        (ordinal, reference) for ordinal, reference in enumerate(original_graph.references)
        if reference.role == "visual"
    )
    metric_occurrences = []
    for visual_index, (ordinal, reference) in enumerate(visual_references):
        relative = _relative(
            original_graph.family_root, reference.source_path, "classified visual source",
        )
        proof = next(
            (item for item in original_proofs.values() if item.relative_path == relative),
            None,
        )
        if proof is None:
            raise ValueError("classified metric occurrence has no sealed source proof")
        provenance = (
            _relative(
                original_graph.family_root, reference.graph_file,
                "classified visual graph",
            ),
            reference.directive, reference.line, proof.file_identity,
        )
        row_keys = tuple(sorted(
            row_keys_by_provenance.get(provenance, ()),
            key=lambda item: (item.casefold(), item),
        ))
        if ordinal in active_ordinals:
            classification = "active-renderable-v1"
            if not row_keys:
                raise ValueError("active QC occurrence has no state-expanded row mapping")
        else:
            classification = "lod-original-selector-nonrenderable-v1"
            next_reference = (
                visual_references[visual_index + 1]
                if visual_index + 1 < len(visual_references) else None
            )
            if (
                row_keys or reference.directive != "$lod/replacemodel"
                or next_reference is None or next_reference[0] != ordinal + 1
                or next_reference[0] not in active_ordinals
                or next_reference[1].directive != reference.directive
                or next_reference[1].line != reference.line
                or next_reference[1].graph_file != reference.graph_file
                or next_reference[1].group != reference.group
            ):
                raise ValueError("nonrenderable QC metric occurrence is not an exact LOD original selector")
        metric_occurrences.append(
            build_adaptive_direct_metric_occurrence_classification(
                metric_occurrence_key=adaptive_metric_occurrence_key(*provenance),
                source_identity=proof.file_identity,
                graph_relative_path=provenance[0], directive=provenance[1],
                line=provenance[2], logical_path=provenance[3],
                classification=classification,
                active_row_occurrence_keys=row_keys,
            )
        )
    metric_occurrences.sort(
        key=lambda item: (item.metric_occurrence_key.casefold(), item.metric_occurrence_key)
    )
    inventory = build_adaptive_direct_state_inventory(
        family_id=manifest.family_id,
        family_input_sha256=manifest.input_hash,
        base_candidate_id=spec.candidate_id,
        base_spec_sha256=spec_digest,
        base_cache_digest=candidate_cache_digest,
        base_source_manifest_sha256=candidate_snapshot.source_manifest.digest,
        base_source_snapshot_sha256=candidate_snapshot.snapshot_sha256,
        complete_source_identities=tuple(sorted(
            original_proofs, key=lambda item: (item.casefold(), item)
        )),
        rows=tuple(rows),
        metric_occurrences=tuple(metric_occurrences),
    )
    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(candidate_snapshot, cancel_event)
    return inventory
