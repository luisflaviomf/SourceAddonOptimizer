from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import threading
from types import MappingProxyType

from .adaptive_metrics_factory import (
    _bound_graph_digest,
    _normalized_model,
    _qc_model_name,
    _root_graph,
    build_production_adaptive_candidate_metrics_proof,
)
from .adaptive_state_inventory import (
    AdaptiveStateSourceDependencies,
    build_production_adaptive_direct_state_inventory,
)
from .candidates import CandidateBuild
from .composite import (
    _safe_tree_files,
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    optimizer_contract_sha256,
    revalidate_recovery_snapshot,
)
from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveDirectCoverageManifest,
    AdaptiveDirectStateInventory,
    CandidateEvaluation,
    FamilyManifest,
    RecoverySourceSnapshot,
    SourceFileProof,
    validation_result_payload,
)
from .focused_cache import _read_regular_no_follow
from .monaco_selection import (
    ExactFallbackSelection,
    RetainedMonacoBaseProof,
    build_retained_monaco_base_proof,
    select_exact_fallback_sources,
    select_monaco_base,
)
from .qc_graph import (
    QcGraph, _block_bounds, _lex, _line_values, _source_tokens, parse_qc_graph,
)
from .production_adapters import SourceUnionPoseBinding
from .smd_state_contracts import (
    SmdAnimationPairInput,
    _parse_nodes_and_frames,
    build_smd_pose_contract,
    build_smd_skeleton_contract,
)
from .smd_contract import direct_smd_material_counts, prefilter_direct_degenerate_smd
from .source_components import SourceComponentManifest, build_source_component_manifest
from .source_materials import (
    SourceUnionMaterialContract,
    build_source_union_material_contract,
    require_current_source_union_material_contract,
)


_SOURCE_BYTE_LIMIT = 2 * 1024 ** 3
_CANDIDATE_METRICS_BYTE_LIMIT = 64 * 1024 * 1024


@dataclass(frozen=True)
class ProductionAnimationAnchor:
    occurrence_index: int
    directive: str
    pair: SmdAnimationPairInput
    representative_frame: int

    def __post_init__(self) -> None:
        if (
            type(self.occurrence_index) is not int or self.occurrence_index < 0
            or self.directive not in {"$sequence", "$animation"}
            or not isinstance(self.pair, SmdAnimationPairInput)
            or type(self.representative_frame) is not int
            or self.representative_frame <= 0
        ):
            raise ValueError("production animation anchor is invalid")


def _absolute(value: Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def _original_root_graph(
    manifest: FamilyManifest, cancel_event: threading.Event | None,
) -> QcGraph:
    root = _absolute(manifest.source_dir)
    paths = _safe_tree_files(root, cancel_event)
    candidates: list[Path] = []
    for path in paths:
        relative = path.relative_to(root)
        if (
            relative.suffix.casefold() != ".qc"
            or relative.stem.casefold().endswith("_opt")
        ):
            continue
        raw = _read_regular_no_follow(
            path, cancel_event, contained_root=root, max_bytes=64 * 1024 * 1024,
        )
        model_name = _qc_model_name(raw)
        if model_name is not None and _normalized_model(model_name) == _normalized_model(
            manifest.model_rel
        ):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("authoritative original root QC is missing or ambiguous")
    graph = parse_qc_graph(candidates[0], root)
    if _safe_tree_files(root, cancel_event) != paths:
        raise ValueError("original source tree changed while parsing QC authority")
    return graph


def _preflight_eligible_count(
    snapshot: RecoverySourceSnapshot,
    cancel_event: threading.Event | None,
) -> int:
    """Read only the sealed metrics member; this count can reject, never authorize."""
    matches = tuple(
        item for item in snapshot.source_manifest.files
        if item.relative_path == "candidate_metrics.json"
    )
    if (
        len(matches) != 1
        or matches[0].kind != "auxiliary"
        or matches[0].file_identity != "auxiliary/candidate_metrics.json"
    ):
        raise ValueError("candidate metrics preflight has no sealed canonical member")
    proof = matches[0]
    path = Path(snapshot.source_root) / "candidate_metrics.json"
    raw = _read_regular_no_follow(
        path, cancel_event, contained_root=snapshot.source_root,
        max_bytes=_CANDIDATE_METRICS_BYTE_LIMIT,
    )
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (proof.size, proof.sha256):
        raise ValueError("candidate metrics preflight bytes differ from sealed member")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate metrics preflight document is invalid") from exc
    files = payload.get("files") if type(payload) is dict else None
    if type(files) is not list:
        return 0
    return sum(
        type(item) is dict
        and type(item.get("adaptive_exact_preservation")) is dict
        and item["adaptive_exact_preservation"].get("schema") == 1
        and item["adaptive_exact_preservation"].get("kind") == "eligible-exact-v1"
        and item["adaptive_exact_preservation"].get("preserved_exact") is True
        and item["adaptive_exact_preservation"].get("reason") in {
            "ratio-preserved-exact-v1", "approved-exact-source-fallback-v1",
        }
        for item in files
    )


def _canonical_cdmaterials(graph: QcGraph) -> tuple[str, ...]:
    values: list[str] = []
    folded: set[str] = set()
    for graph_file in graph.files:
        tokens = _lex(graph_file.text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.value.casefold() != "$cdmaterials":
                index += 1
                continue
            line, next_index = _line_values(tokens, index + 1, len(tokens))
            if len(line) != 1:
                raise ValueError("$cdmaterials requires exactly one canonical path")
            raw = line[0].value.replace("\\", "/").strip().strip("/")
            if raw:
                posix = PurePosixPath(raw)
                windows = PureWindowsPath(raw)
                if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
                    raise ValueError("$cdmaterials path is unsafe")
                canonical = posix.as_posix()
                key = canonical.casefold()
                if key in folded:
                    raise ValueError("$cdmaterials normalized path is duplicated")
                folded.add(key)
                values.append(canonical)
            index = max(next_index, index + 1)
    return tuple(values)


def _visual_proofs(snapshot: RecoverySourceSnapshot) -> dict[str, SourceFileProof]:
    result = {
        item.file_identity: item
        for item in snapshot.source_manifest.files
        if item.kind == "visual-source"
    }
    expected = tuple(sorted(result, key=lambda item: (item.casefold(), item)))
    if not result or tuple(result) != expected:
        raise ValueError("original visual source inventory is not canonical")
    return result


def _proof_for_reference(
    snapshot: RecoverySourceSnapshot, source_path: Path, *, kind: str,
) -> SourceFileProof:
    root = _absolute(snapshot.source_root)
    path = _absolute(source_path)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("animation reference escapes sealed source root") from exc
    matches = tuple(
        item for item in snapshot.source_manifest.files
        if item.relative_path == relative and item.kind == kind
    )
    if len(matches) != 1:
        raise ValueError("animation reference has no exact sealed source member")
    return matches[0]


def _read_animation_pair(
    pair: SmdAnimationPairInput, cancel_event: threading.Event | None,
) -> tuple[tuple[object, ...], tuple[int, ...]]:
    before = _read_regular_no_follow(
        pair.original_path, cancel_event, contained_root=pair.original_root,
        max_bytes=64 * 1024 * 1024,
    )
    after = _read_regular_no_follow(
        pair.candidate_path, cancel_event, contained_root=pair.candidate_root,
        max_bytes=64 * 1024 * 1024,
    )
    if (
        (len(before), hashlib.sha256(before).hexdigest())
        != (pair.original_proof.size, pair.original_proof.sha256)
        or (len(after), hashlib.sha256(after).hexdigest())
        != (pair.candidate_proof.size, pair.candidate_proof.sha256)
        or before != after
    ):
        raise ValueError("paired animation current bytes differ")
    before_nodes, before_frames = _parse_nodes_and_frames(before)
    after_nodes, after_frames = _parse_nodes_and_frames(after)
    before_ids = tuple(frame for frame, _transforms in before_frames)
    after_ids = tuple(frame for frame, _transforms in after_frames)
    if before_nodes != after_nodes or before_ids != after_ids:
        raise ValueError("paired animation nodes or frame indices differ")
    return tuple(before_nodes), before_ids


def _animation_occurrence_shapes(graph: QcGraph) -> tuple[tuple[object, ...], ...]:
    shapes: list[tuple[object, ...]] = []
    for graph_file in graph.files:
        tokens = _lex(graph_file.text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.value.casefold() not in {"$sequence", "$animation"}:
                index += 1
                continue
            args, after_args = _line_values(tokens, index + 1, len(tokens))
            block = _block_bounds(tokens, after_args, len(tokens))
            block_tokens = () if block is None else tokens[block[0]:block[1]]
            sources = tuple(_source_tokens(args[1:])) + tuple(
                _source_tokens(list(block_tokens))
            )

            def normalized(values) -> tuple[tuple[str, str], ...]:
                return tuple(
                    (item.kind, "<animation-source>" if _source_tokens([item]) else item.value)
                    for item in values
                )

            for source_ordinal, _source in enumerate(sources):
                shapes.append((
                    token.value.casefold(), token.line, source_ordinal,
                    normalized(args), normalized(block_tokens),
                ))
            index = (
                block[2] if block is not None
                else max(after_args + 1, index + 1)
            )
    return tuple(shapes)


def _build_animation_anchor(
    original_graph: QcGraph, candidate_graph: QcGraph,
    original_snapshot: RecoverySourceSnapshot,
    candidate_snapshot: RecoverySourceSnapshot,
    cancel_event: threading.Event | None,
) -> ProductionAnimationAnchor | None:
    original_refs = tuple(item for item in original_graph.references if item.role == "animation")
    candidate_refs = tuple(item for item in candidate_graph.references if item.role == "animation")
    original_shapes = _animation_occurrence_shapes(original_graph)
    candidate_shapes = _animation_occurrence_shapes(candidate_graph)
    original_files = tuple(
        item for item in original_snapshot.source_manifest.files
        if item.kind == "animation-source"
    )
    candidate_files = tuple(
        item for item in candidate_snapshot.source_manifest.files
        if item.kind == "animation-source"
    )
    if not original_refs and not candidate_refs and not original_files and not candidate_files:
        return None
    if (
        not original_refs or len(original_refs) != len(candidate_refs)
        or len(original_shapes) != len(original_refs)
        or original_shapes != candidate_shapes
        or tuple(item.directive for item in original_refs)
        != tuple(item.directive for item in candidate_refs)
    ):
        raise ValueError("original/candidate animation occurrence inventory differs")

    pairs: list[tuple[int, str, SmdAnimationPairInput, tuple[object, ...], tuple[int, ...]]] = []
    original_seen: set[str] = set()
    candidate_seen: set[str] = set()
    for index, (before_ref, after_ref) in enumerate(zip(original_refs, candidate_refs)):
        before_proof = _proof_for_reference(
            original_snapshot, before_ref.source_path, kind="animation-source",
        )
        after_proof = _proof_for_reference(
            candidate_snapshot, after_ref.source_path, kind="animation-source",
        )
        original_seen.add(before_proof.relative_path)
        candidate_seen.add(after_proof.relative_path)
        pair = SmdAnimationPairInput(
            _absolute(before_ref.source_path), _absolute(original_snapshot.source_root),
            before_proof, _absolute(after_ref.source_path),
            _absolute(candidate_snapshot.source_root), after_proof,
        )
        nodes, frames = _read_animation_pair(pair, cancel_event)
        pairs.append((index, before_ref.directive, pair, nodes, frames))
    if (
        original_seen != {item.relative_path for item in original_files}
        or candidate_seen != {item.relative_path for item in candidate_files}
    ):
        raise ValueError("animation source union differs from exact QC occurrences")
    anchors = tuple(item for item in pairs if any(frame > 0 for frame in item[4]))
    if not anchors:
        raise ValueError("animation QC has no positive representative frame")
    index, directive, pair, _nodes, frames = anchors[0]
    return ProductionAnimationAnchor(index, directive, pair, max(frame for frame in frames if frame > 0))


def _current_source_bytes(
    snapshot: RecoverySourceSnapshot,
    proof: SourceFileProof,
    cancel_event: threading.Event | None,
) -> bytes:
    path = Path(snapshot.source_root).joinpath(*PurePosixPath(proof.relative_path).parts)
    raw = _read_regular_no_follow(
        path, cancel_event, contained_root=snapshot.source_root,
        max_bytes=_SOURCE_BYTE_LIMIT,
    )
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (proof.size, proof.sha256):
        raise ValueError("current visual source bytes differ from SourceFileProof")
    return raw


def _build_dependencies(
    snapshot: RecoverySourceSnapshot,
    graph: QcGraph,
    material_roots: tuple[Path, ...],
    cancel_event: threading.Event | None,
) -> dict[str, AdaptiveStateSourceDependencies]:
    searches = _canonical_cdmaterials(graph)
    dependencies: dict[str, AdaptiveStateSourceDependencies] = {}
    for identity, proof in _visual_proofs(snapshot).items():
        source_bytes = _current_source_bytes(snapshot, proof, cancel_event)
        component_manifest = build_source_component_manifest(source_bytes)
        filtered = prefilter_direct_degenerate_smd(
            source_bytes.decode("utf-8", errors="strict")
        ).filtered_text.encode("utf-8")
        materials = direct_smd_material_counts(filtered.decode("utf-8"))
        requests = tuple({
            "material_region_key": f"material-{index:03d}",
            "smd_material": material,
            "search_paths": searches,
        } for index, (material, _count) in enumerate(materials))
        material_contract = build_source_union_material_contract(
            source_identity=identity, filtered_source_bytes=filtered,
            requests=requests, roots=material_roots, cancel_event=cancel_event,
        )
        dependencies[identity] = AdaptiveStateSourceDependencies.create(
            source_identity=identity, source_size=proof.size,
            source_sha256=proof.sha256,
            component_manifest=component_manifest,
            material_contract=material_contract,
        )
    return dependencies


def _revalidate_dependencies(
    snapshot: RecoverySourceSnapshot,
    dependencies: Mapping[str, AdaptiveStateSourceDependencies],
    material_roots: tuple[Path, ...],
    cancel_event: threading.Event | None,
) -> None:
    proofs = _visual_proofs(snapshot)
    if set(dependencies) != set(proofs):
        raise ValueError("current dependency/source union differs")
    for identity, proof in proofs.items():
        source_bytes = _current_source_bytes(snapshot, proof, cancel_event)
        dependency = dependencies[identity]
        if build_source_component_manifest(source_bytes) != dependency.component_manifest:
            raise ValueError("current source component manifest differs")
        filtered = prefilter_direct_degenerate_smd(
            source_bytes.decode("utf-8", errors="strict")
        ).filtered_text.encode("utf-8")
        require_current_source_union_material_contract(
            dependency.material_contract, filtered_source_bytes=filtered,
            roots=material_roots, cancel_event=cancel_event,
        )


@dataclass(frozen=True)
class ProductionAdaptiveAuthorityBundle:
    original_snapshot: RecoverySourceSnapshot
    metrics: AdaptiveCandidateMetricsProof
    state_inventory: AdaptiveDirectStateInventory
    retained_base: RetainedMonacoBaseProof
    selection: ExactFallbackSelection | None
    material_roots: tuple[Path, ...]
    dependencies: Mapping[str, AdaptiveStateSourceDependencies]
    component_manifests: Mapping[str, SourceComponentManifest]
    material_contracts: Mapping[str, SourceUnionMaterialContract]
    animation_anchor: ProductionAnimationAnchor | None
    animation_pairs: Mapping[str, SmdAnimationPairInput]
    pose_bindings: Mapping[str, tuple[SourceUnionPoseBinding, ...]]

    def __post_init__(self) -> None:
        if not all((
            isinstance(self.original_snapshot, RecoverySourceSnapshot),
            isinstance(self.metrics, AdaptiveCandidateMetricsProof),
            isinstance(self.state_inventory, AdaptiveDirectStateInventory),
            isinstance(self.retained_base, RetainedMonacoBaseProof),
            self.selection is None or isinstance(self.selection, ExactFallbackSelection),
        )):
            raise TypeError("production adaptive authority bundle is invalid")
        roots = tuple(Path(item) for item in self.material_roots)
        dependencies = dict(self.dependencies)
        components = dict(self.component_manifests)
        materials = dict(self.material_contracts)
        animation_pairs = dict(self.animation_pairs)
        pose_bindings = dict(self.pose_bindings)
        identities = tuple(item.source_identity for item in self.metrics.sources)
        anchor_valid = self.animation_anchor is None or type(
            self.animation_anchor
        ) is ProductionAnimationAnchor
        if (
            tuple(dependencies) != identities
            or tuple(components) != identities
            or tuple(materials) != identities
            or any(components[key] != dependencies[key].component_manifest for key in identities)
            or any(materials[key] != dependencies[key].material_contract for key in identities)
            or tuple(animation_pairs) not in {(), identities}
            or tuple(pose_bindings) not in {(), identities}
            or bool(self.animation_anchor) != bool(animation_pairs)
            or bool(animation_pairs) != bool(pose_bindings)
            or not anchor_valid
        ):
            raise ValueError("production adaptive source-union mappings differ")
        if self.animation_anchor is None:
            if any(row.pose_keys != ("bind",) for row in self.state_inventory.rows):
                raise ValueError("bind-only authority differs from QC animation inventory")
        else:
            anchor = self.animation_anchor
            assert isinstance(anchor, ProductionAnimationAnchor)
            for identity in identities:
                pair = animation_pairs.get(identity)
                bindings = pose_bindings.get(identity)
                rows = tuple(
                    row for row in self.state_inventory.rows
                    if row.source_identity == identity
                )
                contracts = {row.pose_contract_sha256 for row in rows}
                if (
                    not isinstance(pair, SmdAnimationPairInput)
                    or pair != anchor.pair
                    or type(bindings) is not tuple or len(bindings) != 2
                    or any(not isinstance(item, SourceUnionPoseBinding) for item in bindings)
                    or tuple(item.pose_key for item in bindings) != ("bind", "animation")
                    or not rows or len(contracts) != 1
                    or any(row.pose_keys != ("bind", "animation") for row in rows)
                    or any(item.pose_contract_sha256 not in contracts for item in bindings)
                    or bindings[1].animation_pair != pair
                    or bindings[1].frame != anchor.representative_frame
                ):
                    raise ValueError("production paired animation mappings differ")
        object.__setattr__(self, "material_roots", roots)
        object.__setattr__(self, "dependencies", MappingProxyType(dependencies))
        object.__setattr__(self, "component_manifests", MappingProxyType(components))
        object.__setattr__(self, "material_contracts", MappingProxyType(materials))
        object.__setattr__(self, "animation_pairs", MappingProxyType(animation_pairs))
        object.__setattr__(self, "pose_bindings", MappingProxyType(pose_bindings))

    @property
    def coverage(self) -> AdaptiveDirectCoverageManifest | None:
        return None if self.selection is None else self.selection.coverage


def build_production_adaptive_authority_bundle(
    *,
    manifest: FamilyManifest,
    evaluation: CandidateEvaluation,
    build: CandidateBuild,
    candidate_cache_digest: str,
    material_roots: Sequence[Path],
    cancel_event: threading.Event | None = None,
) -> ProductionAdaptiveAuthorityBundle:
    """Construct all direct-fallback authority without performing direct I/O."""
    if not isinstance(manifest, FamilyManifest):
        raise TypeError("production adaptive family manifest is invalid")
    if not isinstance(evaluation, CandidateEvaluation) or not isinstance(build, CandidateBuild):
        raise TypeError("production adaptive candidate authority is invalid")
    spec = build.spec
    snapshot = build.source_snapshot
    if (
        evaluation.spec != spec
        or spec.engine != "blender"
        or spec.strategy != "blender-adaptive-v1"
        or spec.composite_recipe is not None
        or not isinstance(snapshot, RecoverySourceSnapshot)
        or snapshot.kind != "candidate"
        or not evaluation.structural.passed
        or not evaluation.whole_visual.passed
        or not evaluation.visual.passed
        or not evaluation.focused_by_region
        or not all(item.validation.passed for item in evaluation.focused_by_region.values())
    ):
        raise ValueError("production adaptive base is not an approved ordinary candidate")
    cache_digest = snapshot.candidate_cache_digest
    if cache_digest is None or candidate_cache_digest != cache_digest:
        raise ValueError("production adaptive candidate cache authority differs")
    for result in (
        evaluation.structural, evaluation.visual, evaluation.whole_visual,
        *(item.validation for item in evaluation.focused_by_region.values()),
    ):
        validation_result_payload(result)

    if _preflight_eligible_count(snapshot, cancel_event) > 8:
        raise ValueError("production adaptive exact fallback exceeds eight eligible sources")

    if type(material_roots) not in (tuple, list) or not material_roots:
        raise ValueError("production adaptive material roots are invalid")
    roots = tuple(_absolute(Path(item)).resolve(strict=True) for item in material_roots)
    root_keys = tuple(os.path.normcase(str(item)) for item in roots)
    if len(set(root_keys)) != len(root_keys):
        raise ValueError("production adaptive material roots are not canonical unique; resolved roots are duplicated")

    revalidate_recovery_snapshot(snapshot, cancel_event)
    candidate_graph = _root_graph(
        Path(snapshot.source_root), manifest.model_rel, optimized=True,
        source_manifest=snapshot.source_manifest, cancel_event=cancel_event,
    )
    if _absolute(candidate_graph.root) != _absolute(build.optimized_qc):
        raise ValueError("candidate build optimized QC differs from sealed root QC")

    original_root = _absolute(manifest.source_dir)
    original_graph = _original_root_graph(manifest, cancel_event)
    original_manifest = build_source_tree_manifest(
        original_root, original_graph, "original-source-v1", cancel_event,
    )
    try:
        parsed_graph_digest = _bound_graph_digest(
            original_graph, original_manifest, cancel_event,
        )
        sealed_original_graph = _root_graph(
            original_root, manifest.model_rel, optimized=False,
            source_manifest=original_manifest, cancel_event=cancel_event,
        )
        if _bound_graph_digest(
            sealed_original_graph, original_manifest, cancel_event,
        ) != parsed_graph_digest:
            raise ValueError("original QC graph digest differs")
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ValueError(
            "original QC graph changed outside the source-manifest same window"
        ) from exc
    original_graph = sealed_original_graph
    original_snapshot = build_recovery_source_snapshot(
        kind="original", family_id=manifest.family_id,
        family_input_sha256=manifest.input_hash,
        optimizer_contract_sha256=optimizer_contract_sha256(spec),
        whole_profile_sha256=snapshot.whole_profile_sha256,
        focused_profile_sha256=snapshot.focused_profile_sha256,
        dependency_proof_sha256=snapshot.dependency_proof_sha256,
        candidate_id=None, candidate_cache_digest=None,
        source_root=original_root, source_manifest=original_manifest,
        focused_evidence=(),
    )
    revalidate_recovery_snapshot(original_snapshot, cancel_event)

    animation_anchor = _build_animation_anchor(
        original_graph, candidate_graph, original_snapshot, snapshot, cancel_event,
    )

    metrics = build_production_adaptive_candidate_metrics_proof(
        manifest=manifest, spec=spec, candidate_cache_digest=cache_digest,
        original_snapshot=original_snapshot, candidate_snapshot=snapshot,
        candidate_metrics_path=Path(snapshot.source_root) / "candidate_metrics.json",
        cancel_event=cancel_event,
    )
    eligible_count = sum(item.kind == "eligible-exact-v1" for item in metrics.sources)
    if eligible_count > 8:
        raise ValueError("production adaptive exact fallback exceeds eight eligible sources")

    dependencies = _build_dependencies(
        original_snapshot, original_graph, roots, cancel_event,
    )
    animation_pairs: dict[str, SmdAnimationPairInput] = {}
    animation_pose_contracts = {}
    if animation_anchor is not None:
        anchor_nodes, _frames = _read_animation_pair(animation_anchor.pair, cancel_event)
        for identity, proof in _visual_proofs(original_snapshot).items():
            source_path = Path(original_snapshot.source_root).joinpath(
                *PurePosixPath(proof.relative_path).parts
            )
            skeleton = build_smd_skeleton_contract(
                source_path, Path(original_snapshot.source_root), proof, cancel_event,
            )
            if tuple(skeleton.nodes) == anchor_nodes:
                animation_pairs[identity] = animation_anchor.pair
                animation_pose_contracts[identity] = build_smd_pose_contract(
                    skeleton, animation_pair=animation_anchor.pair,
                    cancel_event=cancel_event,
                )
        if set(animation_pairs) != set(_visual_proofs(original_snapshot)):
            raise ValueError("animation anchor is incompatible with a visual source skeleton")
    state_inventory = build_production_adaptive_direct_state_inventory(
        manifest=manifest, spec=spec, candidate_cache_digest=cache_digest,
        original_snapshot=original_snapshot, candidate_snapshot=snapshot,
        metrics_proof=metrics, dependencies=dependencies,
        animation_pairs=animation_pairs, cancel_event=cancel_event,
    )
    pose_bindings: dict[str, tuple[SourceUnionPoseBinding, ...]] = {}
    if animation_anchor is not None:
        rows_by_identity = {
            identity: tuple(row for row in state_inventory.rows if row.source_identity == identity)
            for identity in animation_pairs
        }
        for identity, rows in rows_by_identity.items():
            contracts = {row.pose_contract_sha256 for row in rows}
            if not rows or len(contracts) != 1 or any(
                row.pose_keys != ("bind", "animation") for row in rows
            ):
                raise ValueError("animation pose contract differs across visual occurrences")
            contract = next(iter(contracts))
            pose_bindings[identity] = (
                SourceUnionPoseBinding.bind(contract),
                SourceUnionPoseBinding.paired_anchor(
                    animation_anchor.pair, animation_pose_contracts[identity],
                    cancel_event,
                ),
            )
    retained = build_retained_monaco_base_proof(
        evaluation, build, metrics, state_inventory, snapshot.focused_evidence,
        cancel_event=cancel_event,
    )
    if select_monaco_base(
        (evaluation,), {spec.candidate_id: retained}, cancel_event=cancel_event,
    ) != evaluation:
        raise ValueError("production adaptive base did not retain approval authority")
    selection = select_exact_fallback_sources(
        retained, metrics, state_inventory, original_snapshot,
        cancel_event=cancel_event,
    )

    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(snapshot, cancel_event)
    _revalidate_dependencies(original_snapshot, dependencies, roots, cancel_event)
    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(snapshot, cancel_event)
    ordered_dependencies = {
        identity: dependencies[identity]
        for identity in (item.source_identity for item in metrics.sources)
    }
    return ProductionAdaptiveAuthorityBundle(
        original_snapshot=original_snapshot, metrics=metrics,
        state_inventory=state_inventory, retained_base=retained,
        selection=selection, material_roots=roots,
        dependencies=ordered_dependencies,
        component_manifests={
            identity: item.component_manifest
            for identity, item in ordered_dependencies.items()
        },
        material_contracts={
            identity: item.material_contract
            for identity, item in ordered_dependencies.items()
        },
        animation_anchor=animation_anchor,
        animation_pairs={identity: animation_pairs[identity] for identity in ordered_dependencies if identity in animation_pairs},
        pose_bindings={identity: pose_bindings[identity] for identity in ordered_dependencies if identity in pose_bindings},
    )
