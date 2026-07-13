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
from .qc_graph import QcGraph, _lex, _line_values, parse_qc_graph
from .smd_contract import direct_smd_material_counts, prefilter_direct_degenerate_smd
from .source_components import SourceComponentManifest, build_source_component_manifest
from .source_materials import (
    SourceUnionMaterialContract,
    build_source_union_material_contract,
    require_current_source_union_material_contract,
)


_SOURCE_BYTE_LIMIT = 2 * 1024 ** 3
_CANDIDATE_METRICS_BYTE_LIMIT = 64 * 1024 * 1024


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
        identities = tuple(item.source_identity for item in self.metrics.sources)
        if (
            tuple(dependencies) != identities
            or tuple(components) != identities
            or tuple(materials) != identities
            or any(components[key] != dependencies[key].component_manifest for key in identities)
            or any(materials[key] != dependencies[key].material_contract for key in identities)
        ):
            raise ValueError("production adaptive source-union mappings differ")
        object.__setattr__(self, "material_roots", roots)
        object.__setattr__(self, "dependencies", MappingProxyType(dependencies))
        object.__setattr__(self, "component_manifests", MappingProxyType(components))
        object.__setattr__(self, "material_contracts", MappingProxyType(materials))

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

    if (
        any(item.kind == "animation-source" for item in snapshot.source_manifest.files)
        or any(item.kind == "animation-source" for item in original_manifest.files)
        or any(item.role == "animation" for item in candidate_graph.references)
        or any(item.role == "animation" for item in original_graph.references)
    ):
        raise ValueError("unsupported-until-paired-animation-authority")

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
    state_inventory = build_production_adaptive_direct_state_inventory(
        manifest=manifest, spec=spec, candidate_cache_digest=cache_digest,
        original_snapshot=original_snapshot, candidate_snapshot=snapshot,
        metrics_proof=metrics, dependencies=dependencies,
        animation_pairs={}, cancel_event=cancel_event,
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
    )
