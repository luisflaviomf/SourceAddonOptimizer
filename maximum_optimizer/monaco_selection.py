from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import threading
from typing import Callable, Mapping, Sequence

from .candidates import CandidateBuild
from .compiled_size import _kind as compiled_artifact_kind
from .composite import (
    build_adaptive_direct_coverage_manifest,
    candidate_spec_sha256,
    _has_reparse_ancestor,
    _hash_current_file,
    _safe_tree_files,
    revalidate_recovery_snapshot,
)
from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveDirectCoverageManifest,
    AdaptiveDirectStateInventory,
    CandidateEvaluation,
    FocusedEvidenceRef,
    RecoverySourceSnapshot,
    require_canonical_relative,
)
from .reporting import canonical_json


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMPILED_FILE_LIMIT = 64
_COMPILED_BYTE_LIMIT = 2 * 1024 ** 3


def _seal(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompiledArtifactProof:
    relative_path: str
    kind: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        require_canonical_relative(self.relative_path, "Monaco compiled artifact path")
        if type(self.kind) is not str or not self.kind or self.kind != self.kind.casefold():
            raise ValueError("Monaco compiled artifact kind is invalid")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("Monaco compiled artifact size is invalid")
        if type(self.sha256) is not str or _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("Monaco compiled artifact hash is invalid")


def _compiled_files(
    root: Path, cancel_event: threading.Event | None = None,
) -> tuple[CompiledArtifactProof, ...]:
    root = Path(root)
    if not root.is_absolute() or _has_reparse_ancestor(root):
        raise ValueError("Monaco compiled models root is unavailable or unsafe")
    try:
        paths = _safe_tree_files(root, cancel_event)
    except OSError as exc:
        raise ValueError("Monaco compiled models root is unavailable or unsafe") from exc
    if not paths or len(paths) > _COMPILED_FILE_LIMIT:
        raise ValueError("Monaco compiled artifact inventory is empty or exceeds bound")
    found: list[CompiledArtifactProof] = []
    total = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        size, digest = _hash_current_file(
            path, root, cancel_event, max_bytes=_COMPILED_BYTE_LIMIT - total,
        )
        total += size
        found.append(CompiledArtifactProof(relative, compiled_artifact_kind(path), size, digest))
    if _safe_tree_files(root, cancel_event) != paths:
        raise ValueError("Monaco compiled artifact inventory changed during hashing")
    result = tuple(sorted(found, key=lambda item: (item.relative_path.casefold(), item.relative_path)))
    if len({item.relative_path.casefold() for item in result}) != len(result):
        raise ValueError("Monaco compiled artifact inventory is ambiguous")
    return result


def _compiled_digest(files: tuple[CompiledArtifactProof, ...]) -> str:
    return _seal([{
        "relative_path": item.relative_path, "kind": item.kind,
        "size": item.size, "sha256": item.sha256,
    } for item in files])


def _activation_payload(proof: "RetainedMonacoBaseProof") -> dict[str, object]:
    return {
        "schema": proof.schema,
        "candidate_id": proof.candidate_id,
        "candidate_cache_digest": proof.candidate_cache_digest,
        "base_spec_sha256": proof.base_spec_sha256,
        "source_manifest_sha256": proof.source_manifest_sha256,
        "source_snapshot_sha256": proof.source_snapshot_sha256,
        "metrics_evidence_sha256": proof.metrics.evidence_sha256,
        "state_inventory_sha256": proof.state_inventory.state_inventory_sha256,
        "compiled_manifest_sha256": proof.compiled_manifest_sha256,
        "compiled_total_bytes": proof.compiled_total_bytes,
        "focused_evidence": [
            {"region_key": item.region_key, "evidence_sha256": item.evidence_sha256}
            for item in proof.focused_evidence
        ],
    }


@dataclass(frozen=True)
class RetainedMonacoBaseProof:
    schema: int
    candidate_id: str
    candidate_cache_digest: str
    base_spec_sha256: str
    source_manifest_sha256: str
    source_snapshot_sha256: str
    evaluation: CandidateEvaluation
    build: CandidateBuild
    metrics: AdaptiveCandidateMetricsProof
    state_inventory: AdaptiveDirectStateInventory
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    compiled_artifacts: tuple[CompiledArtifactProof, ...]
    compiled_total_bytes: int
    compiled_manifest_sha256: str
    activation_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 3:
            raise ValueError("Monaco retained proof requires schema 3")
        if not isinstance(self.evaluation, CandidateEvaluation) or not isinstance(self.build, CandidateBuild):
            raise TypeError("Monaco retained evaluation or build is invalid")
        if not isinstance(self.metrics, AdaptiveCandidateMetricsProof) or not isinstance(self.state_inventory, AdaptiveDirectStateInventory):
            raise TypeError("Monaco retained typed evidence is invalid")
        focused = tuple(self.focused_evidence)
        keys = [(item.region_key.casefold(), item.region_key) for item in focused if isinstance(item, FocusedEvidenceRef)]
        if not focused or len(keys) != len(focused) or keys != sorted(keys) or len({key[0] for key in keys}) != len(keys):
            raise ValueError("Monaco retained focused evidence is not complete canonical")
        artifacts = tuple(self.compiled_artifacts)
        if any(not isinstance(item, CompiledArtifactProof) for item in artifacts):
            raise TypeError("Monaco retained compiled manifest is invalid")
        if type(self.compiled_total_bytes) is not int or self.compiled_total_bytes < 0 or self.compiled_total_bytes != sum(item.size for item in artifacts) or self.compiled_manifest_sha256 != _compiled_digest(artifacts):
            raise ValueError("Monaco retained compiled manifest seal mismatch")
        for value in (
            self.candidate_cache_digest, self.base_spec_sha256,
            self.source_manifest_sha256, self.source_snapshot_sha256,
            self.compiled_manifest_sha256, self.activation_sha256,
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError("Monaco retained proof hash is invalid")
        if self.activation_sha256 != _seal(_activation_payload(self)):
            raise ValueError("Monaco retained proof seal mismatch")
        object.__setattr__(self, "focused_evidence", focused)
        object.__setattr__(self, "compiled_artifacts", artifacts)


def _validate_retained(
    proof: RetainedMonacoBaseProof, evaluation: CandidateEvaluation,
    cancel_event: threading.Event | None = None,
) -> tuple[CompiledArtifactProof, ...]:
    spec = evaluation.spec
    snapshot = proof.build.source_snapshot
    if (
        proof.evaluation != evaluation
        or proof.build.spec != spec
        or not isinstance(snapshot, RecoverySourceSnapshot)
        or snapshot.kind != "candidate"
        or snapshot.candidate_id != spec.candidate_id
        or snapshot.candidate_cache_digest != proof.candidate_cache_digest
        or proof.candidate_id != spec.candidate_id
        or proof.base_spec_sha256 != candidate_spec_sha256(spec)
        or proof.metrics.candidate_id != spec.candidate_id
        or proof.metrics.candidate_cache_digest != proof.candidate_cache_digest
        or proof.metrics.base_spec_sha256 != proof.base_spec_sha256
        or proof.metrics.source_manifest_sha256 != snapshot.source_manifest.digest
        or proof.metrics.source_snapshot_sha256 != snapshot.snapshot_sha256
        or proof.source_manifest_sha256 != snapshot.source_manifest.digest
        or proof.source_snapshot_sha256 != snapshot.snapshot_sha256
        or proof.state_inventory.base_candidate_id != spec.candidate_id
        or proof.state_inventory.base_cache_digest != proof.candidate_cache_digest
        or proof.state_inventory.base_spec_sha256 != proof.base_spec_sha256
        or proof.state_inventory.base_source_manifest_sha256 != proof.source_manifest_sha256
        or proof.state_inventory.base_source_snapshot_sha256 != proof.source_snapshot_sha256
        or proof.metrics.family_id != snapshot.family_id
        or proof.metrics.family_input_sha256 != snapshot.family_input_sha256
        or proof.metrics.family_id != proof.state_inventory.family_id
        or proof.metrics.family_input_sha256 != proof.state_inventory.family_input_sha256
        or tuple(item.source_identity for item in proof.metrics.sources)
        != proof.state_inventory.complete_source_identities
        or snapshot.focused_evidence != proof.focused_evidence
    ):
        raise ValueError("Monaco retained build bindings are stale")
    visual_identities = tuple(
        item.file_identity for item in snapshot.source_manifest.files
        if item.kind == "visual-source"
    )
    if visual_identities != tuple(item.source_identity for item in proof.metrics.sources):
        raise ValueError("Monaco candidate visual source inventory is incomplete")
    candidate_files = {
        item.file_identity: item for item in snapshot.source_manifest.files
        if item.kind == "visual-source"
    }
    for metric in proof.metrics.sources:
        output = candidate_files[metric.source_identity]
        if (
            output.relative_path != metric.output_relative_path
            or (output.size, output.sha256) != (metric.output_size, metric.output_sha256)
        ):
            raise ValueError("Monaco candidate metrics differ from current retained output")
    build_root = Path(proof.build.compiled_models_dir).resolve()
    evaluation_root = Path(evaluation.compiled_models_dir).resolve()
    size_root = Path(evaluation.size.root).resolve()
    if build_root != evaluation_root or evaluation_root != size_root:
        raise ValueError("Monaco compiled roots differ")
    current = _compiled_files(Path(proof.build.compiled_models_dir), cancel_event)
    expected_sizes = tuple(sorted(
        ((item.relative_path, "." + item.kind.casefold().lstrip("."), item.size_bytes) for item in evaluation.size.artifacts),
        key=lambda item: (item[0].casefold(), item[0]),
    ))
    if (
        current != proof.compiled_artifacts
        or tuple((item.relative_path, item.kind, item.size) for item in current) != expected_sizes
        or sum(item.size for item in current) != evaluation.size.total_bytes
        or sum(item.size for item in current) != proof.compiled_total_bytes
    ):
        raise ValueError("Monaco current compiled bytes differ from retained evaluation")
    revalidate_recovery_snapshot(snapshot, cancel_event)
    return current


def build_retained_monaco_base_proof(
    evaluation: CandidateEvaluation,
    build: CandidateBuild,
    metrics: AdaptiveCandidateMetricsProof,
    state_inventory: AdaptiveDirectStateInventory,
    focused_evidence: Sequence[FocusedEvidenceRef],
    *, cancel_event: threading.Event | None = None,
) -> RetainedMonacoBaseProof:
    if not isinstance(evaluation, CandidateEvaluation) or not isinstance(build, CandidateBuild):
        raise TypeError("Monaco evaluation or build is invalid")
    snapshot = build.source_snapshot
    if not isinstance(snapshot, RecoverySourceSnapshot):
        raise ValueError("Monaco retained build has no recovery source snapshot")
    artifacts = _compiled_files(Path(build.compiled_models_dir), cancel_event)
    values = dict(
        schema=3, candidate_id=evaluation.spec.candidate_id,
        candidate_cache_digest=metrics.candidate_cache_digest,
        base_spec_sha256=candidate_spec_sha256(evaluation.spec),
        source_manifest_sha256=snapshot.source_manifest.digest,
        source_snapshot_sha256=snapshot.snapshot_sha256,
        evaluation=evaluation, build=build, metrics=metrics,
        state_inventory=state_inventory, focused_evidence=tuple(focused_evidence),
        compiled_artifacts=artifacts,
        compiled_total_bytes=sum(item.size for item in artifacts),
        compiled_manifest_sha256=_compiled_digest(artifacts),
        activation_sha256="0" * 64,
    )
    provisional = object.__new__(RetainedMonacoBaseProof)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    values["activation_sha256"] = _seal(_activation_payload(provisional))
    proof = RetainedMonacoBaseProof(**values)
    _validate_retained(proof, evaluation, cancel_event)
    return proof


def select_monaco_base(
    evaluations: Sequence[CandidateEvaluation],
    retained_proofs: Mapping[str, RetainedMonacoBaseProof],
    *, cancel_event: threading.Event | None = None,
) -> CandidateEvaluation | None:
    eligible: list[tuple[CandidateEvaluation, RetainedMonacoBaseProof]] = []
    for evaluation in evaluations:
        if not isinstance(evaluation, CandidateEvaluation):
            continue
        proof = retained_proofs.get(evaluation.spec.candidate_id)
        if not isinstance(proof, RetainedMonacoBaseProof):
            continue
        try:
            _validate_retained(proof, evaluation, cancel_event)
        except (OSError, ValueError):
            continue
        spec = evaluation.spec
        if (
            spec.engine != "blender" or spec.strategy != "blender-adaptive-v1"
            or spec.composite_recipe is not None
            or not evaluation.structural.passed or not evaluation.whole_visual.passed
            or not evaluation.visual.passed or not evaluation.focused_by_region
            or tuple(sorted(evaluation.focused_by_region)) != tuple(item.region_key for item in proof.focused_evidence)
            or any(evaluation.focused_by_region[item.region_key].evidence_sha256 != item.evidence_sha256 for item in proof.focused_evidence)
            or not all(item.validation.passed for item in evaluation.focused_by_region.values())
        ):
            continue
        eligible.append((evaluation, proof))
    if not eligible:
        return None
    return min(eligible, key=lambda item: (item[1].compiled_total_bytes, item[0].spec.candidate_id))[0]


@dataclass(frozen=True)
class ExactFallbackSelection:
    source_identities: tuple[str, ...]
    coverage: AdaptiveDirectCoverageManifest

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, AdaptiveDirectCoverageManifest):
            raise TypeError("Monaco coverage preflight is invalid")
        if self.source_identities != tuple(
            item.source_identity for item in self.coverage.sources
            if item.eligibility_kind == "eligible-exact-v1"
        ):
            raise ValueError("Monaco fallback selection differs from coverage")


def _validate_fallback_authority_pure(
    base_proof: RetainedMonacoBaseProof,
    metrics: AdaptiveCandidateMetricsProof,
    state_inventory: AdaptiveDirectStateInventory,
    original_snapshot: RecoverySourceSnapshot,
) -> None:
    candidate_snapshot = base_proof.build.source_snapshot
    if not isinstance(candidate_snapshot, RecoverySourceSnapshot) or candidate_snapshot.kind != "candidate":
        raise ValueError("Monaco retained candidate snapshot is invalid")
    if (
        metrics != base_proof.metrics
        or state_inventory != base_proof.state_inventory
        or metrics.source_manifest_sha256 != candidate_snapshot.source_manifest.digest
        or metrics.source_snapshot_sha256 != candidate_snapshot.snapshot_sha256
        or original_snapshot.family_id != candidate_snapshot.family_id
        or original_snapshot.family_input_sha256 != candidate_snapshot.family_input_sha256
        or original_snapshot.optimizer_contract_sha256 != candidate_snapshot.optimizer_contract_sha256
        or original_snapshot.whole_profile_sha256 != candidate_snapshot.whole_profile_sha256
        or original_snapshot.focused_profile_sha256 != candidate_snapshot.focused_profile_sha256
        or original_snapshot.dependency_proof_sha256 != candidate_snapshot.dependency_proof_sha256
    ):
        raise ValueError("Monaco fallback sealed authority bindings differ")
    original = {item.file_identity: item for item in original_snapshot.source_manifest.files}
    current = {item.file_identity: item for item in candidate_snapshot.source_manifest.files}
    metric_identities = tuple(item.source_identity for item in metrics.sources)
    original_visual = tuple(item.file_identity for item in original_snapshot.source_manifest.files if item.kind == "visual-source")
    current_visual = tuple(item.file_identity for item in candidate_snapshot.source_manifest.files if item.kind == "visual-source")
    if original_visual != metric_identities or current_visual != metric_identities:
        raise ValueError("Monaco original/candidate visual inventory differs from metrics")
    for item in metrics.sources:
        source = original.get(item.source_identity)
        output = current.get(item.source_identity)
        if (
            source is None or output is None
            or source.kind != "visual-source" or output.kind != "visual-source"
            or source.relative_path != item.source_relative_path
            or output.relative_path != item.output_relative_path
            or (source.size, source.sha256) != (item.source_size, item.source_sha256)
            or (output.size, output.sha256) != (item.output_size, item.output_sha256)
        ):
            raise ValueError("Monaco exact fallback source path or bytes are stale")


def select_exact_fallback_sources(
    base_proof: RetainedMonacoBaseProof,
    metrics: AdaptiveCandidateMetricsProof,
    state_inventory: AdaptiveDirectStateInventory,
    original_snapshot: RecoverySourceSnapshot,
    *,
    coverage_factory: Callable[..., AdaptiveDirectCoverageManifest] = build_adaptive_direct_coverage_manifest,
    reservation_callback: Callable[..., object] | None = None,
    direct_io_callback: Callable[..., object] | None = None,
    cancel_event: threading.Event | None = None,
) -> ExactFallbackSelection | None:
    _ = reservation_callback, direct_io_callback
    eligible = tuple(item for item in metrics.sources if item.kind == "eligible-exact-v1")
    if len(eligible) > 8:
        raise ValueError("Monaco exact fallback exceeds eight eligible sources")
    if not isinstance(base_proof, RetainedMonacoBaseProof):
        raise TypeError("Monaco retained base proof is invalid")
    if not isinstance(original_snapshot, RecoverySourceSnapshot) or original_snapshot.kind != "original":
        raise TypeError("Monaco original recovery snapshot is invalid")
    _validate_fallback_authority_pure(
        base_proof, metrics, state_inventory, original_snapshot,
    )
    if not eligible:
        return None
    _validate_retained(base_proof, base_proof.evaluation, cancel_event)
    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    candidate_snapshot = base_proof.build.source_snapshot
    metric_identities = tuple(item.source_identity for item in metrics.sources)
    coverage = coverage_factory(
        metrics_proof=metrics, state_inventory=state_inventory,
        family_id=state_inventory.family_id,
        family_input_sha256=state_inventory.family_input_sha256,
        base_candidate_id=state_inventory.base_candidate_id,
        base_spec_sha256=state_inventory.base_spec_sha256,
        base_cache_digest=state_inventory.base_cache_digest,
        base_source_manifest_sha256=state_inventory.base_source_manifest_sha256,
        base_source_snapshot_sha256=state_inventory.base_source_snapshot_sha256,
    )
    if not isinstance(coverage, AdaptiveDirectCoverageManifest):
        raise TypeError("Monaco coverage factory returned invalid proof")
    if coverage.complete_source_identities != metric_identities:
        raise ValueError("Monaco coverage inventory differs from complete metrics")
    return ExactFallbackSelection(tuple(item.source_identity for item in eligible), coverage)
