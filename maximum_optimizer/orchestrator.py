from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import threading
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from .cache import AtomicReplaceError, CacheKey, CandidateCache
from .calibration_evidence import TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256
from .candidates import (
    BlenderAdapter,
    CandidateBuild,
    CandidateBuildError,
    CandidateTools,
    FidelityAdapter,
    MeshoptimizerAdapter,
    _matching_qcs,
)
from .compiled_size import compare_snapshots, scan_compiled_models
from .domain import (
    ArtifactStat,
    CompileFileProof,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    FamilyManifest,
    FocusedRegionPolicy,
    FocusedEvidenceRef,
    RecoverySourceSnapshot,
    StructuralAuthorizationEvidence,
    FocusedGateResult,
    FocusRegionResult,
    GateFailure,
    SearchBudget,
    ValidationResult,
    WholeStateEvidence,
    recovery_source_snapshot_payload,
    recovery_source_snapshot_from_payload,
    source_overlay_payload,
    composite_recipe_from_payload,
    structural_authorization_evidence_payload,
    validation_result_payload,
)
from .composite import (
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    optimizer_contract_sha256,
    revalidate_recovery_snapshot,
    focused_recovery_recipe,
    recovery_candidate_spec,
    select_recovery_overlays,
    compose_candidate_sources,
)
from .fidelity_selection import (
    GENERAL_BODY_DETAIL,
    LEGACY_GLOBAL,
    FamilyFidelitySelection,
    FidelityProfileSet,
    classify_original_family,
    load_fidelity_profile_set,
)
from .focused_cache import (
    FocusCacheContext,
    FocusCacheKey,
    FocusCacheMetadata,
    FocusExpectedMatrix,
    FocusProfileProof,
    FocusRenderDirectories,
    FocusStateProof,
    FocusedEvidenceContext,
    FocusedRecoveryContext,
    FocusedRecoveryEvidence,
    FocusedRenderEvidence,
    FocusedRenderCache,
    UncachedFocusMetadata,
    _copy_file_no_follow as _focused_copy_file_no_follow,
    _remove_owned_tree as _focused_remove_owned_tree,
    _file_proof as _focused_file_proof,
    _render_file_manifest as _focused_render_file_manifest,
    _read_regular_no_follow,
    build_final_whole_authorization_evidence,
    build_focused_recovery_evidence,
    compile_manifest_sha256,
    focused_gate_evidence_payload,
    focused_recovery_evidence_payload,
    material_resolution_proof,
    validate_focused_gate_evidence_payload,
    validate_focused_target,
)
from .focused_regions import FocusSelection, select_focus_targets_with_evidence
from .family_scheduler import FamilySchedulerUpdate, FamilyWorkItem, run_family_jobs
from .meshopt_bridge import MESHOPT_ENGINE_PREFERRED
from .processes import ProcessCancelledError, run_process
from .parallelism import (
    MaximumParallelismPlan,
    current_memory_job_limit,
    detect_memory_snapshot,
    resolve_maximum_parallelism,
)
from .progress_journal import DurableProgressJournal
from .qc_inventory import _inventory_qc, build_family_manifests
from .qc_graph import QcGraph, parse_qc_graph
from .reference_bundle import (
    ReferenceBundle,
    ReferenceBundleIdentity,
    ReferenceBundleStore,
)
from .regions import filter_region_manifest, load_region_manifest_payload
from .reporting import atomic_write_json, canonical_json, canonical_payload, deep_freeze, event_line
from .search import blender_adaptive_candidates, choose_next, select_winner
from .structural_validation import validate_structure
from .visual_validation import (
    EXPECTED_ANGLES,
    EXPECTED_PASSES,
    FidelityProfile,
    REQUIRED_METRICS,
    compare_render_sets,
    load_profile,
)


EVENT_KINDS = frozenset(
    {
        "run_started",
        "family_started",
        "candidate_started",
        "stage",
        "candidate_finished",
        "best_updated",
        "family_finished",
        "run_finished",
        "run_cancelled",
        "scheduler_status",
    }
)
_RATIOS = (0.75, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05)
_POSITION_DIRECT_TARGET_ERRORS = (0.005, 0.00625, 0.0075, 0.009, 0.01, 0.015, 0.02)
_ENGINE_NAMES = frozenset({"fidelity", "blender", "meshoptimizer"})
_IO_CHUNK_SIZE = 1024 * 1024
_REFERENCE_RENDER_FILES_PER_STATE = 33
_REFERENCE_RENDER_BYTES_PER_STATE = 512 * 1024 ** 2


class MaximumConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MaximumRunConfig:
    addon_dir: Path
    output_dir: Path
    work_dir: Path
    blender_path: Path
    studiomdl_path: Path
    repo_root: Path
    budget: SearchBudget
    profile_path: Path
    resume: bool = False
    overwrite: bool = False
    parallelism: MaximumParallelismPlan = field(
        default_factory=MaximumParallelismPlan.serial
    )

    def __post_init__(self) -> None:
        for field_name in (
            "addon_dir", "output_dir", "work_dir", "blender_path",
            "studiomdl_path", "repo_root", "profile_path",
        ):
            raw = Path(getattr(self, field_name)).expanduser()
            # Keep the lexical path. Resolving here would erase a symlink/junction
            # component before the safety validator has a chance to reject it.
            object.__setattr__(
                self, field_name, Path(os.path.abspath(os.fspath(raw)))
            )
        if not isinstance(self.budget, SearchBudget):
            raise TypeError("budget must be a SearchBudget")
        if type(self.budget.max_candidates) is not int or self.budget.max_candidates < 1:
            raise ValueError("budget.max_candidates must be a positive integer")
        for name, value, low_exclusive in (
            ("min_ratio_step", self.budget.min_ratio_step, True),
            ("min_marginal_saving", self.budget.min_marginal_saving, False),
        ):
            if isinstance(value, bool) or type(value) not in (int, float):
                raise TypeError(f"budget.{name} must be numeric")
            number = float(value)
            if not math.isfinite(number) or number > 1 or (number <= 0 if low_exclusive else number < 0):
                raise ValueError(f"budget.{name} is out of range")
        if type(self.resume) is not bool or type(self.overwrite) is not bool:
            raise TypeError("resume and overwrite must be booleans")
        if not isinstance(self.parallelism, MaximumParallelismPlan):
            raise TypeError("parallelism must be a MaximumParallelismPlan")

    def to_kwargs(self) -> dict[str, Any]:
        return {
            "addon_dir": self.addon_dir,
            "output_dir": self.output_dir,
            "work_dir": self.work_dir,
            "blender_path": self.blender_path,
            "studiomdl_path": self.studiomdl_path,
            "repo_root": self.repo_root,
            "budget": self.budget,
            "profile_path": self.profile_path,
            "resume": self.resume,
            "overwrite": self.overwrite,
            "parallelism": self.parallelism,
        }


@dataclass(frozen=True)
class AttemptReport:
    candidate_id: str
    engine: str
    status: str
    compiled_size: CompiledSizeSnapshot | None
    structural: ValidationResult | None
    visual: ValidationResult | None
    cache_hit: bool
    error: str
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", deep_freeze(dict(self.provenance)))


@dataclass(frozen=True)
class FocusedRecoveryAdapterResult:
    build: CandidateBuild | None
    evaluation: CandidateEvaluation | None
    evidence: FocusedRecoveryEvidence
    authorization: Mapping[str, object] | None
    recovery_context: FocusedRecoveryContext | None = None
    initial_records: tuple[FocusedRenderEvidence, ...] = ()
    selection: FocusSelection | None = None
    recoveries: tuple[FocusedRecoveryEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, FocusedRecoveryEvidence):
            raise TypeError("focused recovery adapter evidence is invalid")
        authorized = self.evidence.terminal_status == "authorized"
        if authorized != (
            self.build is not None and self.evaluation is not None
            and self.authorization is not None
            and isinstance(self.recovery_context, FocusedRecoveryContext)
            and bool(self.initial_records)
            and isinstance(self.selection, FocusSelection)
            and bool(self.recoveries)
        ):
            raise ValueError("focused recovery adapter result matrix is invalid")
        if not authorized and (self.build is not None or self.authorization is not None):
            raise ValueError("failed recovery cannot expose an authorizable build")
        if authorized and (
            self.recoveries[-1] != self.evidence
            or tuple(item.round_index for item in self.recoveries)
            != tuple(range(len(self.recoveries)))
            or len(self.recoveries)
            > self.recovery_context.base_context.policy.max_recovery_rounds
        ):
            raise ValueError("authorized recovery cumulative history is invalid")
        if self.evaluation is not None and not isinstance(
            self.evaluation, CandidateEvaluation
        ):
            raise TypeError("focused recovery diagnostic evaluation is invalid")
        if self.build is not None:
            if not isinstance(self.build, CandidateBuild) or self.build.source_snapshot is None:
                raise ValueError("authorized recovery build has no source snapshot")
            if not isinstance(self.evaluation, CandidateEvaluation) or not self.evaluation.passed:
                raise ValueError("authorized recovery evaluation did not pass")
            if self.build.spec != self.evaluation.spec:
                raise ValueError("authorized recovery build/evaluation specs differ")
            payload = dict(self.authorization)
            if (
                payload.get("schema") != 2
                or payload.get("candidate_id") != self.build.spec.candidate_id
                or payload.get("evidence_sha256") is None
            ):
                raise ValueError("authorized recovery schema-2 payload is invalid")
            object.__setattr__(self, "authorization", deep_freeze(payload))


@dataclass(frozen=True)
class FamilyRunOutcome:
    family_id: str
    model_rel: str
    status: str
    selected_candidate: str | None
    original_size: CompiledSizeSnapshot
    control_size: CompiledSizeSnapshot | None
    selected_size: CompiledSizeSnapshot
    savings: Mapping[str, int | float]
    attempts: tuple[AttemptReport, ...]
    reason: str
    worst_metrics: Mapping[str, float]
    worst_scopes: Mapping[str, str]
    provenance: Mapping[str, str]
    metric_margins: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "savings", deep_freeze(dict(self.savings)))
        object.__setattr__(self, "worst_metrics", deep_freeze(dict(self.worst_metrics)))
        object.__setattr__(self, "worst_scopes", deep_freeze(dict(self.worst_scopes)))
        object.__setattr__(self, "provenance", deep_freeze(dict(self.provenance)))
        object.__setattr__(self, "metric_margins", deep_freeze(dict(self.metric_margins)))


@dataclass(frozen=True)
class MaximumRunReport:
    schema: int
    status: str
    original_size: CompiledSizeSnapshot
    control_size: CompiledSizeSnapshot
    selected_size: CompiledSizeSnapshot
    final_size: CompiledSizeSnapshot
    tool_versions: Mapping[str, str]
    families: tuple[FamilyRunOutcome, ...]
    report_path: Path
    events: tuple[Mapping[str, object], ...] = ()
    cancelled: bool = False
    parallelism: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("unsupported Maximum report schema")
        object.__setattr__(self, "tool_versions", deep_freeze(dict(self.tool_versions)))
        object.__setattr__(self, "events", deep_freeze(tuple(self.events)))
        object.__setattr__(self, "parallelism", deep_freeze(dict(self.parallelism)))


class AdapterSet(Protocol):
    def inventory(self, config: MaximumRunConfig) -> Sequence[FamilyManifest]: ...
    def build(
        self,
        manifest: FamilyManifest,
        spec: CandidateSpec,
        workspace: Path,
        tools: CandidateTools,
        cancel_event: threading.Event,
    ) -> CandidateBuild: ...
    def visual(
        self,
        manifest: FamilyManifest,
        control: CandidateBuild,
        candidate: CandidateBuild,
        profile: FidelityProfile,
        *,
        focused_profile: FidelityProfile | None = None,
    ) -> ValidationResult: ...
    def focused_visual(
        self,
        manifest: FamilyManifest,
        control: CandidateBuild,
        candidate: CandidateBuild,
        whole_profile: FidelityProfile,
        focused_profile: FidelityProfile,
        policy: FocusedRegionPolicy,
    ) -> FocusedGateResult: ...
    def tool_versions(self) -> Mapping[str, str]: ...


StructuralValidator = Callable[[FamilyManifest, CandidateBuild], ValidationResult]
EventSink = Callable[[dict[str, Any]], None]


def _snapshot_from_artifacts(root: Path, artifacts: Sequence[ArtifactStat]) -> CompiledSizeSnapshot:
    by_kind: dict[str, int] = {}
    by_lod: dict[int, int] = {}
    for artifact in artifacts:
        by_kind[artifact.kind] = by_kind.get(artifact.kind, 0) + artifact.size_bytes
        for index, vertices in enumerate(artifact.lod_vertices):
            by_lod[index] = by_lod.get(index, 0) + vertices
    return CompiledSizeSnapshot(
        root,
        sum(item.size_bytes for item in artifacts),
        dict(sorted(by_kind.items())),
        dict(sorted(by_lod.items())),
        tuple(artifacts),
    )


def _without_dx80(snapshot: CompiledSizeSnapshot) -> CompiledSizeSnapshot:
    return _snapshot_from_artifacts(
        snapshot.root,
        tuple(
            artifact
            for artifact in snapshot.artifacts
            if artifact.kind.casefold() != ".dx80.vtx"
            and not artifact.relative_path.casefold().endswith(".dx80.vtx")
        ),
    )


def _family_snapshot(snapshot: CompiledSizeSnapshot, model_rel: str) -> CompiledSizeSnapshot:
    artifacts = tuple(
        item for item in snapshot.artifacts
        if _is_exact_family_artifact(item.relative_path, model_rel)
    )
    return _snapshot_from_artifacts(snapshot.root, artifacts)


def _combine_snapshots(root: Path, snapshots: Sequence[CompiledSizeSnapshot]) -> CompiledSizeSnapshot:
    artifacts: list[ArtifactStat] = []
    for index, snapshot in enumerate(snapshots):
        for artifact in snapshot.artifacts:
            artifacts.append(
                ArtifactStat(
                    f"{index:06d}/{artifact.relative_path}",
                    artifact.kind,
                    artifact.size_bytes,
                    artifact.lod_vertices,
                )
            )
    return _snapshot_from_artifacts(root, artifacts)


def _empty_snapshot(root: Path) -> CompiledSizeSnapshot:
    return CompiledSizeSnapshot(root, 0, {}, {}, ())


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(first: Path, second: Path) -> bool:
    return _within(first, second) or _within(second, first)


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _first_reparse(root: Path) -> Path | None:
    for directory, names, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in (*names, *files):
            candidate = parent / name
            if _is_reparse(candidate):
                return candidate
    return None


def _existing_components(path: Path) -> tuple[Path, ...]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    components: list[Path] = []
    current = Path(absolute.anchor)
    if current.exists():
        components.append(current)
    for part in absolute.parts[1:]:
        current = current / part
        if not os.path.lexists(current):
            break
        components.append(current)
    return tuple(components)


def validate_source_path(path: Path, *, require_models: bool) -> None:
    """Validate a user-supplied lexical source path before following it."""
    path = Path(os.path.abspath(os.fspath(path)))
    for component in _existing_components(path):
        if _is_reparse(component):
            raise MaximumConfigError(
                f"addon path contains a symlink/junction/reparse component: {component.name}"
            )
    if _is_reparse(path) or not path.is_dir():
        raise MaximumConfigError("addon_dir must be an existing non-reparse directory")
    if require_models and not (path / "models").is_dir():
        raise MaximumConfigError("addon_dir must contain models")
    internal = _first_reparse(path)
    if internal is not None:
        raise MaximumConfigError(f"addon_dir contains a symlink or junction: {internal.name}")


def validate_path_triplet(
    addon_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    overwrite: bool,
    require_models: bool = True,
) -> None:
    addon_dir = Path(os.path.abspath(os.fspath(addon_dir)))
    output_dir = Path(os.path.abspath(os.fspath(output_dir)))
    work_dir = Path(os.path.abspath(os.fspath(work_dir)))
    validate_source_path(addon_dir, require_models=require_models)
    for label, path in (
        ("output", output_dir),
        ("work", work_dir),
    ):
        for component in _existing_components(path):
            if _is_reparse(component):
                raise MaximumConfigError(
                    f"{label} path contains a symlink/junction/reparse component: {component.name}"
                )
    resolved_addon = addon_dir.resolve(strict=True)
    resolved_output = output_dir.resolve(strict=False)
    resolved_work = work_dir.resolve(strict=False)
    for first_name, first, second_name, second in (
        ("output", resolved_output, "addon", resolved_addon),
        ("work", resolved_work, "addon", resolved_addon),
        ("output", resolved_output, "work", resolved_work),
    ):
        if _overlaps(first, second):
            raise MaximumConfigError(f"{first_name} and {second_name} directories overlap")
    for path, label in ((output_dir, "output"), (work_dir, "work")):
        if _is_reparse(path):
            raise MaximumConfigError(f"{label} directory cannot be a symlink or junction")
        if path.is_dir():
            internal_reparse = _first_reparse(path)
            if internal_reparse is not None:
                raise MaximumConfigError(
                    f"{label} directory contains a symlink or junction: {internal_reparse.name}"
                )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise MaximumConfigError("output must be a directory when it already exists")
        if not overwrite:
            raise MaximumConfigError("output exists and overwrite is disabled")


def validate_run_paths(config: MaximumRunConfig, *, create: bool) -> None:
    validate_path_triplet(
        config.addon_dir,
        config.output_dir,
        config.work_dir,
        overwrite=config.overwrite,
    )
    if create:
        config.output_dir.parent.mkdir(parents=True, exist_ok=True)
        config.work_dir.mkdir(parents=True, exist_ok=True)


def _default_schedule() -> tuple[CandidateSpec, ...]:
    fidelity = CandidateSpec("fidelity-baseline", "fidelity", 0.50, 0.0, "fidelity-current")
    blender = tuple(
        CandidateSpec(f"blender-r{str(ratio).replace('.', '')}", "blender", ratio, 0.01, "transfer-v1")
        for ratio in _RATIOS
    )
    meshopt = tuple(
        CandidateSpec(
            f"meshopt-position-e{str(target_error).replace('.', '')}",
            "meshoptimizer", 0.20, target_error, "meshopt-direct-position-v1",
            strategy="meshopt-direct-position-v1",
            update_vertices=False,
            transfer="direct-v1",
        )
        for target_error in _POSITION_DIRECT_TARGET_ERRORS
    )
    if os.environ.get("MAXIMUM_RND_BLENDER_ADAPTIVE") == "1":
        return (fidelity, *blender_adaptive_candidates(), *meshopt)
    return (*meshopt, fidelity, *blender) if MESHOPT_ENGINE_PREFERRED else (fidelity, *blender, *meshopt)


def _default_sink(event: dict[str, Any]) -> None:
    print(event_line(event), flush=True)


def _validation_payload(result: ValidationResult) -> dict[str, Any]:
    return canonical_payload(result)


def _validation_from_payload(payload: Mapping[str, Any]) -> ValidationResult:
    from .domain import GateFailure
    if type(payload) is not dict or set(payload) != {"passed", "failures", "metrics", "worst_scope"}:
        raise ValueError("cached validation schema is invalid")
    if (
        type(payload["passed"]) is not bool
        or type(payload["failures"]) is not list
        or type(payload["metrics"]) is not dict
        or type(payload["worst_scope"]) is not str
    ):
        raise ValueError("cached validation types are invalid")
    failures = tuple(GateFailure(**item) for item in payload["failures"])
    metrics: dict[str, float] = {}
    for name, value in payload["metrics"].items():
        if type(name) is not str or isinstance(value, bool) or type(value) not in (int, float):
            raise ValueError("cached validation metric is invalid")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("cached validation metric is non-finite")
        metrics[name] = number
    return ValidationResult(
        payload["passed"], failures, metrics, payload["worst_scope"],
    )


def _check_cancelled(cancel_event: threading.Event | None, message: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError(message)


def _file_proof(
    path: Path,
    cancel_event: threading.Event | None = None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> tuple[int, str]:
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        while True:
            _check_cancelled(cancel_event, "cancelled while hashing files")
            block = stream.read(chunk_size)
            if not block:
                break
            digest.update(block)
            size += len(block)
    _check_cancelled(cancel_event, "cancelled while hashing files")
    return size, digest.hexdigest()


def _sha256_file(
    path: Path,
    cancel_event: threading.Event | None = None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> str:
    return _file_proof(path, cancel_event, chunk_size=chunk_size)[1]


def _copy_file_cancellable(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    cancel_event: threading.Event | None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> str:
    source_path = Path(source)
    destination_path = Path(destination)
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    _check_cancelled(cancel_event, "cancelled while copying files")
    try:
        with source_path.open("rb") as reader, destination_path.open("wb") as writer:
            while True:
                _check_cancelled(cancel_event, "cancelled while copying files")
                block = reader.read(chunk_size)
                if not block:
                    break
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        _check_cancelled(cancel_event, "cancelled while copying files")
        shutil.copystat(source_path, destination_path, follow_symlinks=False)
    except BaseException:
        try:
            if destination_path.exists() and not _is_reparse(destination_path):
                destination_path.unlink()
        except OSError:
            pass
        raise
    return str(destination_path)


def _copytree_cancellable(
    source: Path,
    destination: Path,
    cancel_event: threading.Event | None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> Path:
    source = Path(source)
    destination = Path(destination)
    _check_cancelled(cancel_event, "cancelled before tree copy")
    if _is_reparse(source) or not source.is_dir():
        raise ValueError("copy source must be a non-reparse directory")
    if os.path.lexists(destination):
        raise ValueError("copy destination must not exist")
    destination.mkdir(parents=True)
    try:
        for directory, directory_names, file_names in os.walk(source, followlinks=False):
            _check_cancelled(cancel_event, "cancelled during tree copy")
            parent = Path(directory)
            relative_parent = parent.relative_to(source)
            target_parent = destination / relative_parent
            for name in directory_names:
                child = parent / name
                if _is_reparse(child):
                    raise ValueError("copy source contains a reparse directory")
                (target_parent / name).mkdir()
            for name in file_names:
                child = parent / name
                if _is_reparse(child) or not stat.S_ISREG(child.lstat().st_mode):
                    raise ValueError("copy source contains a reparse or special file")
                _copy_file_cancellable(
                    child,
                    target_parent / name,
                    cancel_event,
                    chunk_size=chunk_size,
                )
        for directory, _directory_names, _file_names in os.walk(source, topdown=False):
            parent = Path(directory)
            shutil.copystat(
                parent,
                destination / parent.relative_to(source),
                follow_symlinks=False,
            )
        _check_cancelled(cancel_event, "cancelled after tree copy")
    except BaseException:
        if destination.exists() and not _is_reparse(destination):
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _remove_dx80_artifacts(
    models_root: Path,
    cancel_event: threading.Event | None,
) -> None:
    root = Path(models_root).resolve(strict=True)
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        _check_cancelled(cancel_event, "cancelled while removing DX80 artifacts")
        parent = Path(directory)
        for name in directory_names:
            if _is_reparse(parent / name):
                raise ValueError("models output contains a reparse directory")
        for name in file_names:
            if not name.casefold().endswith(".dx80.vtx"):
                continue
            path = parent / name
            if (
                _is_reparse(path)
                or not stat.S_ISREG(path.lstat().st_mode)
                or not _within(path.resolve(strict=True), root)
            ):
                raise ValueError("DX80 output artifact is unsafe")
            path.unlink()
    _check_cancelled(cancel_event, "cancelled after removing DX80 artifacts")


def _restore_cache_payload(
    cache_entry: Path,
    workspace: Path,
    cancel_event: threading.Event | None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> Path:
    if workspace.exists():
        if _is_reparse(workspace) or not workspace.is_dir():
            raise ValueError("cache restore workspace is invalid")
        shutil.rmtree(workspace)
    return _copytree_cancellable(
        cache_entry / "payload",
        workspace,
        cancel_event,
        chunk_size=chunk_size,
    )


def _configured_vtfcmd(config: MaximumRunConfig) -> Path | None:
    local_root = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "GmodAddonOptimizer" / "tools" / "VTFEdit"
    )
    versioned: tuple[Path, ...] = ()
    try:
        if local_root.is_dir() and not _is_reparse(local_root):
            versioned = tuple(
                child / "VTFEdit" / "VTFCmd.exe"
                for child in sorted(
                    (item for item in local_root.iterdir() if item.is_dir()),
                    key=lambda item: item.name.casefold(),
                    reverse=True,
                )
            )
    except OSError:
        versioned = ()
    candidates = (
        Path(os.environ["VTFCMD"]).expanduser() if os.environ.get("VTFCMD") else None,
        config.repo_root / "VTFEdit" / "VTFCmd.exe",
        config.repo_root / "tools" / "VTFEdit" / "VTFCmd.exe",
        local_root / "VTFCmd.exe",
        *versioned,
    )
    return next((
        path.resolve()
        for path in candidates
        if path is not None
        and path.is_file()
        and all(not _is_reparse(component) for component in _existing_components(path))
    ), None)


def _dependency_material_root_identities(config: MaximumRunConfig) -> tuple[str, ...]:
    values = [config.addon_dir / "materials"]
    values.extend(
        Path(value).expanduser()
        for value in os.environ.get("MAXIMUM_MATERIAL_ROOTS", "").split(os.pathsep)
        if value.strip()
    )
    return tuple(dict.fromkeys(str(value.resolve()) for value in values))


def _dependency_proof(
    config: MaximumRunConfig,
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    paths: list[tuple[str, Path]] = [
        ("tool/python", Path(sys.executable)),
        ("tool/blender", config.blender_path),
        ("tool/studiomdl", config.studiomdl_path),
        ("profile", config.profile_path),
        ("policy", config.work_dir / "logs" / "selective_policy_map.json"),
    ]
    for name in (
        "batch_optimize_qc.py",
        "batch_optimize_round_parts_policy.py",
        "batch_optimize_maximum.py",
        "batch_compile_opt_qc.py",
        "render_previews.py",
        "vehicle_steer_turn_basis_fix.py",
    ):
        paths.append((f"script/{name}", config.repo_root / name))
    package = config.repo_root / "maximum_optimizer"
    if package.is_dir():
        for path in sorted(package.rglob("*.py"), key=lambda item: item.relative_to(package).as_posix()):
            paths.append((f"module/{path.relative_to(package).as_posix()}", path))
    addon_roots = list(config.blender_path.parent.glob("*/scripts/addons/io_scene_valvesource"))
    appdata = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    if appdata.is_dir():
        addon_roots.extend(appdata.glob("*/scripts/addons/io_scene_valvesource"))
    for root in sorted({path.resolve() for path in addon_roots if path.is_dir()}):
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
            paths.append((f"blender_source_tools/{root.parent.parent.parent.name}/{path.relative_to(root).as_posix()}", path))
    for origin, base in (("system", config.blender_path.parent), ("user", appdata)):
        if not base.is_dir():
            continue
        containers = list(base.glob("*/extensions"))
        if (base / "extensions").is_dir():
            containers.append(base / "extensions")
        for container in sorted({path.resolve() for path in containers}):
            version = container.parent.name
            for path in sorted(
                (item for item in container.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(container).as_posix(),
            ):
                paths.append((
                    f"blender_extension/{origin}/{version}/{path.relative_to(container).as_posix()}",
                    path,
                ))
    paths.append((
        "tool/meshopt_bridge",
        config.repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll",
    ))
    vtfcmd = _configured_vtfcmd(config)
    paths.append((
        "tool/vtfcmd",
        vtfcmd if vtfcmd is not None else config.repo_root / "tools" / "VTFEdit" / "VTFCmd.exe",
    ))
    files: list[dict[str, object]] = []
    for label, path in paths:
        if path.is_file() and not _is_reparse(path):
            size, digest = _file_proof(path, cancel_event)
            files.append({
                "label": label,
                "state": "file",
                "size": size,
                "sha256": digest,
            })
        else:
            files.append({"label": label, "state": "missing", "size": 0, "sha256": ""})
    payload = {
        "schema": 1, "files": files,
        "material_roots": list(_dependency_material_root_identities(config)),
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _payload_file_manifest(
    root: Path,
    cancel_event: threading.Event | None = None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> tuple[dict[str, object], ...]:
    root = root.resolve(strict=True)
    entries: list[dict[str, object]] = []
    folded: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in tuple(directory_names):
            path = parent / name
            if _is_reparse(path):
                raise ValueError("cache payload contains a reparse directory")
        for name in file_names:
            path = parent / name
            if _is_reparse(path):
                raise ValueError("cache payload contains a reparse file")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("cache payload contains a special file")
            relative = path.relative_to(root).as_posix()
            if relative.casefold() in folded:
                raise ValueError("cache payload contains case-colliding paths")
            folded.add(relative.casefold())
            size, digest = _file_proof(path, cancel_event, chunk_size=chunk_size)
            entries.append({
                "path": relative,
                "size": size,
                "sha256": digest,
            })
    return tuple(sorted(entries, key=lambda item: str(item["path"])))


def _seal_cache_entry(
    cache_entry: Path,
    cancel_event: threading.Event | None = None,
) -> None:
    files = _payload_file_manifest(cache_entry / "payload", cancel_event)
    record = next(
        (item for item in files if item["path"] == "maximum_cache_record.json"),
        None,
    )
    if record is None:
        raise ValueError("cache record is missing from payload")
    atomic_write_json(
        cache_entry / "maximum_integrity.json",
        {
            "schema": 1,
            "record_sha256": record["sha256"],
            "files": files,
        },
    )


def _verify_cache_entry(
    cache_entry: Path,
    cancel_event: threading.Event | None = None,
) -> bool:
    try:
        if _is_reparse(cache_entry) or _is_reparse(cache_entry / "payload"):
            return False
        marker = json.loads((cache_entry / "maximum_integrity.json").read_text(encoding="utf-8"))
        if type(marker) is not dict or set(marker) != {"schema", "record_sha256", "files"}:
            return False
        if type(marker["schema"]) is not int or marker["schema"] != 1:
            return False
        if type(marker["record_sha256"]) is not str or len(marker["record_sha256"]) != 64:
            return False
        raw_files = marker["files"]
        if type(raw_files) is not list:
            return False
        expected: list[dict[str, object]] = []
        for item in raw_files:
            if type(item) is not dict or set(item) != {"path", "size", "sha256"}:
                return False
            if (
                type(item["path"]) is not str
                or not item["path"]
                or type(item["size"]) is not int
                or item["size"] < 0
                or type(item["sha256"]) is not str
                or len(item["sha256"]) != 64
            ):
                return False
            expected.append(item)
        actual = list(_payload_file_manifest(cache_entry / "payload", cancel_event))
        if expected != actual:
            return False
        record = next((item for item in actual if item["path"] == "maximum_cache_record.json"), None)
        return record is not None and record["sha256"] == marker["record_sha256"]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _verify_recovery_cache_entry(
    cache_entry: Path,
    key: CacheKey,
    cancel_event: threading.Event | None = None,
) -> bool:
    if not _verify_cache_entry(cache_entry, cancel_event):
        return False
    try:
        complete = json.loads(_read_regular_no_follow(
            cache_entry / "complete.json", cancel_event,
            contained_root=cache_entry,
        ).decode("utf-8"))
        if type(complete) is not dict or set(complete) != {
            "schema", "digest", "metadata_sha256", "integrity_sha256",
        }:
            return False
        if complete["schema"] != 2 or complete["digest"] != key.digest:
            return False
        for name, path in (
            ("metadata_sha256", cache_entry / "metadata.json"),
            ("integrity_sha256", cache_entry / "maximum_integrity.json"),
        ):
            raw = _read_regular_no_follow(
                path, cancel_event, contained_root=cache_entry,
            )
            if hashlib.sha256(raw).hexdigest() != complete[name]:
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _seal_then_validate_private_recovery_entry(
    entry: Path,
    semantic_validator: Callable[[], None],
) -> None:
    _seal_cache_entry(entry, None)
    semantic_validator()
    if not _verify_cache_entry(entry, None):
        raise ValueError("focused recovery cache integrity is invalid")


def _cache_record_path(workspace: Path) -> Path:
    return workspace / "maximum_cache_record.json"


def _profile_contract_sha256(profile: FidelityProfile, profile_file_sha256: str) -> str:
    if not isinstance(profile, FidelityProfile) or _SHA256_RE.fullmatch(profile_file_sha256) is None:
        raise ValueError("recovery profile proof is invalid")
    payload = {
        "version": profile.version,
        "corpus_hash": profile.corpus_hash,
        "profile_file_sha256": profile_file_sha256,
        "limits": dict(profile.limits),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _attach_recovery_source_snapshot(
    build: CandidateBuild,
    manifest: FamilyManifest,
    *,
    key: CacheKey,
    whole_profile: FidelityProfile,
    focused_profile: FidelityProfile,
    profile_file_sha256: str,
    dependency_digest: str,
    focused_by_region: Mapping[str, FocusRegionResult],
    cancel_event: threading.Event,
) -> CandidateBuild:
    source_root = build.workspace / "src"
    graph = parse_qc_graph(build.optimized_qc, source_root)
    source_manifest = build_source_tree_manifest(
        source_root, graph, "candidate-source-v1", cancel_event
    )
    refs = tuple(sorted((
        FocusedEvidenceRef(region_key, result.evidence_sha256)
        for region_key, result in focused_by_region.items()
    ), key=lambda item: (item.region_key.casefold(), item.region_key)))
    snapshot = build_recovery_source_snapshot(
        kind="candidate",
        family_id=manifest.family_id,
        family_input_sha256=manifest.input_hash,
        optimizer_contract_sha256=optimizer_contract_sha256(build.spec),
        whole_profile_sha256=_profile_contract_sha256(whole_profile, profile_file_sha256),
        focused_profile_sha256=_profile_contract_sha256(focused_profile, profile_file_sha256),
        dependency_proof_sha256=dependency_digest,
        candidate_id=build.spec.candidate_id,
        candidate_cache_digest=key.digest,
        source_root=source_root,
        source_manifest=source_manifest,
        focused_evidence=refs,
    )
    return replace(build, source_snapshot=snapshot)


def _build_original_recovery_snapshot(
    manifest: FamilyManifest,
    base_snapshot: RecoverySourceSnapshot,
    cancel_event: threading.Event,
) -> RecoverySourceSnapshot:
    original_qcs = _matching_qcs(manifest.source_dir, manifest.model_rel, optimized=False)
    if len(original_qcs) != 1:
        raise ValueError("authoritative original QC is missing or ambiguous")
    graph = parse_qc_graph(original_qcs[0], manifest.source_dir)
    source_manifest = build_source_tree_manifest(
        manifest.source_dir, graph, "original-source-v1", cancel_event
    )
    return build_recovery_source_snapshot(
        kind="original", family_id=base_snapshot.family_id,
        family_input_sha256=base_snapshot.family_input_sha256,
        optimizer_contract_sha256=base_snapshot.optimizer_contract_sha256,
        whole_profile_sha256=base_snapshot.whole_profile_sha256,
        focused_profile_sha256=base_snapshot.focused_profile_sha256,
        dependency_proof_sha256=base_snapshot.dependency_proof_sha256,
        candidate_id=None, candidate_cache_digest=None,
        source_root=manifest.source_dir, source_manifest=source_manifest,
        focused_evidence=(),
    )


def _compile_composed_candidate(
    manifest: FamilyManifest,
    spec: CandidateSpec,
    workspace: Path,
    optimized_qc: Path,
    tools: CandidateTools,
    cancel_event: threading.Event,
    *,
    process_runner=run_process,
) -> CandidateBuild:
    logs = workspace / "logs"
    logs.mkdir(exist_ok=True)
    compiled = workspace / "compiled"
    command = (
        str(tools.python_exe), str(tools.repo_root / "batch_compile_opt_qc.py"),
        str(workspace / "src"), "--out", str(compiled), "--studiomdl",
        str(tools.studiomdl_exe), "--compile-jobs", str(tools.compile_jobs),
        "--no-restore-phy",
    )
    result = process_runner(
        command, cwd=tools.repo_root, log_path=logs / "compile.log",
        cancel_event=cancel_event,
    )
    if result.returncode != 0:
        raise CandidateBuildError(
            f"composite compiler exited with code {result.returncode}",
            stage="compile", log_path=logs / "compile.log",
        )
    try:
        summary = json.loads((compiled / "compile_summary.json").read_text(encoding="utf-8"))
        records = summary["results"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CandidateBuildError("composite compile summary is invalid", stage="compile") from exc
    if type(records) is not list:
        raise CandidateBuildError("composite compile results are invalid", stage="compile")
    normalized_model = manifest.model_rel.replace("\\", "/").casefold()
    matching = [
        item for item in records
        if type(item) is dict
        and str(item.get("model_rel") or "").replace("\\", "/").casefold() == normalized_model
    ]
    if len(matching) != 1:
        raise CandidateBuildError("composite compile record is missing or ambiguous", stage="compile")
    record = matching[0]
    if record.get("status") != "ok" or type(record.get("returncode")) is not int or record["returncode"] != 0:
        raise CandidateBuildError("composite compile record failed", stage="compile")
    compiled_models = compiled / "models"
    if _is_reparse(compiled_models) or not compiled_models.is_dir():
        raise CandidateBuildError("composite compiled models directory is invalid", stage="compile")
    expected = compiled_models.joinpath(*PurePosixPath(manifest.model_rel).parts)
    if _is_reparse(expected) or not expected.is_file():
        raise CandidateBuildError("composite expected model is missing", stage="compile")
    record_expected = record.get("expected_mdl")
    if not record_expected or Path(str(record_expected)).resolve() != expected.resolve():
        raise CandidateBuildError("composite expected model binding differs", stage="compile")
    artifacts = tuple(sorted((
        path for path in compiled_models.rglob("*")
        if path.is_file() and not _is_reparse(path)
        and _is_exact_family_artifact(path.relative_to(compiled_models).as_posix(), manifest.model_rel)
    ), key=lambda path: path.relative_to(compiled_models).as_posix().casefold()))
    present_suffixes = {path.suffix.casefold() for path in artifacts}
    if any(
        (kind if str(kind).startswith(".") else "." + str(kind)).casefold()
        not in present_suffixes
        for kind in manifest.required_artifact_kinds
    ):
        raise CandidateBuildError("composite required artifact is missing", stage="compile")
    provenance = {
        path.relative_to(compiled_models).as_posix(): "candidate-compile"
        for path in artifacts
    }
    return CandidateBuild(
        spec, workspace, optimized_qc, compiled_models, dict(record), provenance,
        (tuple(str(item) for item in command),),
    )


def _store_cache_record(
    workspace: Path,
    build: CandidateBuild,
    structural: ValidationResult,
    visual: ValidationResult,
    *,
    key: CacheKey,
    manifest: FamilyManifest,
    dependency_digest: str,
) -> None:
    relative_compiled = build.compiled_models_dir.relative_to(workspace).as_posix()
    relative_qc = build.optimized_qc.relative_to(workspace).as_posix()
    record = {
            "schema": 3 if build.source_snapshot is not None else 2,
            "key_digest": key.digest,
            "family_id": manifest.family_id,
            "model_rel": manifest.model_rel,
            "input_hash": manifest.input_hash,
            "candidate": build.spec.cache_payload(),
            "dependency_digest": dependency_digest,
            "compiled_models_dir": relative_compiled,
            "optimized_qc": relative_qc,
            "compile_record": build.compile_record,
            "provenance": build.provenance,
            "commands": build.commands,
            "structural": structural,
            "visual": visual,
        }
    if build.source_snapshot is not None:
        record["source_snapshot"] = recovery_source_snapshot_payload(build.source_snapshot)
    atomic_write_json(_cache_record_path(workspace), record)


def _load_cached_build(
    cache_entry: Path,
    spec: CandidateSpec,
    *,
    key: CacheKey,
    manifest: FamilyManifest,
    dependency_digest: str,
    materialized_workspace: Path | None = None,
    cancel_event: threading.Event | None = None,
    optimizer_contract_digest: str | None = None,
    whole_profile_digest: str | None = None,
    focused_profile_digest: str | None = None,
) -> CandidateBuild:
    workspace = materialized_workspace or (cache_entry / "payload")
    payload = json.loads(_cache_record_path(workspace).read_text(encoding="utf-8"))
    expected_fields = {
        "schema", "key_digest", "family_id", "model_rel", "input_hash",
        "candidate", "dependency_digest", "compiled_models_dir", "optimized_qc",
        "compile_record", "provenance", "commands", "structural", "visual",
    }
    schema = payload.get("schema") if type(payload) is dict else None
    if schema == 3:
        expected_fields.add("source_snapshot")
    if type(payload) is not dict or set(payload) != expected_fields:
        raise ValueError("cached record schema is invalid")
    if (
        type(payload["schema"]) is not int
        or payload["schema"] not in {2, 3}
        or payload["key_digest"] != key.digest
        or payload["family_id"] != manifest.family_id
        or payload["model_rel"] != manifest.model_rel
        or payload["input_hash"] != manifest.input_hash
        or payload["candidate"] != spec.cache_payload()
        or payload["dependency_digest"] != dependency_digest
        or type(payload["compile_record"]) is not dict
        or type(payload["provenance"]) is not dict
        or not all(type(k) is str and type(v) is str for k, v in payload["provenance"].items())
        or type(payload["commands"]) is not list
    ):
        raise ValueError("cached record does not match the requested candidate")
    _validation_from_payload(payload["structural"])
    _validation_from_payload(payload["visual"])
    def contained_relative(raw: object, *, directory: bool) -> Path:
        if not isinstance(raw, str):
            raise ValueError("cached path must be a string")
        relative = PurePosixPath(raw.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("cached path escapes payload")
        result = workspace.joinpath(*relative.parts)
        if not _within(result.resolve(), workspace.resolve()) or result.is_symlink():
            raise ValueError("cached path escapes payload")
        if directory and not result.is_dir():
            raise ValueError("cached directory is missing")
        if not directory and not result.is_file():
            raise ValueError("cached file is missing")
        return result
    optimized_qc = contained_relative(payload["optimized_qc"], directory=False)
    compiled_models = contained_relative(payload["compiled_models_dir"], directory=True)
    build = CandidateBuild(
        spec,
        workspace,
        optimized_qc,
        compiled_models,
        payload["compile_record"],
        payload["provenance"],
        tuple(tuple(command) for command in payload.get("commands", ())),
        (
            recovery_source_snapshot_from_payload(
                payload["source_snapshot"], source_root=workspace / "src"
            )
            if payload["schema"] == 3 else None
        ),
    )
    if build.source_snapshot is not None:
        if (
            build.source_snapshot.candidate_id != spec.candidate_id
            or build.source_snapshot.candidate_cache_digest != key.digest
            or build.source_snapshot.family_id != manifest.family_id
            or build.source_snapshot.family_input_sha256 != manifest.input_hash
            or build.source_snapshot.dependency_proof_sha256 != dependency_digest
            or optimizer_contract_digest is None
            or whole_profile_digest is None
            or focused_profile_digest is None
            or build.source_snapshot.optimizer_contract_sha256 != optimizer_contract_digest
            or build.source_snapshot.whole_profile_sha256 != whole_profile_digest
            or build.source_snapshot.focused_profile_sha256 != focused_profile_digest
        ):
            raise ValueError("cached recovery snapshot binding mismatch")
        revalidate_recovery_snapshot(build.source_snapshot, cancel_event)
    return build


def _copy_selected_family(
    build: CandidateBuild,
    output_models: Path,
    model_rel: str,
    cancel_event: threading.Event | None = None,
) -> dict[str, str]:
    source_root = build.compiled_models_dir.resolve()
    destination_root = output_models.resolve()
    promoted: dict[str, str] = {}
    if not build.provenance:
        raise ValueError("candidate provenance must be nonempty")
    for logical, provenance in sorted(build.provenance.items()):
        relative = PurePosixPath(logical.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"candidate provenance escapes models root: {logical}")
        if not _is_exact_family_artifact(relative.as_posix(), model_rel):
            raise ValueError(f"candidate provenance contains a non-family artifact: {logical}")
        if provenance != "candidate-compile":
            raise ValueError(f"candidate provenance is not compile-owned: {logical}")
        source = source_root.joinpath(*relative.parts)
        destination = destination_root.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file() or not _within(source.resolve(), source_root):
            raise ValueError(f"candidate artifact is invalid: {logical}")
        if not _within(destination.resolve(strict=False), destination_root):
            raise ValueError(f"candidate destination escapes models root: {logical}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_cancellable(source, destination, cancel_event)
        promoted[relative.as_posix()] = str(provenance)
    return promoted


def _copy_authorized_recovery_family(
    build: CandidateBuild,
    output_models: Path,
    manifest: FamilyManifest,
    proofs: tuple[CompileFileProof, ...],
    cancel_event: threading.Event | None,
) -> dict[str, str]:
    source_root = build.compiled_models_dir.resolve(strict=True)
    output_root = output_models.resolve(strict=True)
    promoted: dict[str, str] = {}
    for proof in proofs:
        if not _is_exact_family_artifact(proof.relative_path, manifest.model_rel):
            raise ValueError("authorized compile proof contains a non-family artifact")
        source = source_root / PurePosixPath(proof.relative_path)
        destination = output_root / PurePosixPath(proof.relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_cancellable(source, destination, cancel_event)
        size, digest = _focused_file_proof(
            destination, cancel_event, contained_root=output_root,
            max_bytes=2 * 1024 ** 3,
        )
        if size != proof.size or digest != proof.sha256:
            raise ValueError("staged recovery artifact differs from authorization")
        promoted[proof.relative_path] = build.provenance[proof.relative_path]
    staged_build = replace(build, compiled_models_dir=output_root)
    if _current_recovery_compile_files(
        manifest, staged_build, cancel_event
    ) != proofs:
        raise ValueError("staged recovery family manifest differs from authorization")
    return promoted


def _is_exact_family_artifact(logical: str, model_rel: str) -> bool:
    normalized = PurePosixPath(logical.replace("\\", "/")).as_posix().casefold()
    base = PurePosixPath(model_rel.replace("\\", "/")).with_suffix("").as_posix().casefold()
    if normalized in {base + suffix for suffix in (".mdl", ".vvd", ".phy", ".ani", ".vtx")}:
        return True
    prefix, suffix = base + ".", ".vtx"
    if normalized.startswith(prefix) and normalized.endswith(suffix):
        variant = normalized[len(prefix):-len(suffix)]
        return bool(variant) and all(char.isalnum() or char in "_-" for char in variant)
    return False


def _current_recovery_compile_files(
    manifest: FamilyManifest,
    build: CandidateBuild,
    cancel_event: threading.Event | None,
) -> tuple[CompileFileProof, ...]:
    """Rebuild the exact compiled-family manifest from current contained bytes."""
    root = build.compiled_models_dir.resolve(strict=True)
    paths: list[tuple[str, Path]] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in tuple(directory_names):
            if _is_reparse(parent / name):
                raise ValueError("recovery compiled tree contains a reparse directory")
        for name in file_names:
            path = parent / name
            if _is_reparse(path):
                raise ValueError("recovery compiled tree contains a reparse file")
            relative = path.relative_to(root).as_posix()
            if _is_exact_family_artifact(relative, manifest.model_rel):
                paths.append((relative, path))
                if len(paths) > 64:
                    raise ValueError("compiled recovery artifact count exceeds 64")
    paths.sort(key=lambda item: (item[0].casefold(), item[0]))
    folded = [relative.casefold() for relative, _path in paths]
    if len(folded) != len(set(folded)):
        raise ValueError("compiled recovery artifacts case-collide")
    provenance = tuple(sorted(
        (relative for relative in build.provenance if _is_exact_family_artifact(
            relative, manifest.model_rel
        )),
        key=lambda item: (item.casefold(), item),
    ))
    if tuple(relative for relative, _path in paths) != provenance:
        raise ValueError("compiled recovery membership differs from build provenance")
    values: list[CompileFileProof] = []
    total = 0
    for relative, path in paths:
        size, digest = _focused_file_proof(
            path, cancel_event, contained_root=root,
            max_bytes=2 * 1024 ** 3 - total,
        )
        total += size
        values.append(CompileFileProof(relative, path.suffix.casefold(), size, digest))
    if not values:
        raise ValueError("compiled recovery family is empty")
    return tuple(values)


def _validate_current_focused_rerun_files(
    result: FocusedRecoveryAdapterResult,
    workspace: Path,
    cancel_event: threading.Event | None,
) -> None:
    root = workspace.resolve(strict=True)
    for recovery in result.recoveries:
        for record in recovery.rerun_records:
            target_root = (
                root / "focused-authorized"
                / f"round-{recovery.round_index:03d}"
                / f"{record.target.rank:03d}-{record.target.region_key}"
            )
            directories = FocusRenderDirectories(
                target_root / "reference", target_root / "candidate"
            )
            current = _focused_render_file_manifest(directories, cancel_event)
            if current != record.files:
                raise ValueError("authorized recovery focused render bytes changed")


def _validate_authorized_recovery_boundary(
    *,
    result: FocusedRecoveryAdapterResult,
    manifest: FamilyManifest,
    recipe,
    cache_key: CacheKey,
    cancel_event: threading.Event | None,
) -> Mapping[str, object]:
    """Authorize from current bytes at the outer orchestration boundary."""
    if not isinstance(result, FocusedRecoveryAdapterResult):
        raise ValueError("recovery boundary adapter result is invalid")
    if result.evidence.terminal_status != "authorized":
        raise ValueError("recovery boundary requires an authorized typed round")
    build = result.build
    evaluation = result.evaluation
    final = result.evidence.final_whole
    structural = result.evidence.structural
    if (
        build is None or evaluation is None or final is None or structural is None
        or result.authorization is None
        or result.recovery_context is None or result.selection is None
        or not result.initial_records
    ):
        raise ValueError("authorized recovery boundary inputs are incomplete")
    if not evaluation.structural.passed or not evaluation.visual.passed:
        raise ValueError("authorized recovery evaluation does not pass all gates")
    if (
        evaluation.structural != structural.validation
        or evaluation.whole_visual != final.validation
    ):
        raise ValueError("authorized recovery evaluation differs from typed gates")
    if build.source_snapshot is None:
        raise ValueError("authorized recovery source snapshot is missing")
    snapshot = build.source_snapshot
    if (
        snapshot.candidate_id != build.spec.candidate_id
        or snapshot.candidate_cache_digest != cache_key.digest
        or final.candidate_cache_digest != cache_key.digest
        or structural.candidate_cache_digest != cache_key.digest
        or final.recipe_sha256 != recipe.recipe_sha256
        or final.composition_evidence_sha256
        != result.evidence.composition.evidence_sha256
        or snapshot.source_manifest.digest
        != result.evidence.composition.composed_manifest_sha256
    ):
        raise ValueError("authorized recovery byte contracts differ")
    revalidate_recovery_snapshot(snapshot, cancel_event)
    _validate_current_focused_rerun_files(result, build.workspace, cancel_event)
    current_compile = _current_recovery_compile_files(
        manifest, build, cancel_event
    )
    if current_compile != result.evidence.compile_files:
        raise ValueError("authorized recovery compiled bytes changed")
    compile_sha = compile_manifest_sha256(current_compile)
    if (
        structural.compile_manifest_sha256 != compile_sha
        or final.compile_manifest_sha256 != compile_sha
    ):
        raise ValueError("authorized recovery compile manifest differs")
    if final.whole_index_path != "logs/whole-visual-index.json":
        raise ValueError("authorized recovery whole index path is invalid")
    whole_path = build.workspace / PurePosixPath(final.whole_index_path)
    index = _load_whole_visual_index(
        whole_path, build.workspace, final.whole_index_sha256, cancel_event
    )
    if (
        index["family"]["family_id"] != manifest.family_id
        or index["family"]["input_sha256"] != manifest.input_hash
        or index["candidate"]["candidate_id"] != build.spec.candidate_id
        or index["candidate"]["cache_digest"] != cache_key.digest
        or canonical_json(index["candidate"]["spec"])
        != canonical_json(build.spec.cache_payload())
        or index["dependency"]["digest"] != recipe.dependency_proof_sha256
    ):
        raise ValueError("authorized recovery whole index bindings differ")
    expected_render = hashlib.sha256(canonical_json({
        "whole_index_sha256": final.whole_index_sha256,
        "validation": validation_result_payload(final.validation),
    }).encode("utf-8")).hexdigest()
    if final.whole_render_evidence_sha256 != expected_render:
        raise ValueError("authorized recovery whole render binding differs")
    validated = validate_focused_gate_evidence_payload(
        result.authorization,
        family_id=manifest.family_id,
        candidate_id=build.spec.candidate_id,
        eligible_targets=result.selection.eligible_ranking,
        targets=result.selection.selected,
        regions=evaluation.focused_by_region,
        recovery_context=result.recovery_context,
        initial_records=result.initial_records,
        recoveries=result.recoveries,
    )
    final_records = result.recoveries[-1].rerun_records
    record_by_region = {record.target.region_key: record for record in final_records}
    if set(record_by_region) != set(evaluation.focused_by_region):
        raise ValueError("authorized recovery focused evaluation cardinality differs")
    for region_key, region in evaluation.focused_by_region.items():
        record = record_by_region[region_key]
        if (
            region.target != record.target
            or region.validation != record.validation
            or region.evidence_sha256 != record.evidence_sha256
        ):
            raise ValueError("authorized recovery focused evaluation differs")
    focused_gate = FocusedGateResult(
        _aggregate_visual_results(tuple(
            (record.target.region_key, record.validation)
            for record in final_records
        )),
        tuple(record.target for record in final_records),
        dict(evaluation.focused_by_region), result.evidence.evidence_sha256,
    )
    if evaluation.visual != _aggregate_focused_gate(final.validation, focused_gate):
        raise ValueError("authorized recovery aggregate evaluation differs")
    current_size = _family_snapshot(
        scan_compiled_models(build.compiled_models_dir), manifest.model_rel
    )
    if current_size != evaluation.size:
        raise ValueError("authorized recovery size evaluation differs from current bytes")
    return validated


def _tree_manifest(
    root: Path,
    cancel_event: threading.Event | None = None,
    *,
    chunk_size: int = _IO_CHUNK_SIZE,
) -> dict[str, dict[str, int | str]]:
    root = Path(root).resolve(strict=True)
    result: dict[str, dict[str, int | str]] = {}
    folded: set[str] = set()
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in tuple(directory_names):
            if _is_reparse(parent / name):
                raise ValueError("tree contains a reparse directory")
        for name in file_names:
            path = parent / name
            if _is_reparse(path) or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("tree contains a reparse or special file")
            relative = path.relative_to(root).as_posix()
            if relative.casefold() in folded:
                raise ValueError("tree contains case-colliding paths")
            folded.add(relative.casefold())
            size, digest = _file_proof(path, cancel_event, chunk_size=chunk_size)
            result[relative] = {
                "size": size,
                "sha256": digest,
            }
    return dict(sorted(result.items()))


_TRANSACTION_SCHEMA = 1
_TRANSACTION_PHASES = frozenset({"marker_created", "backup_renamed", "staging_renamed", "verified"})
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


class _InjectedPromotionCrash(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


def _fsync_directory(path: Path) -> None:
    path = Path(path)
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path), 0x40000000, 0x00000007, None, 3, 0x02000000, None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (0, -1, invalid):
        raise OSError(ctypes.get_last_error(), f"cannot open directory for fsync: {path}")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), f"cannot fsync directory: {path}")
    finally:
        kernel32.CloseHandle(handle)


def _transaction_marker_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.maximum-transaction.json"


def _transaction_names(destination: Path, staging: Path) -> tuple[str, str, str]:
    if staging.parent != destination.parent:
        raise MaximumConfigError("promotion staging must be a direct destination sibling")
    prefix = f".{destination.name}.maximum-staging-"
    if not staging.name.startswith(prefix):
        raise MaximumConfigError("promotion staging name is invalid")
    nonce = staging.name[len(prefix):]
    if _NONCE_RE.fullmatch(nonce) is None:
        raise MaximumConfigError("promotion staging nonce is invalid")
    return nonce, staging.name, f".{destination.name}.maximum-backup-{nonce}"


def _canonical_marker_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(
        dict(payload), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _write_marker_exclusive(marker: Path, payload: Mapping[str, object]) -> None:
    with marker.open("xb") as stream:
        stream.write(_canonical_marker_bytes(payload))
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(marker.parent)


def _update_marker(marker: Path, payload: Mapping[str, object]) -> None:
    temporary = marker.with_name(f".{marker.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as stream:
            stream.write(_canonical_marker_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        _fsync_directory(marker.parent)
    finally:
        if temporary.exists() and not _is_reparse(temporary):
            temporary.unlink()


def _validated_transaction_marker(destination: Path) -> tuple[dict[str, object], Path, Path | None]:
    marker = _transaction_marker_path(destination)
    if _is_reparse(marker) or not marker.is_file() or marker.stat().st_size > 65536:
        raise MaximumConfigError("output transaction marker is missing, unsafe, or oversized")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaximumConfigError("output transaction marker is invalid") from exc
    required = {"schema", "destination", "staging", "backup", "nonce", "phase", "had_destination"}
    if type(payload) is not dict or set(payload) != required:
        raise MaximumConfigError("output transaction marker schema is invalid")
    if (
        type(payload["schema"]) is not int or payload["schema"] != _TRANSACTION_SCHEMA
        or type(payload["destination"]) is not str or payload["destination"] != destination.name
        or type(payload["staging"]) is not str
        or type(payload["nonce"]) is not str or _NONCE_RE.fullmatch(payload["nonce"]) is None
        or type(payload["phase"]) is not str or payload["phase"] not in _TRANSACTION_PHASES
        or type(payload["had_destination"]) is not bool
        or (payload["backup"] is not None and type(payload["backup"]) is not str)
    ):
        raise MaximumConfigError("output transaction marker values are invalid")
    nonce, staging_name, backup_name = _transaction_names(
        destination, destination.parent / payload["staging"]
    )
    expected_backup = backup_name if payload["had_destination"] else None
    if nonce != payload["nonce"] or payload["staging"] != staging_name or payload["backup"] != expected_backup:
        raise MaximumConfigError("output transaction marker paths do not match transaction ownership")
    staging = destination.parent / staging_name
    backup = destination.parent / backup_name if expected_backup is not None else None
    for path in (destination, staging, backup, marker):
        if path is None:
            continue
        if path.parent != destination.parent:
            raise MaximumConfigError("output transaction path escapes destination parent")
        if os.path.lexists(path) and _is_reparse(path):
            raise MaximumConfigError("output transaction path contains a reparse point")
    return payload, staging, backup


def _remove_owned_tree(path: Path) -> None:
    if not path.exists():
        return
    if _is_reparse(path) or not path.is_dir():
        raise MaximumConfigError("owned transaction artifact is unsafe")
    _tree_manifest(path)
    shutil.rmtree(path)
    _fsync_directory(path.parent)


def _recover_output_transaction(destination: Path) -> bool:
    destination = Path(os.path.abspath(os.fspath(destination)))
    parent = destination.parent
    if _is_reparse(parent) or not parent.is_dir():
        raise MaximumConfigError("output transaction parent is unsafe")
    marker = _transaction_marker_path(destination)
    backups = tuple(sorted(parent.glob(f".{destination.name}.maximum-backup-*")))
    stagings = tuple(sorted(parent.glob(f".{destination.name}.maximum-staging-*")))
    if not os.path.lexists(marker):
        if backups or stagings:
            raise MaximumConfigError("legacy output transaction orphan exists without a valid marker")
        return False
    payload, staging, backup = _validated_transaction_marker(destination)
    owned = {staging}
    if backup is not None:
        owned.add(backup)
    extras = (set(backups) | set(stagings)) - owned
    if extras:
        raise MaximumConfigError("ambiguous output transaction artifacts")
    if destination.exists():
        if _is_reparse(destination) or not destination.is_dir():
            raise MaximumConfigError("existing output transaction destination is unsafe")
        _tree_manifest(destination)
        if backup is not None:
            _remove_owned_tree(backup)
        _remove_owned_tree(staging)
    else:
        if payload["had_destination"]:
            if backup is None or not backup.is_dir() or _is_reparse(backup):
                raise MaximumConfigError("marker-owned backup is unavailable for recovery")
            _tree_manifest(backup)
            os.replace(backup, destination)
            _fsync_directory(parent)
        _remove_owned_tree(staging)
    marker.unlink()
    _fsync_directory(parent)
    return True


def _promote_verified_tree(
    staging: Path,
    destination: Path,
    expected: Mapping[str, Mapping[str, int | str]],
    *,
    manifest_reader: Callable[[Path], dict[str, dict[str, int | str]]] = _tree_manifest,
    cancel_event: threading.Event | None = None,
    crash_hook: Callable[[str], None] | None = None,
) -> None:
    staging_manifest = (
        _tree_manifest(staging, cancel_event)
        if manifest_reader is _tree_manifest
        else manifest_reader(staging)
    )
    if staging_manifest != dict(expected):
        raise ValueError("staging manifest does not match expected output manifest")
    # This is the final cancellation barrier. From the first os.replace onward
    # the transaction is deliberately non-interruptible and commits or rolls back.
    _check_cancelled(cancel_event, "cancelled before atomic output promotion")
    nonce, staging_name, backup_name = _transaction_names(destination, staging)
    marker = _transaction_marker_path(destination)
    had_destination = destination.exists()
    backup = destination.parent / backup_name if had_destination else None
    payload: dict[str, object] = {
        "schema": _TRANSACTION_SCHEMA,
        "destination": destination.name,
        "staging": staging_name,
        "backup": backup_name if had_destination else None,
        "nonce": nonce,
        "phase": "marker_created",
        "had_destination": had_destination,
    }
    _write_marker_exclusive(marker, payload)

    def inject(phase: str) -> None:
        if crash_hook is None:
            return
        try:
            crash_hook(phase)
        except BaseException as exc:
            raise _InjectedPromotionCrash(exc) from exc

    try:
        inject("marker_created")
    except _InjectedPromotionCrash as injected:
        raise injected.original

    if backup is not None:
        os.replace(destination, backup)
        _fsync_directory(destination.parent)
        payload["phase"] = "backup_renamed"
        _update_marker(marker, payload)
        try:
            inject("backup_renamed")
        except _InjectedPromotionCrash as injected:
            raise injected.original
    try:
        os.replace(staging, destination)
        _fsync_directory(destination.parent)
        payload["phase"] = "staging_renamed"
        _update_marker(marker, payload)
        inject("staging_renamed")
        if manifest_reader(destination) != dict(expected):
            raise ValueError("final manifest does not match expected output manifest")
        payload["phase"] = "verified"
        _update_marker(marker, payload)
        inject("verified")
    except _InjectedPromotionCrash as injected:
        raise injected.original
    except BaseException as promotion_error:
        restore_error: BaseException | None = None
        try:
            if destination.exists() and not _is_reparse(destination):
                shutil.rmtree(destination)
            if backup is not None and backup.exists():
                os.replace(backup, destination)
            if marker.exists() and not _is_reparse(marker):
                marker.unlink()
            _fsync_directory(destination.parent)
        except BaseException as exc:
            restore_error = exc
        if restore_error is not None:
            raise AtomicReplaceError(
                destination,
                backup or destination,
                promotion_error,
                restore_error,
            ) from restore_error
        raise
    if backup is not None:
        _remove_owned_tree(backup)
    marker.unlink()
    _fsync_directory(destination.parent)


def _expected_output_manifest(
    addon_dir: Path,
    selected: Sequence[tuple[FamilyRunOutcome, CandidateBuild, FamilyManifest]],
    cancel_event: threading.Event | None = None,
) -> dict[str, dict[str, int | str]]:
    expected = {
        logical: proof
        for logical, proof in _tree_manifest(addon_dir, cancel_event).items()
        if not (
            logical.casefold().startswith("models/")
            and logical.casefold().endswith(".dx80.vtx")
        )
    }
    for outcome, build, manifest in selected:
        family_original = [
            logical for logical in expected
            if logical.casefold().startswith("models/")
            and _is_exact_family_artifact(logical[7:], outcome.model_rel)
        ]
        if not family_original:
            raise ValueError(f"original family artifacts are missing: {outcome.model_rel}")
        for logical in family_original:
            del expected[logical]
        if not build.provenance:
            raise ValueError("candidate provenance must be nonempty")
        base = PurePosixPath(outcome.model_rel.replace("\\", "/")).with_suffix("").as_posix()
        required = {
            base + (kind if str(kind).startswith(".") else "." + str(kind))
            for kind in manifest.required_artifact_kinds
            if str(kind).casefold() != ".dx80.vtx"
        }
        missing_required = sorted(
            logical for logical in required
            if logical.casefold() not in {key.casefold() for key in build.provenance}
        )
        if missing_required:
            raise ValueError(f"candidate provenance is missing required artifacts: {missing_required}")
        for logical, provenance in sorted(build.provenance.items()):
            if provenance != "candidate-compile" or not _is_exact_family_artifact(logical, outcome.model_rel):
                raise ValueError(f"candidate provenance contains invalid family artifact: {logical}")
            if logical.casefold().endswith(".dx80.vtx"):
                continue
            relative = PurePosixPath(logical.replace("\\", "/"))
            source = build.compiled_models_dir.joinpath(*relative.parts)
            if _is_reparse(source) or not source.is_file():
                raise ValueError(f"candidate family artifact is missing: {logical}")
            size, digest = _file_proof(source, cancel_event)
            expected[f"models/{relative.as_posix()}"] = {
                "size": size,
                "sha256": digest,
            }
    return dict(sorted(expected.items()))


def _reconcile_inventory(
    original: CompiledSizeSnapshot,
    manifests: Sequence[FamilyManifest],
) -> tuple[tuple[FamilyManifest, ...], tuple[str, ...]]:
    original_models = tuple(
        item.relative_path for item in original.artifacts if item.kind == ".mdl"
    )
    if not original_models:
        raise MaximumConfigError("original models tree contains no MDL families")
    by_folded: dict[str, str] = {}
    for model_rel in original_models:
        folded = model_rel.casefold()
        if folded in by_folded:
            raise MaximumConfigError("original models contain case-colliding MDL paths")
        by_folded[folded] = model_rel
    seen: set[str] = set()
    for manifest in manifests:
        if re.fullmatch(r"[0-9a-f]{64}", manifest.family_id) is None:
            raise MaximumConfigError("inventory family_id must be a lowercase SHA-256 digest")
        if re.fullmatch(r"[0-9a-f]{64}", manifest.input_hash) is None:
            raise MaximumConfigError("inventory input_hash must be a lowercase SHA-256 digest")
        folded = manifest.model_rel.replace("\\", "/").casefold()
        if folded not in by_folded:
            raise MaximumConfigError(f"inventory family has no original MDL: {manifest.model_rel}")
        if folded in seen:
            raise MaximumConfigError(f"inventory contains duplicate family: {manifest.model_rel}")
        seen.add(folded)
    for artifact in original.artifacts:
        owners = [
            model_rel for model_rel in original_models
            if _is_exact_family_artifact(artifact.relative_path, model_rel)
        ]
        if len(owners) != 1:
            raise MaximumConfigError(
                f"original compiled artifact has ambiguous or missing family owner: {artifact.relative_path}"
            )
    missing = tuple(by_folded[key] for key in sorted(set(by_folded) - seen))
    return tuple(manifests), missing


def _worst(attempts: Sequence[AttemptReport]) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    scopes: dict[str, str] = {}
    for attempt in attempts:
        for result in (attempt.structural, attempt.visual):
            if result is None:
                continue
            result_scopes: dict[str, str] = {}
            for failure in result.failures:
                measured = failure.measured
                if (
                    failure.gate in result.metrics
                    and not isinstance(measured, bool)
                    and type(measured) in (int, float)
                    and float(measured) == float(result.metrics[failure.gate])
                ):
                    result_scopes[failure.gate] = failure.scope
            for metric, value in result.metrics.items():
                if metric not in metrics or value > metrics[metric]:
                    metrics[metric] = value
                    if metric in result_scopes:
                        scopes[metric] = result_scopes[metric]
                    else:
                        scopes.pop(metric, None)
    return metrics, scopes


def _family_work_estimate(
    manifest: FamilyManifest,
    original: CompiledSizeSnapshot,
) -> int:
    source_bytes = 0
    try:
        for path in manifest.source_dir.rglob("*"):
            if (
                path.suffix.casefold() in {".smd", ".dmx", ".qc"}
                and path.is_file()
                and not _is_reparse(path)
            ):
                source_bytes += path.stat().st_size
    except OSError:
        source_bytes = 0
    visual_states = 1
    try:
        original_qcs = _matching_qcs(
            manifest.source_dir, manifest.model_rel, optimized=False
        )
        if len(original_qcs) == 1:
            visual_states = max(1, len(_graph_visual_configurations(
                parse_qc_graph(original_qcs[0], manifest.source_dir)
            )))
    except (OSError, UnicodeError, ValueError):
        visual_states = 1
    original_bytes = _family_snapshot(original, manifest.model_rel).total_bytes
    return max(1, source_bytes) * visual_states + original_bytes


@dataclass(frozen=True)
class _FamilyExecutionResult:
    index: int
    outcome: FamilyRunOutcome
    control_snapshot: CompiledSizeSnapshot | None
    selected_snapshot: CompiledSizeSnapshot
    selected_build: CandidateBuild | None
    recovery_authorization: tuple[FocusedRecoveryAdapterResult, object, CacheKey, Path] | None
    selection_record: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("family execution index is invalid")
        if not isinstance(self.outcome, FamilyRunOutcome):
            raise TypeError("family execution outcome is invalid")
        if self.control_snapshot is not None and not isinstance(
            self.control_snapshot, CompiledSizeSnapshot
        ):
            raise TypeError("family control snapshot is invalid")
        if not isinstance(self.selected_snapshot, CompiledSizeSnapshot):
            raise TypeError("family selected snapshot is invalid")
        if self.selected_snapshot != self.outcome.selected_size:
            raise ValueError("family result differs from outcome selection")
        if self.selected_build is not None and not isinstance(
            self.selected_build, CandidateBuild
        ):
            raise TypeError("family selected build is invalid")
        object.__setattr__(
            self, "selection_record", deep_freeze(dict(self.selection_record))
        )


def _execute_family(
    *,
    index: int,
    manifest: FamilyManifest,
    config: MaximumRunConfig,
    original: CompiledSizeSnapshot,
    profile_set: FidelityProfileSet,
    adapter_set: AdapterSet,
    structural_validator: StructuralValidator,
    cache: CandidateCache,
    versions: Mapping[str, str],
    dependency: Mapping[str, object],
    tools: CandidateTools,
    cancel: threading.Event,
    emit: Callable[..., None],
    profile_selector: Callable[[FamilyManifest], FamilyFidelitySelection] | None,
    total_family_count: int,
) -> _FamilyExecutionResult:
    family_index = index
    outcomes: list[FamilyRunOutcome] = []
    control_snapshots: list[CompiledSizeSnapshot] = []
    selected_snapshots: list[CompiledSizeSnapshot] = []
    selected_builds: dict[str, CandidateBuild] = {}
    selected_recovery_authorizations: dict[
        str, tuple[FocusedRecoveryAdapterResult, object, CacheKey, Path]
    ] = {}
    selection_records: list[dict[str, Any]] = []
    terminal_result: _FamilyExecutionResult | None = None

    def finish_result() -> _FamilyExecutionResult:
        if len(outcomes) != 1 or len(selected_snapshots) != 1:
            raise RuntimeError("family execution did not produce one terminal outcome")
        if len(control_snapshots) > 1 or len(selection_records) != 1:
            raise RuntimeError("family execution produced ambiguous state")
        outcome = outcomes[0]
        optimized = outcome.status == "optimized"
        return _FamilyExecutionResult(
            index=family_index,
            outcome=outcome,
            control_snapshot=(control_snapshots[0] if control_snapshots else None),
            selected_snapshot=selected_snapshots[0],
            selected_build=(
                selected_builds.get(manifest.family_id) if optimized else None
            ),
            recovery_authorization=(
                selected_recovery_authorizations.get(manifest.family_id)
                if optimized else None
            ),
            selection_record=selection_records[0],
        )

    coordinator_emit = emit

    def emit(kind: str, **payload: Any) -> None:
        nonlocal terminal_result
        if kind == "family_finished" and terminal_result is None:
            terminal_result = finish_result()
        coordinator_emit(kind, **payload)

    def terminal_result_value() -> _FamilyExecutionResult:
        if terminal_result is None:
            raise RuntimeError("family execution returned before its terminal event")
        return terminal_result

    original_family = _family_snapshot(original, manifest.model_rel)
    original_without_dx80 = _without_dx80(original_family)
    attempts: list[AttemptReport] = []
    emit("family_started", family=manifest.model_rel, index=family_index, total=total_family_count)
    try:
        if profile_set.mode == LEGACY_GLOBAL:
            profile_class = LEGACY_GLOBAL
            profile = profile_set.profile_for(GENERAL_BODY_DETAIL)
            reason = "legacy-global-profile"
            sources: list[dict[str, Any]] = []
        else:
            if profile_selector is not None:
                selection = profile_selector(manifest)
            else:
                original_qcs = _matching_qcs(
                    manifest.source_dir, manifest.model_rel, optimized=False
                )
                if len(original_qcs) != 1:
                    raise ValueError(
                        "typed fidelity selection requires exactly one original QC"
                    )
                selection = classify_original_family(
                    parse_qc_graph(original_qcs[0], manifest.source_dir)
                )
            if not isinstance(selection, FamilyFidelitySelection):
                raise TypeError("profile selector returned an invalid selection")
            profile_class = selection.profile_class
            profile = profile_set.profile_for(profile_class)
            reason = selection.reason
            sources = [asdict(source) for source in selection.sources]
        focused_policy = profile_set.focused_policy
        focused_profile = (
            profile_set.focused_profile_for(profile_class)
            if focused_policy is not None
            else None
        )
        selection_records.append({
            "family_id": manifest.family_id,
            "model_rel": manifest.model_rel,
            "status": "selected",
            "profile_class": profile_class,
            "reason": reason,
            "version": profile.version,
            "corpus_hash": profile.corpus_hash,
            "sources": sources,
        })
    except Exception as exc:
        selection_records.append({
            "family_id": manifest.family_id,
            "model_rel": manifest.model_rel,
            "status": "failed",
            "profile_class": None,
            "reason": f"classification-error:{type(exc).__name__}",
            "version": None,
            "corpus_hash": profile_set.corpus_hash,
            "sources": [],
        })
        outcome = FamilyRunOutcome(
            manifest.family_id, manifest.model_rel, "failed", None,
            original_family, None, original_without_dx80,
            compare_snapshots(original_family, original_family, original_without_dx80),
            tuple(attempts),
            f"fidelity profile selection failed: {exc}", {}, {},
            {"source": "original-preserved"},
        )
        outcomes.append(outcome)
        selected_snapshots.append(original_without_dx80)
        emit(
            "family_finished",
            family=manifest.model_rel,
            status="failed",
            reason=outcome.reason,
        )
        return terminal_result_value()
    control_spec = CandidateSpec(
        "roundtrip-control",
        "blender",
        1.0,
        0.0,
        "blender-adaptive-v1",
        strategy="blender-adaptive-v1",
        update_vertices=True,
        transfer="blender-native-v1",
    )
    control_build: CandidateBuild | None = None
    control_size: CompiledSizeSnapshot | None = None
    control_valid = False

    if cancel.is_set():
        outcome = FamilyRunOutcome(
            manifest.family_id, manifest.model_rel, "cancelled", None,
            original_family, None, original_family, {}, tuple(attempts),
            "run cancelled before control compile", {}, {}, {"source": "original-preserved"},
        )
        outcomes.append(outcome)
        selected_snapshots.append(original_family)
        emit("family_finished", family=manifest.model_rel, status="cancelled")
        return terminal_result_value()

    emit("candidate_started", family=manifest.model_rel, candidate=control_spec.candidate_id, engine="blender")
    emit("stage", family=manifest.model_rel, candidate=control_spec.candidate_id, stage="generate_compile")
    try:
        control_workspace = config.work_dir / "families" / manifest.family_id / control_spec.candidate_id
        if control_workspace.exists():
            shutil.rmtree(control_workspace)
        control_build = adapter_set.build(manifest, control_spec, control_workspace, tools, cancel)
        if cancel.is_set():
            raise ProcessCancelledError("cancelled after control compile")
        control_size = _family_snapshot(scan_compiled_models(control_build.compiled_models_dir), manifest.model_rel)
        structural = structural_validator(manifest, control_build)
        emit("stage", family=manifest.model_rel, candidate=control_spec.candidate_id, stage="structural")
        control_visual = (
            adapter_set.visual(manifest, control_build, control_build, profile)
            if structural.passed
            else ValidationResult(False, worst_scope=structural.worst_scope)
        )
        emit("stage", family=manifest.model_rel, candidate=control_spec.candidate_id, stage="visual")
        attempts.append(AttemptReport(
            control_spec.candidate_id,
            "blender",
            "control" if structural.passed and control_visual.passed else "control_rejected",
            control_size,
            structural,
            control_visual,
            False,
            "",
            control_build.provenance,
        ))
        control_snapshots.append(control_size)
        control_valid = structural.passed and control_visual.passed
        emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="control" if control_valid else "control_rejected", compiled_bytes=control_size.total_bytes)
    except ProcessCancelledError as exc:
        cancel.set()
        attempts.append(AttemptReport(control_spec.candidate_id, "blender", "cancelled", None, None, None, False, str(exc), {}))
        emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="cancelled")
    except Exception as exc:
        attempts.append(AttemptReport(control_spec.candidate_id, "blender", "compile_failed", None, None, None, False, str(exc), {}))
        emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="compile_failed", stage=getattr(exc, "stage", "orchestrator"))

    if control_build is None or control_size is None or not control_valid:
        status = "cancelled" if cancel.is_set() else ("failed" if control_build is None else "preserved")
        reason = (
            "run cancelled during control compile"
            if cancel.is_set()
            else (
                "mandatory control compile failed"
                if control_build is None
                else "control roundtrip failed structural or visual compatibility gates"
            )
        )
        retained = original_family if cancel.is_set() else original_without_dx80
        control_savings = compare_snapshots(
            original_family,
            control_size if control_size is not None else original_family,
            retained,
        )
        outcome = FamilyRunOutcome(
            manifest.family_id, manifest.model_rel, status, None,
            original_family, control_size, retained, control_savings, tuple(attempts), reason,
            *_worst(attempts), {"source": "original-preserved"},
        )
        outcomes.append(outcome)
        selected_snapshots.append(retained)
        emit("family_finished", family=manifest.model_rel, status=status, reason=reason)
        return terminal_result_value()

    schedule_method = getattr(adapter_set, "candidate_schedule", None)
    schedule = tuple(schedule_method(manifest)) if callable(schedule_method) else _default_schedule()
    if (
        not schedule
        or len({item.candidate_id for item in schedule}) != len(schedule)
        or any(
            not isinstance(item, CandidateSpec)
            or type(item.engine) is not str
            or item.engine not in _ENGINE_NAMES
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item.candidate_id) is None
            or not math.isfinite(item.target_ratio)
            or not 0 < item.target_ratio <= 1
            for item in schedule
        )
    ):
        reason = "candidate schedule is empty, duplicated, or invalid"
        outcome = FamilyRunOutcome(
            manifest.family_id,
            manifest.model_rel,
            "failed",
            None,
            original_family,
            control_size,
            original_without_dx80,
            compare_snapshots(original_family, control_size, original_without_dx80),
            tuple(attempts),
            reason,
            *_worst(attempts),
            {
                item.relative_path: "original-preserved"
                for item in original_without_dx80.artifacts
            },
        )
        outcomes.append(outcome)
        selected_snapshots.append(original_without_dx80)
        emit(
            "family_finished",
            family=manifest.model_rel,
            status="failed",
            reason=reason,
            best_bytes=original_without_dx80.total_bytes,
            reduction_percent=(
                (original_family.total_bytes - original_without_dx80.total_bytes)
                / original_family.total_bytes * 100
                if original_family.total_bytes else 0.0
            ),
        )
        return terminal_result_value()
    evaluations: list[CandidateEvaluation] = []
    candidate_builds: dict[str, CandidateBuild] = {}
    recovery_authorizations: dict[
        str, tuple[FocusedRecoveryAdapterResult, object, CacheKey, Path]
    ] = {}
    retained_candidates: dict[
        str, tuple[CandidateBuild, CandidateEvaluation, RecoverySourceSnapshot]
    ] = {}
    attempted_ids: set[str] = set()
    last_best: str | None = None
    recovery_base: tuple[CandidateBuild, CandidateEvaluation, RecoverySourceSnapshot] | None = None
    while not cancel.is_set():
        if (
            recovery_base is not None and focused_policy is not None
            and len(attempted_ids) >= max(
                0, config.budget.max_candidates - focused_policy.max_recovery_rounds
            )
        ):
            break
        spec = choose_next(
            evaluations,
            config.budget,
            initial=schedule,
            attempted_ids=attempted_ids,
            recovery_mode=(
                "external-byte-exact"
                if focused_policy is not None
                else "legacy-regional"
            ),
        )
        if spec is None:
            break
        attempted_ids.add(spec.candidate_id)
        emit("candidate_started", family=manifest.model_rel, candidate=spec.candidate_id, engine=spec.engine)
        emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="generate_compile")
        workspace = config.work_dir / "families" / manifest.family_id / spec.candidate_id
        key = CacheKey.build(manifest.input_hash, spec.cache_payload(), versions, profile.version)
        cache_hit = False
        try:
            cache_entry = cache.lookup(key) if config.resume else None
            if cache_entry is not None and not _verify_cache_entry(cache_entry, cancel):
                cache.invalidate(key)
                cache_entry = None
            if cache_entry is not None:
                try:
                    _restore_cache_payload(cache_entry, workspace, cancel)
                    build = _load_cached_build(
                        cache_entry,
                        spec,
                        key=key,
                        manifest=manifest,
                        dependency_digest=str(dependency["digest"]),
                        materialized_workspace=workspace,
                        cancel_event=cancel,
                        optimizer_contract_digest=(
                            optimizer_contract_sha256(spec)
                            if focused_policy is not None else None
                        ),
                        whole_profile_digest=(
                            _profile_contract_sha256(
                                profile, versions["profile_sha256"]
                            ) if focused_policy is not None else None
                        ),
                        focused_profile_digest=(
                            _profile_contract_sha256(
                                focused_profile, versions["profile_sha256"]
                            ) if focused_profile is not None else None
                        ),
                    )
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    cache.invalidate(key)
                    cache_entry = None
                else:
                    cache_hit = True
                    emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="cache_hit")
                    if cancel.is_set():
                        raise ProcessCancelledError("cancelled after cache restore")
            if cache_entry is None:
                if workspace.exists():
                    shutil.rmtree(workspace)
                build = adapter_set.build(manifest, spec, workspace, tools, cancel)
                if cancel.is_set():
                    raise ProcessCancelledError("cancelled after candidate compile")
            emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="compiled_size")
            # Cached diagnostics never authorize a candidate: both hard gates
            # are evaluated from the sealed artifacts and current materials.
            structural = structural_validator(manifest, build)
            emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="structural")
            if cancel.is_set():
                raise ProcessCancelledError("cancelled after structural validation")
            bind_cache_digest = getattr(adapter_set, "bind_candidate_cache_digest", None)
            if focused_policy is not None and callable(bind_cache_digest):
                bind_cache_digest(build, key.digest)
            whole_visual = (
                adapter_set.visual(
                    manifest, control_build, build, profile,
                    focused_profile=focused_profile,
                )
                if structural.passed and focused_policy is not None
                else (
                    adapter_set.visual(manifest, control_build, build, profile)
                    if structural.passed
                    else ValidationResult(False, worst_scope=structural.worst_scope)
                )
            )
            emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="visual")
            if cancel.is_set():
                raise ProcessCancelledError("cancelled after visual validation")
            focused_by_region: Mapping[str, FocusRegionResult] = {}
            visual = whole_visual
            if focused_policy is not None and structural.passed and whole_visual.passed:
                focused_method = getattr(adapter_set, "focused_visual", None)
                if not callable(focused_method):
                    raise TypeError("schema-3 adapter does not implement focused_visual")
                focused_gate = focused_method(
                    manifest, control_build, build, profile,
                    focused_profile, focused_policy,
                )
                if cancel.is_set():
                    raise ProcessCancelledError("cancelled after focused visual validation")
                visual = _aggregate_focused_gate(whole_visual, focused_gate)
                focused_by_region = focused_gate.regions
                emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="focused_visual")
            if focused_policy is not None:
                if focused_profile is None:
                    raise TypeError("schema-3 focused profile is unavailable")
                build = _attach_recovery_source_snapshot(
                    build, manifest, key=key, whole_profile=profile,
                    focused_profile=focused_profile,
                    profile_file_sha256=versions["profile_sha256"],
                    dependency_digest=str(dependency["digest"]),
                    focused_by_region=focused_by_region,
                    cancel_event=cancel,
                )
            if not cache_hit and (focused_policy is None or visual.passed):
                _check_cancelled(cancel, "cancelled before candidate cache record")
                _store_cache_record(
                    workspace,
                    build,
                    structural,
                    visual,
                    key=key,
                    manifest=manifest,
                    dependency_digest=str(dependency["digest"]),
                )
                _check_cancelled(cancel, "cancelled before candidate cache store")
                stored = cache.store(
                    key,
                    workspace,
                    {"candidate": spec.candidate_id, "profile": profile.version},
                    copy_function=lambda source, destination: _copy_file_cancellable(
                        source, destination, cancel
                    ),
                )
                _seal_cache_entry(stored, cancel)
            size = _family_snapshot(scan_compiled_models(build.compiled_models_dir), manifest.model_rel)
            evaluation = CandidateEvaluation(
                spec, size, structural, visual, build.compiled_models_dir,
                whole_visual, focused_by_region,
            )
            evaluations.append(evaluation)
            candidate_builds[spec.candidate_id] = build
            if focused_policy is not None:
                if build.source_snapshot is None:
                    raise ValueError("schema-3 completed build has no recovery snapshot")
                if len(retained_candidates) >= config.budget.max_candidates:
                    raise ValueError("schema-3 retained candidate registry exceeds budget")
                retained_candidates[spec.candidate_id] = (
                    build, evaluation, build.source_snapshot,
                )
                if (
                    structural.passed and whole_visual.passed
                    and focused_by_region and not visual.passed
                ):
                    proposed_base = retained_candidates[spec.candidate_id]
                    if recovery_base is None or (
                        evaluation.size.total_bytes, spec.candidate_id
                    ) < (
                        recovery_base[1].size.total_bytes,
                        recovery_base[1].spec.candidate_id,
                    ):
                        recovery_base = proposed_base
            status = "passed" if evaluation.passed else "rejected"
            attempts.append(AttemptReport(
                spec.candidate_id, spec.engine, status, size, structural, visual,
                cache_hit, "", build.provenance,
            ))
            emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status=status, compiled_bytes=size.total_bytes, cache_hit=cache_hit)
            best = select_winner(evaluations)
            if best is not None and best.spec.candidate_id != last_best:
                last_best = best.spec.candidate_id
                selected_builds[manifest.family_id] = candidate_builds[
                    best.spec.candidate_id
                ]
                emit("best_updated", family=manifest.model_rel, candidate=best.spec.candidate_id, compiled_bytes=best.size.total_bytes)
        except ProcessCancelledError as exc:
            cancel.set()
            attempts.append(AttemptReport(spec.candidate_id, spec.engine, "cancelled", None, None, None, cache_hit, str(exc), {}))
            emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status="cancelled")
        except Exception as exc:
            attempts.append(AttemptReport(spec.candidate_id, spec.engine, "compile_failed", None, None, None, cache_hit, str(exc), {}))
            emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status="compile_failed", stage=getattr(exc, "stage", "orchestrator"))

    recover_method = getattr(adapter_set, "recover_focused_candidate", None)
    recovery_error_reason: str | None = None
    if (
        focused_policy is not None and recovery_base is not None
        and not callable(recover_method)
    ):
        recovery_error_reason = (
            "schema-3 adapter does not implement byte-exact focused recovery"
        )
        attempts.append(AttemptReport(
            recovery_base[0].spec.candidate_id,
            recovery_base[0].spec.engine,
            "compile_failed", None, None, None, False,
            recovery_error_reason, {},
        ))
    if (
        focused_policy is not None
        and focused_profile is not None
        and recovery_base is not None
        and callable(recover_method)
        and not cancel.is_set()
    ):
        base_build, base_evaluation, base_snapshot = recovery_base
        selected_targets = tuple(sorted(
            (item.target for item in base_evaluation.focused_by_region.values()),
            key=lambda item: item.rank,
        ))
        if selected_targets and tuple(item.rank for item in selected_targets) == tuple(range(len(selected_targets))):
            focus_selection = FocusSelection(
                selected_targets[0].selector_input_sha256,
                selected_targets,
                selected_targets,
            )
            snapshots_by_candidate = {
                candidate_id: entry[2]
                for candidate_id, entry in retained_candidates.items()
            }
            original_snapshot: RecoverySourceSnapshot | None = None
            current_recipe = None
            failed_for_selection = base_evaluation
            attempted_overlay_hashes: set[str] = set()
            recovery_records: list[FocusedRecoveryEvidence] = []
            for round_index in range(focused_policy.max_recovery_rounds):
                # Availability is checked before original/donor snapshot I/O.
                if len(attempted_ids) >= config.budget.max_candidates:
                    break
                recovery_evidence_path: Path | None = None
                recovery_cache_owned = False
                recovery_cache_sealed = False
                workspace: Path | None = None
                recovery_key: CacheKey | None = None
                try:
                    if original_snapshot is None:
                        original_snapshot = _build_original_recovery_snapshot(
                            manifest, base_snapshot, cancel
                        )
                    overlays = select_recovery_overlays(
                        failed_for_selection, focus_selection, evaluations,
                        snapshots_by_candidate, original_snapshot, current_recipe,
                        attempted_overlay_hashes, round_index,
                    )
                    if not overlays:
                        break
                    recipe = focused_recovery_recipe(
                        base_build.spec, base_snapshot, overlays,
                        round_index=round_index,
                        selector_version=focused_policy.selector,
                    )
                    recovery_spec = recovery_candidate_spec(base_build.spec, recipe)
                    if recovery_spec.candidate_id in attempted_ids:
                        raise ValueError("focused recovery repeated a candidate identity")
                    recovery_key = CacheKey.build(
                        manifest.input_hash, recovery_spec.cache_payload(), versions,
                        profile.version,
                    )
                except ProcessCancelledError:
                    cancel.set()
                    break
                except Exception as exc:
                    attempts.append(AttemptReport(
                        f"recovery-plan-{round_index}", base_build.spec.engine,
                        "compile_failed", None, None, None, False, str(exc), {},
                    ))
                    break
                # Reserve both shared counters before reopening a snapshot,
                # mutating a workspace, or launching a process.
                attempted_ids.add(recovery_spec.candidate_id)
                previous = {
                    item.source_identity: item
                    for item in (() if current_recipe is None else current_recipe.overlays)
                }
                for overlay in overlays:
                    if previous.get(overlay.source_identity) != overlay:
                        attempted_overlay_hashes.add(hashlib.sha256(
                            canonical_json(source_overlay_payload(overlay)).encode("utf-8")
                        ).hexdigest())
                current_recipe = recipe
                workspace = (
                    config.work_dir / "families" / manifest.family_id
                    / recovery_spec.candidate_id
                )
                emit(
                    "candidate_started", family=manifest.model_rel,
                    candidate=recovery_spec.candidate_id, engine=recovery_spec.engine,
                )
                try:
                    result = recover_method(
                        manifest=manifest,
                        control_build=control_build,
                        base_build=base_build,
                        base_evaluation=base_evaluation,
                        recipe=recipe,
                        spec=recovery_spec,
                        cache_key=recovery_key,
                        snapshots_by_sha256={
                            snapshot.snapshot_sha256: snapshot
                            for snapshot in (*snapshots_by_candidate.values(), original_snapshot)
                        },
                        workspace=workspace,
                        whole_profile=profile,
                        focused_profile=focused_profile,
                        policy=focused_policy,
                        structural_validator=structural_validator,
                        tools=tools,
                        prior_recoveries=tuple(recovery_records),
                        cancel_event=cancel,
                    )
                    _check_cancelled(
                        cancel, "cancelled after focused recovery adapter"
                    )
                    if not isinstance(result, FocusedRecoveryAdapterResult):
                        raise TypeError("focused recovery adapter returned an invalid result")
                    if result.evidence.round_index != round_index or result.evidence.recipe != recipe:
                        raise ValueError("focused recovery adapter evidence differs from reserved recipe")
                    recovery_records.append(result.evidence)
                    if result.evidence.terminal_status != "authorized":
                        if result.evaluation is not None:
                            failed_for_selection = result.evaluation
                        attempts.append(AttemptReport(
                            recovery_spec.candidate_id, recovery_spec.engine,
                            "rejected", None, None, None, False,
                            result.evidence.terminal_status,
                            {"recovery_evidence_sha256": result.evidence.evidence_sha256},
                        ))
                        emit(
                            "candidate_finished", family=manifest.model_rel,
                            candidate=recovery_spec.candidate_id, status="rejected",
                            stage=result.evidence.terminal_status,
                        )
                        continue
                    build = result.build
                    evaluation = result.evaluation
                    if (
                        build.spec != recovery_spec
                        or build.source_snapshot.candidate_cache_digest != recovery_key.digest
                        or evaluation.spec != recovery_spec
                        or result.authorization["candidate_id"] != recovery_spec.candidate_id
                    ):
                        raise ValueError("authorized focused recovery identity mismatch")
                    validated_authorization = _validate_authorized_recovery_boundary(
                        result=result, manifest=manifest, recipe=recipe,
                        cache_key=recovery_key, cancel_event=cancel,
                    )
                    _check_cancelled(
                        cancel, "cancelled before focused recovery evidence publication"
                    )
                    recovery_evidence_path = (
                        build.workspace / "logs/focused-region-gate.json"
                    )
                    _safe_workspace_atomic_json(
                        build.workspace,
                        recovery_evidence_path,
                        validated_authorization,
                        "focused recovery authoritative evidence",
                    )
                    _check_cancelled(
                        cancel, "cancelled before focused recovery cache record"
                    )
                    _store_cache_record(
                        build.workspace, build, evaluation.structural, evaluation.visual,
                        key=recovery_key, manifest=manifest,
                        dependency_digest=str(dependency["digest"]),
                    )
                    _check_cancelled(
                        cancel, "cancelled before focused recovery cache store"
                    )
                    validated_authorization = _validate_authorized_recovery_boundary(
                        result=result, manifest=manifest, recipe=recipe,
                        cache_key=recovery_key, cancel_event=cancel,
                    )
                    _safe_workspace_atomic_json(
                        build.workspace, recovery_evidence_path,
                        validated_authorization,
                        "focused recovery authoritative evidence",
                    )
                    cache_state: dict[str, object] = {}

                    def validate_private_entry(
                        entry: Path, *, publish_integrity: bool,
                    ) -> None:
                        cached_workspace = entry / "payload"
                        cached_build = _load_cached_build(
                            entry, recovery_spec, key=recovery_key,
                            manifest=manifest,
                            dependency_digest=str(dependency["digest"]),
                            materialized_workspace=cached_workspace,
                            cancel_event=cancel,
                            optimizer_contract_digest=recipe.optimizer_contract_sha256,
                            whole_profile_digest=recipe.whole_profile_sha256,
                            focused_profile_digest=recipe.focused_profile_sha256,
                        )
                        cached_authorization = json.loads(
                            _read_regular_no_follow(
                                cached_workspace / "logs/focused-region-gate.json",
                                cancel, contained_root=cached_workspace,
                            ).decode("utf-8")
                        )
                        cached_evaluation = replace(
                            evaluation,
                            size=_family_snapshot(
                                scan_compiled_models(cached_build.compiled_models_dir),
                                manifest.model_rel,
                            ),
                            compiled_models_dir=cached_build.compiled_models_dir,
                        )
                        cached_result = replace(
                            result, build=cached_build,
                            evaluation=cached_evaluation,
                            authorization=cached_authorization,
                        )
                        def authorize_current_entry() -> None:
                            _validate_authorized_recovery_boundary(
                                result=cached_result, manifest=manifest,
                                recipe=recipe, cache_key=recovery_key,
                                cancel_event=cancel,
                            )

                        if publish_integrity:
                            _seal_then_validate_private_recovery_entry(
                                entry, authorize_current_entry
                            )
                        else:
                            authorize_current_entry()
                            if not _verify_cache_entry(entry, None):
                                raise ValueError(
                                    "focused recovery cache integrity is invalid"
                                )
                        cache_state.update({
                            "result": cached_result, "build": cached_build,
                            "evaluation": cached_evaluation,
                        })

                    stored, recovery_cache_owned = cache.store_validated(
                        recovery_key, build.workspace,
                        {"candidate": recovery_spec.candidate_id, "profile": profile.version},
                        finalize_staging=lambda staging: validate_private_entry(
                            staging, publish_integrity=True
                        ),
                        validate_existing=lambda entry: validate_private_entry(
                            entry, publish_integrity=False
                        ),
                        copy_function=lambda source, destination: _copy_file_cancellable(
                            source, destination, cancel
                        ),
                        cancel_check=lambda: _check_cancelled(
                            cancel, "cancelled waiting for recovery cache lock"
                        ),
                        is_cancellation=lambda exc: isinstance(
                            exc, ProcessCancelledError
                        ),
                    )
                    recovery_cache_sealed = True
                    # The atomic rename invalidates paths rooted in private
                    # staging. Reopen and reauthorize only from final/payload.
                    validate_private_entry(stored, publish_integrity=False)
                    cached_result = cache_state["result"]
                    cached_build = cache_state["build"]
                    cached_evaluation = cache_state["evaluation"]
                    _check_cancelled(
                        cancel, "cancelled before focused recovery promotion"
                    )
                    if not _verify_recovery_cache_entry(
                        stored, recovery_key, cancel
                    ):
                        raise ValueError("sealed focused recovery cache changed")
                    _validate_authorized_recovery_boundary(
                        result=cached_result, manifest=manifest, recipe=recipe,
                        cache_key=recovery_key, cancel_event=cancel,
                    )
                    evaluations.append(cached_evaluation)
                    candidate_builds[recovery_spec.candidate_id] = cached_build
                    recovery_authorizations[recovery_spec.candidate_id] = (
                        cached_result, recipe, recovery_key, stored,
                    )
                    attempts.append(AttemptReport(
                        recovery_spec.candidate_id, recovery_spec.engine, "passed",
                        cached_evaluation.size, cached_evaluation.structural,
                        cached_evaluation.visual, False, "", cached_build.provenance,
                    ))
                    emit(
                        "candidate_finished", family=manifest.model_rel,
                        candidate=recovery_spec.candidate_id, status="passed",
                        compiled_bytes=cached_evaluation.size.total_bytes, cache_hit=False,
                    )
                    break
                except ProcessCancelledError as exc:
                    if recovery_cache_owned and not recovery_cache_sealed:
                        try:
                            cache.invalidate(recovery_key)
                        except Exception:
                            pass
                    if recovery_evidence_path is not None and workspace is not None:
                        try:
                            _safe_workspace_leaf(
                                workspace,
                                recovery_evidence_path,
                                "cancelled focused recovery evidence",
                            )
                            if (
                                os.path.lexists(recovery_evidence_path)
                                and recovery_evidence_path.is_file()
                                and not _is_reparse(recovery_evidence_path)
                            ):
                                recovery_evidence_path.unlink()
                        except (OSError, ValueError):
                            pass
                    cancel.set()
                    attempts.append(AttemptReport(
                        recovery_spec.candidate_id, recovery_spec.engine,
                        "cancelled", None, None, None, False, str(exc), {},
                    ))
                    emit(
                        "candidate_finished", family=manifest.model_rel,
                        candidate=recovery_spec.candidate_id, status="cancelled",
                    )
                    break
                except Exception as exc:
                    if recovery_cache_owned and not recovery_cache_sealed:
                        try:
                            cache.invalidate(recovery_key)
                        except Exception:
                            pass
                    if recovery_evidence_path is not None and workspace is not None:
                        try:
                            _safe_workspace_leaf(
                                workspace,
                                recovery_evidence_path,
                                "failed focused recovery evidence",
                            )
                            if (
                                os.path.lexists(recovery_evidence_path)
                                and recovery_evidence_path.is_file()
                                and not _is_reparse(recovery_evidence_path)
                            ):
                                recovery_evidence_path.unlink()
                        except (OSError, ValueError):
                            pass
                    attempts.append(AttemptReport(
                        recovery_spec.candidate_id, recovery_spec.engine,
                        "compile_failed", None, None, None, False, str(exc), {},
                    ))
                    emit(
                        "candidate_finished", family=manifest.model_rel,
                        candidate=recovery_spec.candidate_id,
                        status="compile_failed",
                        stage=getattr(exc, "stage", "focused-recovery"),
                    )
                    break

    winner = None if recovery_error_reason is not None else select_winner(evaluations)
    no_positive_saving = (
        winner is not None
        and _without_dx80(winner.size).total_bytes
        >= original_without_dx80.total_bytes
    )
    if no_positive_saving:
        winner = None
    if cancel.is_set():
        status, reason, selected = "cancelled", "run cancelled; original family retained", original_family
        selected_id = None
        provenance = {"source": "original-preserved"}
    elif recovery_error_reason is not None:
        status, reason, selected = "failed", recovery_error_reason, original_without_dx80
        selected_id = None
        provenance = {"source": "original-preserved"}
    elif winner is None:
        status, reason, selected = (
            "preserved",
            (
                "no candidate produced a strictly positive compiled saving"
                if no_positive_saving
                else "no candidate passed all hard gates"
            ),
            original_without_dx80,
        )
        selected_id = None
        provenance = {"source": "original-preserved"}
    else:
        status, reason, selected = (
            "optimized",
            "smallest passing compiled candidate selected",
            _without_dx80(winner.size),
        )
        selected_id = winner.spec.candidate_id
        selected_builds[manifest.family_id] = candidate_builds[selected_id]
        if selected_id in recovery_authorizations:
            selected_recovery_authorizations[manifest.family_id] = (
                recovery_authorizations[selected_id]
            )
        chosen_attempt = next(item for item in attempts if item.candidate_id == selected_id)
        provenance = dict(chosen_attempt.provenance)
    selected_snapshots.append(selected)
    savings = compare_snapshots(original_family, control_size, selected)
    approved_attempts = (
        [next(item for item in attempts if item.candidate_id == selected_id)]
        if selected_id is not None
        else [item for item in attempts if item.status in {"control", "control_rejected"}]
    )
    metrics, scopes = _worst(approved_attempts)
    margins: dict[str, float] = {}
    if selected_id is not None:
        selected_attempt = approved_attempts[0]
        if selected_attempt.visual is not None:
            margins = {
                name: float(limit) - float(selected_attempt.visual.metrics.get(name, 0.0))
                for name, limit in profile.limits.items()
            }
    outcome = FamilyRunOutcome(
        manifest.family_id, manifest.model_rel, status, selected_id,
        original_family, control_size, selected, savings, tuple(attempts), reason,
        metrics, scopes, provenance, margins,
    )
    outcomes.append(outcome)
    emit(
        "family_finished",
        family=manifest.model_rel,
        status=status,
        selected=selected_id,
        reason=reason,
        best_bytes=selected.total_bytes,
        reduction_percent=(
            (original_family.total_bytes - selected.total_bytes)
            / original_family.total_bytes
            * 100
            if original_family.total_bytes
            else 0.0
        ),
    )
    clear_recovery_artifacts = getattr(
        adapter_set, "clear_recovery_artifacts", None
    )
    if callable(clear_recovery_artifacts):
        clear_recovery_artifacts()
    return terminal_result_value()


def run_maximum_addon(
    config: MaximumRunConfig,
    cancel_event: threading.Event | None = None,
    *,
    adapters: AdapterSet | None = None,
    validator: StructuralValidator | None = None,
    event_sink: EventSink | None = None,
    profile_selector: Callable[[FamilyManifest], FamilyFidelitySelection] | None = None,
) -> MaximumRunReport:
    if not isinstance(config, MaximumRunConfig):
        raise TypeError("config must be MaximumRunConfig")
    # Loading the calibrated profile is deliberately first: the production sentinel
    # must fail closed before any candidate, cache mutation, or output promotion.
    profile_set = load_fidelity_profile_set(config.profile_path)
    validate_run_paths(config, create=False)
    _recover_output_transaction(config.output_dir)
    validate_run_paths(config, create=True)
    cancel = cancel_event or threading.Event()
    sink = event_sink or _default_sink
    if cancel.is_set():
        report_path = config.work_dir / "logs" / "maximum_report.json"
        events = (
            canonical_payload({
                "schema": 1,
                "kind": "run_started",
                "family_count": 0,
                "report_path": "logs/maximum_report.json",
            }),
            canonical_payload({
                "schema": 1,
                "kind": "run_cancelled",
                "completed_families": 0,
                "total_families": 0,
                "report_path": "logs/maximum_report.json",
            }),
        )
        original = _empty_snapshot(config.addon_dir / "models")
        empty = _empty_snapshot(config.work_dir)
        report = MaximumRunReport(
            1, "cancelled", original, empty, empty, original,
            {}, (), report_path, events, True,
        )
        atomic_write_json(report_path, report)
        atomic_write_json(
            config.work_dir / "logs" / "fidelity-profile-selection.json",
            {"schema": 1, "selector": profile_set.mode, "families": []},
        )
        for event in events:
            try:
                sink(dict(event))
            except Exception:
                pass
        return report
    adapter_set = adapters or ProductionAdapters(config, cancel)
    structural_validator = validator or (
        lambda manifest, build: validate_structure(
            manifest,
            build.optimized_qc,
            build.compiled_models_dir,
            build.compile_record,
            build.provenance,
        )
    )
    report_path = config.work_dir / "logs" / "maximum_report.json"
    versions = dict(adapter_set.tool_versions())
    versions["profile_sha256"] = _sha256_file(config.profile_path, cancel)
    versions["fidelity_selector"] = profile_set.mode
    dependency = _dependency_proof(config, cancel)
    versions["dependency_digest"] = str(dependency["digest"])
    if any(
        not isinstance(key, str) or not key or not isinstance(value, str)
        for key, value in versions.items()
    ):
        raise MaximumConfigError("tool versions must be non-empty string mappings")
    original = scan_compiled_models(config.addon_dir / "models")
    outcomes: list[FamilyRunOutcome] = []
    control_snapshots: list[CompiledSizeSnapshot] = []
    selected_snapshots: list[CompiledSizeSnapshot] = []
    selected_builds: dict[str, CandidateBuild] = {}
    selected_recovery_authorizations: dict[
        str, tuple[FocusedRecoveryAdapterResult, object, CacheKey, Path]
    ] = {}
    selection_audit_path = config.work_dir / "logs" / "fidelity-profile-selection.json"
    selection_records: list[dict[str, Any]] = []
    cache = CandidateCache(config.work_dir / "cache")
    event_history: list[dict[str, Any]] = []
    candidate_counts: dict[str, int] = {}
    candidate_indexes: dict[tuple[str, str], int] = {}
    journal_lock = threading.RLock()
    peak_active_families = 0
    runtime_memory_throttled = config.parallelism.memory_throttled
    tools = CandidateTools(
        sys.executable,
        config.blender_path,
        config.studiomdl_path,
        config.repo_root,
        heuristic_map=config.work_dir / "logs" / "selective_policy_map.json",
        meshopt_dll=config.repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll",
        blender_threads=config.parallelism.blender_threads,
    )

    def write_selection_audit() -> None:
        with journal_lock:
            atomic_write_json(selection_audit_path, {
                "schema": 1,
                "selector": profile_set.mode,
                "families": selection_records,
            })

    def parallelism_payload() -> dict[str, object]:
        return {
            "requested_jobs": config.parallelism.requested_jobs,
            "cpu_target_jobs": config.parallelism.cpu_target_jobs,
            "effective_jobs": config.parallelism.effective_jobs,
            "memory_limit_jobs": config.parallelism.memory_limit_jobs,
            "memory_throttled": runtime_memory_throttled,
            "peak_active_families": peak_active_families,
        }

    def partial_payload(status: str) -> dict[str, object]:
        return {
            "schema": 1,
            "status": status,
            "original_size": original,
            "control_size": _combine_snapshots(config.work_dir, control_snapshots),
            "selected_size": _combine_snapshots(config.work_dir, selected_snapshots),
            "tool_versions": versions,
            "families": tuple(outcomes),
            "events": tuple(event_history),
            "parallelism": parallelism_payload(),
        }

    def write_journal(payload: Mapping[str, object]) -> None:
        atomic_write_json(report_path, payload)
        write_selection_audit()

    journal = DurableProgressJournal(
        write_journal,
        min_interval=0.25,
    )

    def _emit_locked(kind: str, **payload: Any) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown Maximum event kind: {kind}")
        payload.setdefault("report_path", "logs/maximum_report.json")
        family_rel = payload.get("family")
        if isinstance(family_rel, str):
            manifest_for_event = next(
                (item for item in manifests if item.model_rel == family_rel), None
            )
            if manifest_for_event is not None:
                payload.setdefault("family_id", manifest_for_event.family_id)
                payload.setdefault("family_index", manifests.index(manifest_for_event))
                payload.setdefault("family_total", len(manifests) + len(missing_model_rels))
            if kind.startswith("family_"):
                if "index" in payload:
                    payload.setdefault("family_index", payload["index"])
                if "total" in payload:
                    payload.setdefault("family_total", payload["total"])
        if "candidate" in payload:
            payload.setdefault("candidate_id", payload["candidate"])
        if kind == "candidate_started" and isinstance(family_rel, str):
            index = candidate_counts.get(family_rel, 0)
            candidate_counts[family_rel] = index + 1
            candidate_indexes[(family_rel, str(payload.get("candidate", "")))] = index
            payload.setdefault("index", index)
            payload.setdefault("total", config.budget.max_candidates + 1)
            payload.setdefault("candidate_index", index)
            payload.setdefault("candidate_total", config.budget.max_candidates + 1)
        elif isinstance(family_rel, str) and "candidate" in payload:
            index = candidate_indexes.get((family_rel, str(payload["candidate"])))
            if index is not None:
                payload.setdefault("candidate_index", index)
                payload.setdefault("candidate_total", config.budget.max_candidates + 1)
        if "status" in payload:
            payload.setdefault("gate_status", payload["status"])
        if kind == "best_updated" and "compiled_bytes" in payload:
            payload.setdefault("best_bytes", payload["compiled_bytes"])
        if isinstance(family_rel, str) and "compiled_bytes" in payload:
            original_for_event = _family_snapshot(original, family_rel).total_bytes
            payload.setdefault(
                "reduction_percent",
                (
                    (original_for_event - int(payload["compiled_bytes"]))
                    / original_for_event
                    * 100
                    if original_for_event
                    else 0.0
                ),
            )
        if kind == "family_finished" and isinstance(family_rel, str):
            finished = next(
                (item for item in reversed(outcomes) if item.model_rel == family_rel),
                None,
            )
            if finished is not None:
                payload.setdefault("best_bytes", finished.selected_size.total_bytes)
                payload.setdefault(
                    "reduction_percent",
                    (
                        (finished.original_size.total_bytes - finished.selected_size.total_bytes)
                        / finished.original_size.total_bytes
                        * 100
                        if finished.original_size.total_bytes
                        else 0.0
                    ),
                )
        event = {"schema": 1, "kind": kind, **payload}
        # Canonicalization rejects non-finite data and unsupported/path objects.
        canonical_payload(event)
        frozen_event = canonical_payload(event)
        event_history.append(frozen_event)
        try:
            sink(dict(frozen_event))
        except Exception:
            # Progress observers are non-authoritative; the atomic report journal
            # remains the durable protocol and must still reach a terminal state.
            pass
        journal.publish(
            lambda: partial_payload("running"),
            force=kind in {"family_finished", "run_finished", "run_cancelled"},
        )

    def emit(kind: str, **payload: Any) -> None:
        with journal_lock:
            _emit_locked(kind, **payload)

    manifests: tuple[FamilyManifest, ...] = ()
    missing_model_rels: tuple[str, ...] = ()
    try:
        manifests, missing_model_rels = _reconcile_inventory(
            original, tuple(adapter_set.inventory(config))
        )
    except Exception as exc:
        emit("run_started", family_count=0)
        emit(
            "run_finished",
            status="failed",
            optimized=0,
            preserved=0,
            failed=0,
            final_bytes=original.total_bytes,
            reason=type(exc).__name__,
        )
        empty = _empty_snapshot(config.work_dir)
        report = MaximumRunReport(
            1,
            "failed",
            original,
            empty,
            original,
            original,
            versions,
            (),
            report_path,
            tuple(event_history),
            False,
            parallelism_payload(),
        )
        atomic_write_json(report_path, report)
        return report
    diagnostic_method = getattr(adapter_set, "inventory_diagnostics", None)
    inventory_diagnostics = (
        dict(diagnostic_method(config)) if callable(diagnostic_method) else {}
    )
    total_family_count = len(manifests) + len(missing_model_rels)
    parallel_fork = getattr(adapter_set, "fork_for_family", None)
    if config.parallelism.effective_jobs > 1 and not callable(parallel_fork):
        raise MaximumConfigError(
            "parallel Maximum adapters must implement fork_for_family()"
        )
    emit("run_started", family_count=total_family_count)

    selected_snapshot_by_family: dict[str, CompiledSizeSnapshot] = {}
    control_snapshot_by_family: dict[str, CompiledSizeSnapshot] = {}

    def commit_family_result(result: _FamilyExecutionResult) -> None:
        outcomes.append(result.outcome)
        if result.control_snapshot is not None:
            control_snapshots.append(result.control_snapshot)
            control_snapshot_by_family[result.outcome.family_id] = (
                result.control_snapshot
            )
        selected_snapshots.append(result.selected_snapshot)
        selected_snapshot_by_family[result.outcome.family_id] = result.selected_snapshot
        if result.selected_build is not None:
            selected_builds[result.outcome.family_id] = result.selected_build
        if result.recovery_authorization is not None:
            selected_recovery_authorizations[result.outcome.family_id] = (
                result.recovery_authorization
            )

    def execute_family(item: tuple[int, FamilyManifest]) -> _FamilyExecutionResult:
        family_index, manifest = item
        family_adapters = (
            adapter_set
            if config.parallelism.effective_jobs == 1
            else parallel_fork()
        )
        family_cache = (
            cache
            if config.parallelism.effective_jobs == 1
            else CandidateCache(config.work_dir / "cache")
        )
        return _execute_family(
            index=family_index, manifest=manifest,
            config=config,
            original=original,
            profile_set=profile_set,
            adapter_set=family_adapters,
            structural_validator=structural_validator,
            cache=family_cache,
            versions=versions,
            dependency=dependency,
            tools=tools,
            cancel=cancel,
            emit=emit,
            profile_selector=profile_selector,
            total_family_count=total_family_count,
        )

    def emit_scheduler_status(update: FamilySchedulerUpdate) -> None:
        nonlocal peak_active_families, runtime_memory_throttled
        with journal_lock:
            peak_active_families = max(peak_active_families, update.active)
            runtime_memory_throttled = (
                runtime_memory_throttled or update.memory_throttled
            )
            _emit_locked(
                "scheduler_status",
                active_families=update.active,
                completed_families=update.completed,
                family_total=total_family_count,
                effective_jobs=update.capacity,
                memory_throttled=update.memory_throttled,
            )

    work_items = tuple(
        FamilyWorkItem(
            index,
            _family_work_estimate(manifest, original),
            (index, manifest),
        )
        for index, manifest in enumerate(manifests)
    )
    family_results = run_family_jobs(
        work_items,
        max_workers=config.parallelism.effective_jobs,
        worker=execute_family,
        cancel_event=cancel,
        memory_limit=lambda: current_memory_job_limit(
            config.parallelism, detect_memory_snapshot()
        ),
        on_update=emit_scheduler_status,
    )
    completed_indexes = {result.index for result in family_results}
    for result in family_results:
        selection_records.append(dict(result.selection_record))
        commit_family_result(result)

    # Families not reached after cancellation remain explicit.
    if cancel.is_set() and len(completed_indexes) < len(manifests):
        for manifest_index, manifest in enumerate(manifests):
            if manifest_index in completed_indexes:
                continue
            original_family = _family_snapshot(original, manifest.model_rel)
            if not any(
                item["family_id"] == manifest.family_id for item in selection_records
            ):
                selection_records.append({
                    "family_id": manifest.family_id,
                    "model_rel": manifest.model_rel,
                    "status": "cancelled",
                    "profile_class": None,
                    "reason": "run-cancelled-before-selection",
                    "version": None,
                    "corpus_hash": profile_set.corpus_hash,
                    "sources": [],
                })
            cancelled_outcome = FamilyRunOutcome(
                manifest.family_id, manifest.model_rel, "cancelled", None,
                original_family, None, original_family, {}, (),
                "run cancelled before family started", {}, {}, {"source": "original-preserved"},
            )
            outcomes.append(cancelled_outcome)
            selected_snapshot_by_family[manifest.family_id] = original_family

    manifest_order = {
        manifest.family_id: index for index, manifest in enumerate(manifests)
    }
    outcomes.sort(key=lambda item: manifest_order[item.family_id])
    selection_records.sort(
        key=lambda item: manifest_order.get(str(item["family_id"]), len(manifests))
    )
    selected_snapshots[:] = [
        selected_snapshot_by_family[outcome.family_id] for outcome in outcomes
    ]
    control_snapshots[:] = [
        control_snapshot_by_family[outcome.family_id]
        for outcome in outcomes
        if outcome.family_id in control_snapshot_by_family
    ]
    write_selection_audit()

    for missing_index, model_rel in enumerate(missing_model_rels, start=len(manifests)):
        family_id = hashlib.sha256(model_rel.casefold().encode("utf-8")).hexdigest()
        original_family = _family_snapshot(original, model_rel)
        missing_status = "cancelled" if cancel.is_set() else "preserved"
        retained = original_family if cancel.is_set() else _without_dx80(original_family)
        diagnostic = str(
            inventory_diagnostics.get(model_rel)
            or inventory_diagnostics.get(model_rel.casefold())
            or "model was not successfully decompiled/inventoried"
        )
        reason = "run cancelled before family started" if cancel.is_set() else diagnostic
        emit(
            "family_started",
            family=model_rel,
            family_id=family_id,
            index=missing_index,
            total=total_family_count,
        )
        outcomes.append(FamilyRunOutcome(
            family_id,
            model_rel,
            missing_status,
            None,
            original_family,
            None,
            retained,
            compare_snapshots(original_family, original_family, retained),
            (),
            reason,
            {},
            {},
            {item.relative_path: "original-preserved" for item in retained.artifacts},
        ))
        selected_snapshots.append(retained)
        emit(
            "family_finished",
            family=model_rel,
            family_id=family_id,
            index=missing_index,
            total=total_family_count,
            status=missing_status,
            reason=reason,
        )

    control_total = _combine_snapshots(config.work_dir, control_snapshots)
    selected_total = _combine_snapshots(config.work_dir, selected_snapshots)
    if cancel.is_set():
        final = original
        emit("run_cancelled", completed_families=len([item for item in outcomes if item.status != "cancelled"]), total_families=total_family_count)
        report = MaximumRunReport(
            1, "cancelled", original, control_total, selected_total, final,
            versions, tuple(outcomes), report_path, tuple(event_history), True,
            parallelism_payload(),
        )
        atomic_write_json(report_path, report)
        return report

    staging = config.output_dir.parent / f".{config.output_dir.name}.maximum-staging-{uuid.uuid4().hex}"
    try:
        if staging.exists():
            raise RuntimeError("unique output staging path unexpectedly exists")
        selected_pairs = tuple(
            (
                outcome,
                selected_builds[outcome.family_id],
                next(item for item in manifests if item.family_id == outcome.family_id),
            )
            for outcome in outcomes
            if outcome.status == "optimized"
        )
        _copytree_cancellable(config.addon_dir, staging, cancel)
        expected_output = _expected_output_manifest(config.addon_dir, selected_pairs, cancel)
        for outcome in outcomes:
            if cancel.is_set():
                raise ProcessCancelledError("cancelled during output staging")
            if outcome.status != "optimized":
                continue
            build = selected_builds.get(outcome.family_id)
            if build is None:
                raise RuntimeError(f"selected build is unavailable for {outcome.model_rel}")
            recovery_authorization = selected_recovery_authorizations.get(
                outcome.family_id
            )
            if recovery_authorization is not None:
                recovery_result, recovery_recipe, recovery_key, cache_entry = (
                    recovery_authorization
                )
                if not _verify_recovery_cache_entry(
                    cache_entry, recovery_key, cancel
                ):
                    raise ValueError("selected recovery cache changed before staging")
                manifest = next(
                    item for item in manifests if item.family_id == outcome.family_id
                )
                _validate_authorized_recovery_boundary(
                    result=recovery_result, manifest=manifest,
                    recipe=recovery_recipe, cache_key=recovery_key,
                    cancel_event=cancel,
                )
                _copy_authorized_recovery_family(
                    build, staging / "models", manifest,
                    recovery_result.evidence.compile_files, cancel,
                )
            else:
                _copy_selected_family(
                    build, staging / "models", outcome.model_rel, cancel
                )
        _remove_dx80_artifacts(staging / "models", cancel)
        if cancel.is_set():
            raise ProcessCancelledError("cancelled before output promotion")
        _promote_verified_tree(
            staging,
            config.output_dir,
            expected_output,
            cancel_event=cancel,
        )
    except ProcessCancelledError:
        cancel.set()
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        emit("run_cancelled", completed_families=len(outcomes), total_families=total_family_count)
        report = MaximumRunReport(
            1, "cancelled", original, control_total, selected_total, original,
            versions, tuple(outcomes), report_path, tuple(event_history), True,
            parallelism_payload(),
        )
        atomic_write_json(report_path, report)
        return report
    except BaseException as exc:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        final = (
            scan_compiled_models(config.output_dir / "models")
            if (config.output_dir / "models").is_dir()
            else original
        )
        emit(
            "run_finished",
            status="failed",
            optimized=sum(item.status == "optimized" for item in outcomes),
            preserved=sum(item.status == "preserved" for item in outcomes),
            failed=sum(item.status == "failed" for item in outcomes),
            final_bytes=final.total_bytes,
            reason=type(exc).__name__,
        )
        report = MaximumRunReport(
            1, "failed", original, control_total, selected_total, final,
            versions, tuple(outcomes), report_path, tuple(event_history), False,
            parallelism_payload(),
        )
        atomic_write_json(report_path, report)
        return report
    final = scan_compiled_models(config.output_dir / "models")
    # The promoted, hash-verified tree is the single source of truth for selected accounting.
    selected_total = final
    status = "success" if all(item.status in {"optimized", "preserved"} for item in outcomes) else "failed"
    emit("run_finished", status=status, optimized=sum(item.status == "optimized" for item in outcomes), preserved=sum(item.status == "preserved" for item in outcomes), failed=sum(item.status == "failed" for item in outcomes), final_bytes=final.total_bytes)
    report = MaximumRunReport(
        1, status, original, control_total, selected_total, final,
        versions, tuple(outcomes), report_path, tuple(event_history), False,
        parallelism_payload(),
    )
    atomic_write_json(report_path, report)
    return report


@dataclass(frozen=True)
class VisualConfiguration:
    name: str
    sources: tuple[Path, ...]
    source_identities: tuple[str, ...]
    bodygroup_indices: tuple[tuple[str, int], ...]
    lod_index: int = 0


def _logical_visual_source_identity(graph: QcGraph, path: Path) -> str:
    relative = path.resolve(strict=True).relative_to(
        graph.family_root.resolve(strict=True)
    )
    parts = list(PurePosixPath(relative.as_posix()).parts)
    if len(parts) >= 2 and parts[-2].casefold() == "output":
        parts.pop(-2)
    elif parts and parts[0].casefold() == "output":
        parts.pop(0)
    if not parts:
        raise ValueError("visual source has no logical identity")
    leaf = PurePosixPath(parts[-1])
    stem = re.sub(r"_opt$", "", leaf.stem, flags=re.IGNORECASE)
    parts[-1] = f"{stem}{leaf.suffix.casefold()}"
    identity = PurePosixPath(*(part.casefold() for part in parts)).as_posix()
    collisions = {
        reference.source_path.resolve(strict=True)
        for reference in graph.references
        if reference.role == "visual"
        and _normalized_visual_identity_without_collision_check(
            graph, reference.source_path
        ) == identity
    }
    if len(collisions) != 1:
        raise ValueError(f"ambiguous logical visual source identity: {identity}")
    return identity


def _normalized_visual_identity_without_collision_check(
    graph: QcGraph, path: Path
) -> str:
    relative = path.resolve(strict=True).relative_to(
        graph.family_root.resolve(strict=True)
    )
    parts = list(PurePosixPath(relative.as_posix()).parts)
    if len(parts) >= 2 and parts[-2].casefold() == "output":
        parts.pop(-2)
    elif parts and parts[0].casefold() == "output":
        parts.pop(0)
    leaf = PurePosixPath(parts[-1])
    parts[-1] = f"{re.sub(r'_opt$', '', leaf.stem, flags=re.IGNORECASE)}{leaf.suffix.casefold()}"
    return PurePosixPath(*(part.casefold() for part in parts)).as_posix()


def _graph_visual_configurations(
    graph: QcGraph, *, max_alternatives: int = 8
) -> tuple[VisualConfiguration, ...]:
    if type(max_alternatives) is not int or max_alternatives < 0:
        raise ValueError("bodygroup alternative bound must be a non-negative integer")
    fixed_references = tuple(
        reference
        for reference in graph.references
        if reference.role == "visual"
        and reference.directive not in {"$lod/replacemodel", "$bodygroup/studio"}
    )
    fixed = tuple(reference.source_path for reference in fixed_references)
    fixed_identities = tuple(
        _logical_visual_source_identity(graph, reference.source_path)
        for reference in fixed_references
    )
    groups = graph.bodygroups
    defaults = tuple(group.choices[0] for group in groups)
    base = fixed + tuple(choice.source_path for choice in defaults if choice is not None)
    base_identities = fixed_identities + tuple(
        _logical_visual_source_identity(graph, choice.source_path)
        for choice in defaults if choice is not None
    )
    if not base:
        raise ValueError("QC graph has no base visual sources")
    default_indices = tuple(
        (f"{index:03d}:{group.name}", 0) for index, group in enumerate(groups)
    )
    states: list[VisualConfiguration] = [
        VisualConfiguration("engine-default", base, base_identities, default_indices)
    ]
    alternatives = 0
    for group_index, group in enumerate(groups):
        for choice_index, choice in enumerate(group.choices[1:], start=1):
            if alternatives >= max_alternatives:
                break
            selected = list(defaults)
            selected[group_index] = choice
            sources = fixed + tuple(
                selected_choice.source_path
                for selected_choice in selected
                if selected_choice is not None
            )
            source_identities = fixed_identities + tuple(
                _logical_visual_source_identity(graph, selected_choice.source_path)
                for selected_choice in selected
                if selected_choice is not None
            )
            indices = tuple(
                (
                    f"{index:03d}:{item.name}",
                    choice_index if index == group_index else 0,
                )
                for index, item in enumerate(groups)
            )
            safe_name = re.sub(r"[^a-z0-9_.-]+", "-", group.name.casefold()).strip("-")
            stable_hash = hashlib.sha256(group.name.casefold().encode("utf-8")).hexdigest()[:8]
            states.append(VisualConfiguration(
                f"bodygroup-{safe_name or 'group'}-{group_index:03d}-{stable_hash}-{choice_index}",
                sources,
                source_identities,
                indices,
            ))
            alternatives += 1
        if alternatives >= max_alternatives:
            break
    lod_groups: dict[str, list] = {}
    for reference in graph.references:
        if reference.directive == "$lod/replacemodel":
            lod_groups.setdefault(reference.group, []).append(reference)
    for lod_index, (group, references) in enumerate(lod_groups.items(), start=1):
        if not group or len(references) % 2:
            raise ValueError("LOD replacement graph is ambiguous")
        replacements = {
            references[index].source_path: references[index + 1].source_path
            for index in range(0, len(references), 2)
        }
        state = tuple(replacements.get(source, source) for source in base)
        state_identities = tuple(
            _logical_visual_source_identity(graph, source) for source in state
        )
        if state == base:
            raise ValueError("LOD state does not replace a base source")
        states.append(VisualConfiguration(
            f"lod-{lod_index}", state, state_identities, default_indices, lod_index
        ))
    return tuple(states)


def _graph_visual_states(graph: QcGraph) -> tuple[tuple[Path, ...], ...]:
    """Compatibility view for callers that only need source tuples."""
    return tuple(state.sources for state in _graph_visual_configurations(graph))


def _validate_visual_configuration_pairing(
    original: Sequence[VisualConfiguration],
    candidate: Sequence[VisualConfiguration],
) -> None:
    if len(original) != len(candidate):
        raise ValueError("render configuration counts differ")
    for before, after in zip(original, candidate):
        if (
            before.name,
            before.bodygroup_indices,
            before.lod_index,
        ) != (
            after.name,
            after.bodygroup_indices,
            after.lod_index,
        ):
            raise ValueError("render configuration identities differ")
        if before.source_identities != after.source_identities:
            raise ValueError(
                "render logical source identities or order differ"
            )


def _smd_animation_frames(path: Path) -> tuple[int, ...]:
    if path.suffix.casefold() != ".smd":
        return ()
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ()
    in_skeleton = False
    frames: list[int] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.casefold() == "skeleton":
            in_skeleton = True
        elif in_skeleton and stripped.casefold() == "end":
            break
        elif in_skeleton:
            match = re.fullmatch(r"time\s+(-?\d+)", stripped, re.IGNORECASE)
            if match:
                frames.append(int(match.group(1)))
    return tuple(dict.fromkeys(frames))


def _smd_controlling_bones(path: Path) -> frozenset[int] | None:
    """Return positive triangle influences, or None when rigid evidence is unsafe."""
    if path.suffix.casefold() != ".smd":
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return None

    section = ""
    saw_nodes = False
    saw_triangles = False
    nodes: dict[int, int] = {}
    triangle_rows: list[str] = []
    triangle_position = 0
    malformed = False
    node_pattern = re.compile(r'(-?\d+)\s+"[^"]*"\s+(-?\d+)')

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        folded = line.casefold()
        if not section:
            if folded == "nodes":
                if saw_nodes:
                    malformed = True
                saw_nodes = True
                section = "nodes"
            elif folded == "triangles":
                if saw_triangles:
                    malformed = True
                saw_triangles = True
                section = "triangles"
                triangle_position = 0
            continue
        if folded == "end":
            if section == "triangles" and triangle_position != 0:
                malformed = True
            section = ""
            continue
        if section == "nodes":
            match = node_pattern.fullmatch(line)
            if match is None:
                malformed = True
                continue
            bone_id, parent_id = (int(value) for value in match.groups())
            if bone_id < 0 or bone_id in nodes:
                malformed = True
            else:
                nodes[bone_id] = parent_id
        elif section == "triangles":
            if triangle_position == 0:
                triangle_position = 1
            else:
                triangle_rows.append(line)
                triangle_position = (triangle_position + 1) % 4

    if section or not saw_nodes or not saw_triangles or not nodes or not triangle_rows:
        return None
    if malformed or any(parent != -1 and parent not in nodes for parent in nodes.values()):
        return None

    controlling: set[int] = set()
    for row in triangle_rows:
        fields = row.split()
        if len(fields) < 9:
            return None
        try:
            parent = int(fields[0])
            numeric = tuple(float(value) for value in fields[1:9])
        except ValueError:
            return None
        if parent not in nodes or any(not math.isfinite(value) for value in numeric):
            return None
        if len(fields) == 9:
            controlling.add(parent)
            continue
        try:
            link_count = int(fields[9])
        except ValueError:
            return None
        if link_count < 0 or len(fields) != 10 + (link_count * 2):
            return None
        explicit_sum = 0.0
        linked_bones: set[int] = set()
        for index in range(link_count):
            try:
                bone_id = int(fields[10 + (index * 2)])
                weight = float(fields[11 + (index * 2)])
            except ValueError:
                return None
            if (
                bone_id < 0 or bone_id not in nodes or bone_id in linked_bones
                or not math.isfinite(weight) or weight < 0 or weight > 1
            ):
                return None
            linked_bones.add(bone_id)
            explicit_sum += weight
            if weight > 0:
                controlling.add(bone_id)
        tolerance = 1e-6
        if explicit_sum > 1.0 + tolerance:
            return None
        # SMD v1 assigns any unlisted remainder to the vertex parent bone.
        if explicit_sum < 1.0 - tolerance:
            controlling.add(parent)
    return frozenset(controlling) if controlling else None


def _smd_deformation_required(paths: Sequence[Path]) -> bool:
    """Fail closed unless every real visual vertex has one common controlling bone."""
    controlling: set[int] = set()
    saw_source = False
    for path in paths:
        saw_source = True
        evidence = _smd_controlling_bones(path)
        if evidence is None:
            return True
        controlling.update(evidence)
        if len(controlling) >= 2:
            return True
    return not saw_source or len(controlling) != 1


def _canonical_graph_source_identity(graph: QcGraph, path: Path) -> str:
    return path.resolve(strict=True).relative_to(
        graph.family_root.resolve(strict=True)
    ).as_posix()


def _validate_complete_recovery_graph_pairing(
    original_graph: QcGraph, candidate_graph: QcGraph
) -> None:
    def signature(graph: QcGraph) -> tuple[tuple[str, str, str], ...]:
        values = []
        for reference in graph.references:
            identity = (
                _logical_visual_source_identity(graph, reference.source_path)
                if reference.role == "visual"
                else _canonical_graph_source_identity(graph, reference.source_path).casefold()
            )
            values.append((reference.role, reference.directive.casefold(), identity))
        return tuple(sorted(values))

    if signature(original_graph) != signature(candidate_graph):
        raise ValueError("recovery optimized/original QC graphs are not exactly paired")
    _validate_visual_configuration_pairing(
        _graph_visual_configurations(original_graph),
        _graph_visual_configurations(candidate_graph),
    )


def _paired_representative_animation(
    original_graph: QcGraph,
    candidate_graph: QcGraph,
) -> tuple[Path, Path, int] | None:
    def refs(graph: QcGraph):
        result: dict[tuple[str, str], list[Any]] = {}
        for reference in graph.references:
            if reference.role != "animation":
                continue
            identity = _canonical_graph_source_identity(graph, reference.source_path)
            key = (reference.directive.casefold(), identity.casefold())
            result.setdefault(key, []).append(reference)
        return result

    before = refs(original_graph)
    after = refs(candidate_graph)
    if not before or not after or before.keys() != after.keys():
        return None
    selected: tuple[Path, Path, int] | None = None
    for key in sorted(before):
        if len(before[key]) != len(after[key]):
            return None
        for original, optimized in zip(before[key], after[key]):
            original_frames = _smd_animation_frames(original.source_path)
            optimized_frames = _smd_animation_frames(optimized.source_path)
            if original_frames != optimized_frames:
                return None
            representative = max((frame for frame in original_frames if frame > 0), default=0)
            if representative and selected is None:
                selected = original.source_path, optimized.source_path, representative
    return selected


_WHOLE_INDEX_FIELDS = {
    "schema", "selector_version", "family", "candidate", "profiles",
    "dependency", "renderer", "full_region_manifest", "animation", "states",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGION_KEY_RE = re.compile(r"^r-[0-9a-f]{64}$")


def _safe_workspace_leaf(workspace: Path, path: Path, label: str) -> Path:
    root = Path(workspace).resolve(strict=True)
    leaf = Path(path)
    try:
        parent = leaf.parent.resolve(strict=True)
        parent.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} parent is not contained") from exc
    current = root
    if _is_reparse(current):
        raise ValueError(f"{label} workspace is a reparse point")
    for part in parent.relative_to(root).parts:
        current = current / part
        if _is_reparse(current) or not current.is_dir():
            raise ValueError(f"{label} ancestor is unsafe")
    if os.path.lexists(leaf) and _is_reparse(leaf):
        raise ValueError(f"{label} leaf is a reparse point")
    return leaf


def _safe_workspace_atomic_json(
    workspace: Path, path: Path, payload: Mapping[str, object], label: str
) -> None:
    leaf = _safe_workspace_leaf(workspace, path, label)
    atomic_write_json(leaf, payload)
    _safe_workspace_leaf(workspace, leaf, label)


def _remove_workspace_owned_tree(
    workspace: Path,
    path: Path,
    label: str,
    *,
    max_files: int = 128,
    max_bytes: int = 2 * 512 * 1024 ** 2 + 2 * 16 * 1024 ** 2,
) -> None:
    _safe_workspace_leaf(workspace, path, label)
    if not os.path.lexists(path):
        return
    try:
        _focused_remove_owned_tree(
            path,
            path.parent,
            max_files=max_files,
            max_bytes=max_bytes,
        )
    except (OSError, ValueError) as exc:
        raise CandidateBuildError(f"{label} is unsafe", stage="focused-render") from exc


def _safe_workspace_mkdir(workspace: Path, path: Path, label: str) -> Path:
    root = Path(workspace).resolve(strict=True)
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise CandidateBuildError(f"{label} escapes workspace", stage="focused-render") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            if _is_reparse(current) or not current.is_dir():
                raise CandidateBuildError(
                    f"{label} ancestor is unsafe", stage="focused-render"
                )
        else:
            current.mkdir()
            if _is_reparse(current) or not current.is_dir():
                raise CandidateBuildError(
                    f"{label} creation is unsafe", stage="focused-render"
                )
    return current


def _whole_exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"whole visual index {label} fields are invalid")
    return value


def _whole_hash(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"whole visual index {label} hash is invalid")
    return value


def _whole_relative(value: object, label: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise ValueError(f"whole visual index {label} path is not relative canonical POSIX")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute() or windows.is_absolute() or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or posix.as_posix() != value
    ):
        raise ValueError(f"whole visual index {label} path is not relative canonical POSIX")
    return value


def _validate_whole_file_proof(
    value: object,
    workspace: Path,
    label: str,
    cancel_event: threading.Event | None,
    path_registry: dict[str, str] | None = None,
) -> dict[str, str]:
    proof = _whole_exact(value, {"path", "sha256"}, label)
    relative = _whole_relative(proof["path"], label)
    if path_registry is not None:
        folded = relative.casefold()
        previous = path_registry.get(folded)
        if previous is not None and previous != relative:
            raise ValueError(f"whole visual index {label} path case-collides with {previous}")
        path_registry[folded] = relative
    expected = _whole_hash(proof["sha256"], label)
    root = Path(workspace).resolve(strict=True)
    path = Path(workspace) / PurePosixPath(relative)
    for component in _existing_components(path):
        if _is_reparse(component):
            raise ValueError(f"whole visual index {label} path contains a reparse point")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        info = path.lstat()
    except (OSError, ValueError) as exc:
        raise ValueError(f"whole visual index {label} path is not contained") from exc
    if _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"whole visual index {label} path is not a regular file")
    _size, actual = _focused_file_proof(
        path, cancel_event, contained_root=root,
    )
    if actual != expected:
        raise ValueError(f"whole visual index {label} current file hash changed")
    return {"path": relative, "sha256": expected}


def _validate_whole_visual_payload(
    raw: object,
    workspace: Path,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    payload = _whole_exact(raw, _WHOLE_INDEX_FIELDS, "root")
    path_registry: dict[str, str] = {}
    if payload["schema"] != 1 or payload["selector_version"] != "surface-risk-top-k-v1":
        raise ValueError("whole visual index schema/selector is invalid")
    family = _whole_exact(
        payload["family"], {"family_id", "model_rel", "input_sha256"}, "family"
    )
    _whole_hash(family["family_id"], "family id")
    _whole_relative(family["model_rel"], "model")
    _whole_hash(family["input_sha256"], "family input")
    candidate = _whole_exact(
        payload["candidate"], {"candidate_id", "spec", "cache_digest"}, "candidate"
    )
    if type(candidate["candidate_id"]) is not str or not candidate["candidate_id"]:
        raise ValueError("whole visual index candidate id is invalid")
    spec_fields = {
        "candidate_id", "engine", "target_ratio", "target_error", "repair_profile",
        "region_overrides", "strategy", "update_vertices", "transfer",
    }
    if type(candidate["spec"]) is dict and "composite_recipe" in candidate["spec"]:
        spec_fields.add("composite_recipe")
    spec = _whole_exact(candidate["spec"], spec_fields, "candidate spec")
    if spec["candidate_id"] != candidate["candidate_id"]:
        raise ValueError("whole visual index candidate spec identity differs")
    if spec.get("composite_recipe") is not None:
        recipe = composite_recipe_from_payload(spec["composite_recipe"])
        if candidate["candidate_id"] != "recovery-" + recipe.recipe_sha256:
            raise ValueError("whole visual index composite candidate identity differs")
    _whole_hash(candidate["cache_digest"], "candidate cache")
    profiles = _whole_exact(payload["profiles"], {"whole", "focused"}, "profiles")
    for name in ("whole", "focused"):
        profile = _whole_exact(
            profiles[name], {"version", "corpus_hash", "file_sha256"}, f"{name} profile"
        )
        if type(profile["version"]) is not str or not profile["version"]:
            raise ValueError("whole visual index profile version is invalid")
        _whole_hash(profile["corpus_hash"], f"{name} profile corpus")
        _whole_hash(profile["file_sha256"], f"{name} profile file")
    dependency = _whole_exact(payload["dependency"], {"digest"}, "dependency")
    renderer = _whole_exact(payload["renderer"], {"version", "digest"}, "renderer")
    _whole_hash(dependency["digest"], "dependency")
    if type(renderer["version"]) is not str or not renderer["version"]:
        raise ValueError("whole visual index renderer version is invalid")
    _whole_hash(renderer["digest"], "renderer")
    _validate_whole_file_proof(
        payload["full_region_manifest"], workspace, "full region manifest", cancel_event,
        path_registry,
    )
    animation = _whole_exact(
        payload["animation"], {"classification", "reference", "candidate"}, "animation"
    )
    _validate_whole_file_proof(
        animation["classification"], workspace, "animation classification", cancel_event,
        path_registry,
    )
    if (animation["reference"] is None) != (animation["candidate"] is None):
        raise ValueError("whole visual index animation source pairing is invalid")
    if animation["reference"] is not None:
        _validate_whole_file_proof(
            animation["reference"], workspace, "reference animation", cancel_event,
            path_registry,
        )
        _validate_whole_file_proof(
            animation["candidate"], workspace, "candidate animation", cancel_event,
            path_registry,
        )
        if animation["reference"]["path"].casefold() == animation["candidate"]["path"].casefold():
            raise ValueError("whole visual index animation source pair self-references")
    states = payload["states"]
    if type(states) is not list or not states or len(states) > 16:
        raise ValueError("whole visual index states are invalid")
    if [state.get("state_index") if type(state) is dict else None for state in states] != list(range(len(states))):
        raise ValueError("whole visual index state indices are not contiguous")
    state_names: set[str] = set()
    for index, state_value in enumerate(states):
        state = _whole_exact(state_value, {
            "state_index", "state_name", "bodygroups", "lod_index", "poses",
            "region_manifest", "configuration_manifest", "reference_manifest",
            "candidate_manifest", "sources", "geometry_rows",
        }, f"state {index}")
        name = state["state_name"]
        if type(name) is not str or not name or name in state_names:
            raise ValueError("whole visual index state names are invalid")
        state_names.add(name)
        if type(state["lod_index"]) is not int or state["lod_index"] < 0:
            raise ValueError("whole visual index state LOD is invalid")
        bodygroups = state["bodygroups"]
        if (
            type(bodygroups) is not list
            or any(type(item) is not list or len(item) != 2 or type(item[0]) is not str or type(item[1]) is not int for item in bodygroups)
            or bodygroups != sorted(bodygroups, key=lambda item: (item[0].casefold(), item[0]))
        ):
            raise ValueError("whole visual index state bodygroups are invalid")
        poses = state["poses"]
        if type(poses) is not list or not poses or poses[0] != "bind" or len(poses) > 2 or len(set(poses)) != len(poses):
            raise ValueError("whole visual index state poses are invalid")
        for field in (
            "region_manifest", "configuration_manifest", "reference_manifest", "candidate_manifest"
        ):
            _validate_whole_file_proof(
                state[field], workspace, f"state {index} {field}", cancel_event,
                path_registry,
            )
        if state["reference_manifest"]["path"].casefold() == state["candidate_manifest"]["path"].casefold():
            raise ValueError("whole visual index render manifest pair self-references")
        sources = state["sources"]
        if type(sources) is not list or not sources:
            raise ValueError("whole visual index state sources are invalid")
        identities: list[str] = []
        for source_value in sources:
            source = _whole_exact(
                source_value, {"source_identity", "reference", "candidate"}, "state source"
            )
            identity = _whole_relative(source["source_identity"], "source identity")
            identities.append(identity)
            _validate_whole_file_proof(
                source["reference"], workspace, "reference source", cancel_event,
                path_registry,
            )
            _validate_whole_file_proof(
                source["candidate"], workspace, "candidate source", cancel_event,
                path_registry,
            )
            if source["reference"]["path"].casefold() == source["candidate"]["path"].casefold():
                raise ValueError("whole visual index source pair self-references")
        if identities != sorted(identities, key=lambda item: (item.casefold(), item)) or len({item.casefold() for item in identities}) != len(identities):
            raise ValueError("whole visual index state source identities are not canonical")
        rows = state["geometry_rows"]
        if type(rows) is not list or not rows:
            raise ValueError("whole visual index geometry rows are invalid")
        if [
            (row.get("scope"), row.get("pose")) if type(row) is dict else (None, None)
            for row in rows
        ] != sorted(
            (
                (row.get("scope"), row.get("pose")) if type(row) is dict else (None, None)
                for row in rows
            ),
            key=lambda item: (str(item[0]), str(item[1])),
        ):
            raise ValueError("whole visual index geometry rows are not canonical order")
        coverage: set[tuple[str, str]] = set()
        for row_value in rows:
            row = _whole_exact(
                row_value,
                {"scope", "pose", "surface_bidirectional_p95", "surface_max"},
                "geometry row",
            )
            if type(row["scope"]) is not str or _REGION_KEY_RE.fullmatch(row["scope"]) is None or row["pose"] not in poses:
                raise ValueError("whole visual index geometry identity is invalid")
            for metric in ("surface_bidirectional_p95", "surface_max"):
                value = row[metric]
                if isinstance(value, bool) or type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError("whole visual index geometry metric is invalid")
            key = (row["scope"], row["pose"])
            if key in coverage:
                raise ValueError("whole visual index geometry rows are duplicated")
            coverage.add(key)
        scopes = {scope for scope, _pose in coverage}
        if coverage != {(scope, pose) for scope in scopes for pose in poses}:
            raise ValueError("whole visual index geometry pose coverage is incomplete")
        candidate_manifest = state["candidate_manifest"]
        try:
            manifest_bytes = _read_regular_no_follow(
                workspace / PurePosixPath(candidate_manifest["path"]),
                cancel_event, contained_root=workspace,
            )
            if hashlib.sha256(manifest_bytes).hexdigest() != candidate_manifest["sha256"]:
                raise ValueError("whole visual index candidate manifest changed")
            manifest_payload = json.loads(manifest_bytes.decode("utf-8"))
            manifest_rows = sorted((
                {
                    "scope": item["scope"],
                    "pose": item["pose"],
                    "surface_bidirectional_p95": item["surface_bidirectional_p95"],
                    "surface_max": item["surface_max"],
                }
                for item in manifest_payload["geometry"]
            ), key=lambda item: (item["scope"], item["pose"]))
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(
                "whole visual index candidate manifest geometry is invalid"
            ) from exc
        if manifest_rows != rows:
            raise ValueError(
                "whole visual index geometry rows differ from candidate manifest"
            )
    return payload


def _write_whole_visual_index(
    path: Path,
    workspace: Path,
    payload: Mapping[str, object],
    cancel_event: threading.Event | None,
) -> str:
    mutable = json.loads(canonical_json(payload))
    _validate_whole_visual_payload(mutable, workspace, cancel_event)
    seal = hashlib.sha256(canonical_json(mutable).encode("utf-8")).hexdigest()
    mutable["evidence_sha256"] = seal
    _check_cancelled(cancel_event, "cancelled before whole visual index publication")
    _safe_workspace_atomic_json(workspace, path, mutable, "whole visual index")
    return seal


def _load_whole_visual_index(
    path: Path,
    workspace: Path,
    expected_seal: str,
    cancel_event: threading.Event | None,
) -> Mapping[str, object]:
    _whole_hash(expected_seal, "expected evidence")
    try:
        raw = json.loads(_read_regular_no_follow(
            path, cancel_event, contained_root=Path(workspace),
        ).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("whole visual index cannot be read safely") from exc
    sealed = _whole_exact(raw, _WHOLE_INDEX_FIELDS | {"evidence_sha256"}, "sealed root")
    supplied = _whole_hash(sealed.pop("evidence_sha256"), "evidence")
    actual = hashlib.sha256(canonical_json(sealed).encode("utf-8")).hexdigest()
    if supplied != actual or supplied != expected_seal:
        raise ValueError("whole visual index seal is invalid")
    _validate_whole_visual_payload(sealed, workspace, cancel_event)
    sealed["evidence_sha256"] = supplied
    return deep_freeze(sealed)


def _aggregate_visual_results(results: Sequence[tuple[str, ValidationResult]]) -> ValidationResult:
    failures: list[GateFailure] = []
    metrics: dict[str, float] = {}
    worst_scope = ""
    worst_score = math.inf
    for state, result in results:
        for name, value in result.metrics.items():
            number = float(value)
            if name == "fidelity_score":
                metrics[name] = min(metrics.get(name, math.inf), number)
            else:
                metrics[name] = max(metrics.get(name, float("-inf")), number)
        failures.extend(
            GateFailure(
                failure.gate,
                f"{state}/{failure.scope}" if failure.scope else state,
                failure.measured,
                failure.limit,
                failure.message,
            )
            for failure in result.failures
        )
        score = float(result.metrics.get("fidelity_score", 0.0 if not result.passed else 1.0))
        result_scope = result.worst_scope or (result.failures[0].scope if result.failures else "")
        if result_scope and score < worst_score:
            worst_score = score
            worst_scope = f"{state}/{result_scope}"
    return ValidationResult(
        all(result.passed for _, result in results),
        tuple(failures),
        metrics,
        worst_scope,
    )


def _validate_whole_index_current_bindings(
    index: Mapping[str, object],
    *,
    whole_profile: FidelityProfile,
    focused_profile: FidelityProfile,
    profile_file_sha256: str,
    dependency_digest: str,
    renderer_digest: str,
    candidate_cache_digest: str,
) -> None:
    expected_profiles = {
        "whole": (whole_profile.version, whole_profile.corpus_hash),
        "focused": (focused_profile.version, focused_profile.corpus_hash),
    }
    for name, (version, corpus_hash) in expected_profiles.items():
        indexed = index["profiles"][name]
        if (
            indexed["version"] != version
            or indexed["corpus_hash"] != corpus_hash
            or indexed["file_sha256"] != profile_file_sha256
        ):
            raise ValueError(f"whole visual index current {name} profile differs")
    if index["dependency"]["digest"] != dependency_digest:
        raise ValueError("whole visual index current dependency differs")
    if (
        index["renderer"]["version"] != "focus-render-v1"
        or index["renderer"]["digest"] != renderer_digest
    ):
        raise ValueError("whole visual index current renderer differs")
    if index["candidate"]["cache_digest"] != candidate_cache_digest:
        raise ValueError("whole visual index current candidate cache binding differs")


def _aggregate_focused_gate(
    whole: ValidationResult,
    gate: FocusedGateResult,
) -> ValidationResult:
    if not isinstance(whole, ValidationResult) or not isinstance(gate, FocusedGateResult):
        raise TypeError("focused gate aggregate arguments are invalid")
    targets = tuple(gate.targets)
    regions = dict(gate.regions)
    target_keys = tuple(target.region_key for target in targets)
    if (
        not targets
        or tuple(target.rank for target in targets) != tuple(range(len(targets)))
        or len(set(target_keys)) != len(target_keys)
        or _SHA256_RE.fullmatch(gate.evidence_sha256) is None
        or set(regions) != set(target_keys)
        or any(
            not isinstance(regions.get(target.region_key), FocusRegionResult)
            or regions[target.region_key].target != target
            or _SHA256_RE.fullmatch(regions[target.region_key].evidence_sha256) is None
            for target in targets
        )
    ):
        raise ValueError("focused gate evidence/rank/cardinality is invalid")
    failures = list(whole.failures)
    metrics = {name: float(value) for name, value in whole.metrics.items()}
    worst_scope = whole.worst_scope or (
        whole.failures[0].scope if whole.failures else ""
    )
    worst_score = (
        float(whole.metrics.get("fidelity_score", 0.0))
        if not whole.passed and worst_scope
        else math.inf
    )
    passed = whole.passed
    for target in targets:
        result = regions[target.region_key].validation
        if not isinstance(result, ValidationResult):
            raise TypeError("focused region validation is invalid")
        passed = passed and result.passed
        for failure in result.failures:
            scope = failure.scope
            if not scope.startswith(target.region_key + "/"):
                scope = f"{target.region_key}/{scope or result.worst_scope or target.anchor_pose}"
            failures.append(GateFailure(
                failure.gate, scope, failure.measured, failure.limit, failure.message,
            ))
        for name, value in result.metrics.items():
            number = float(value)
            if name == "fidelity_score":
                metrics[name] = min(metrics.get(name, number), number)
            else:
                metrics[name] = max(metrics.get(name, number), number)
        if not result.passed:
            score = float(result.metrics.get("fidelity_score", 0.0))
            if score < worst_score:
                worst_score = score
                worst_scope = f"{target.region_key}/{result.worst_scope or target.anchor_pose}"
    region_aggregate = _aggregate_visual_results(tuple(
        (target.region_key, regions[target.region_key].validation)
        for target in targets
    ))
    if (
        not isinstance(gate.validation, ValidationResult)
        or gate.validation.passed != region_aggregate.passed
        or {name: float(value) for name, value in gate.validation.metrics.items()}
        != {name: float(value) for name, value in region_aggregate.metrics.items()}
    ):
        raise ValueError("focused gate validation differs from region records")
    return ValidationResult(passed, tuple(failures), metrics, worst_scope)


class ProductionAdapters:
    def __init__(self, config: MaximumRunConfig, cancel_event: threading.Event) -> None:
        self.config = config
        self.cancel_event = cancel_event
        self._whole_index_seals: dict[Path, str] = {}
        self._candidate_cache_digests: dict[Path, str] = {}
        self._focused_runtime: dict[
            Path, tuple[FocusedEvidenceContext, FocusSelection, tuple[object, ...], Mapping[str, object]]
        ] = {}
        self._recovery_artifact_roots: dict[str, Path] = {}
        self._reference_store = ReferenceBundleStore(
            self.config.work_dir / "reference-render-cache"
        )
        self._reference_identities: dict[
            tuple[str, str], ReferenceBundleIdentity
        ] = {}

    def fork_for_family(self) -> ProductionAdapters:
        return ProductionAdapters(self.config, self.cancel_event)

    def _blender_command(self, *arguments: str | Path) -> tuple[str, ...]:
        prefix = [str(self.config.blender_path)]
        threads = self.config.parallelism.blender_threads
        if threads > 0:
            prefix.extend(("--threads", str(threads)))
        prefix.extend(str(argument) for argument in arguments)
        return tuple(prefix)

    def compile_adaptive_direct_candidate(self, **kwargs):
        """Typed E2 boundary; scheduling remains an E3 concern."""
        from .production_adapters import AdaptiveDirectProductionBoundary
        kwargs.setdefault("cancel_event", self.cancel_event)
        return AdaptiveDirectProductionBoundary(
            process_runner=run_process
        ).compile_adaptive_direct_candidate(**kwargs)

    def render_adaptive_direct_source_union(self, **kwargs):
        """Fresh source-union boundary with no focused-render cache."""
        from .production_adapters import (
            AdaptiveDirectProductionBoundary, SourceUnionRenderTools,
            build_source_union_python_runtime_contract,
        )
        kwargs.setdefault("cancel_event", self.cancel_event)
        kwargs.pop("tools", None)
        candidate_compile = kwargs.get("candidate_compile")
        if candidate_compile is None or not hasattr(candidate_compile, "build"):
            raise TypeError("source-union compile result is unavailable")
        renderer_script = self.config.repo_root / "render_previews.py"
        _renderer_size, renderer_sha256 = _file_proof(
            renderer_script, self.cancel_event,
        )
        kwargs["tools"] = SourceUnionRenderTools(
            blender_exe=self.config.blender_path,
            renderer_script=renderer_script,
            renderer_sha256=renderer_sha256,
            python_runtime=build_source_union_python_runtime_contract(
                self.config.repo_root / "maximum_optimizer", self.cancel_event,
            ),
            materials_roots=self._materials_roots(),
            vtfcmd=self._vtfcmd(),
            texture_cache=None,
            dependency_digest_provider=lambda event: str(
                _dependency_proof(self.config, event)["digest"]
            ),
            blender_threads=self.config.parallelism.blender_threads,
        )
        return AdaptiveDirectProductionBoundary(
            process_runner=run_process
        ).render_adaptive_direct_source_union(**kwargs)

    def clear_recovery_artifacts(self) -> None:
        self._recovery_artifact_roots.clear()

    def _materialize_recovery_focused_artifacts(
        self,
        workspace: Path,
        round_index: int,
        records: tuple[FocusedRenderEvidence, ...],
        prior_recoveries: tuple[FocusedRecoveryEvidence, ...],
        cancel_event: threading.Event,
    ) -> Path:
        destination_root = workspace / "focused-authorized"
        if os.path.lexists(destination_root):
            _remove_workspace_owned_tree(
                workspace, destination_root, "focused authorization root"
            )
        destination_root.mkdir(parents=True)
        for prior in prior_recoveries:
            if not prior.rerun_records:
                (destination_root / f"round-{prior.round_index:03d}").mkdir()
                continue
            prior_root = self._recovery_artifact_roots.get(prior.evidence_sha256)
            if prior_root is None or not prior_root.is_dir():
                raise CandidateBuildError(
                    "prior focused recovery artifacts are unavailable",
                    stage="focused-recovery",
                )
            source_round = prior_root / f"round-{prior.round_index:03d}"
            _copytree_cancellable(
                source_round,
                destination_root / source_round.name,
                cancel_event,
            )
        round_root = destination_root / f"round-{round_index:03d}"
        round_root.mkdir()
        for record in records:
            identity = f"{record.target.rank:03d}-{record.target.region_key}"
            snapshot = workspace / "focused-snapshots" / identity
            rendered = workspace / "focused-renders" / identity
            if snapshot.is_dir():
                sides = ((snapshot / "reference", "reference"),
                         (snapshot / "candidate", "candidate"))
            elif rendered.is_dir():
                sides = ((rendered / "original", "reference"),
                         (rendered / "optimized", "candidate"))
            else:
                raise CandidateBuildError(
                    "focused recovery render artifacts are unavailable",
                    stage="focused-recovery",
                )
            target_root = round_root / identity
            for source, side in sides:
                _copytree_cancellable(source, target_root / side, cancel_event)
            current = _focused_render_file_manifest(
                FocusRenderDirectories(
                    target_root / "reference", target_root / "candidate"
                ),
                cancel_event,
            )
            if current != record.files:
                raise CandidateBuildError(
                    "focused recovery render changed while snapshotting",
                    stage="focused-recovery",
                )
        return destination_root

    def bind_candidate_cache_digest(self, candidate: CandidateBuild, digest: str) -> None:
        if not isinstance(candidate, CandidateBuild) or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("candidate cache digest binding is invalid")
        self._candidate_cache_digests[candidate.workspace.resolve()] = digest

    @staticmethod
    def _remove_evidence_file(workspace: Path, path: Path) -> None:
        try:
            _safe_workspace_leaf(workspace, path, "candidate visual evidence")
        except ValueError as exc:
            raise CandidateBuildError(str(exc), stage="render") from exc
        if not os.path.lexists(path):
            return
        if _is_reparse(path) or not path.is_file():
            raise CandidateBuildError("candidate visual evidence path is unsafe", stage="render")
        path.unlink()

    @staticmethod
    def _workspace_proof(workspace: Path, path: Path, cancel_event: threading.Event) -> dict[str, str]:
        root = workspace.resolve(strict=True)
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise CandidateBuildError("visual evidence path escapes candidate workspace", stage="render") from exc
        _size, digest = _focused_file_proof(
            path, cancel_event, contained_root=root,
        )
        return {"path": relative, "sha256": digest}

    def _vtfcmd(self) -> Path | None:
        return _configured_vtfcmd(self.config)

    def _materials_roots(self) -> tuple[Path, ...]:
        values = [self.config.addon_dir / "materials"]
        values.extend(
            Path(value).expanduser()
            for value in os.environ.get("MAXIMUM_MATERIAL_ROOTS", "").split(os.pathsep)
            if value.strip()
        )
        roots: list[Path] = []
        for value in values:
            root = value.resolve(strict=True)
            if not root.is_dir():
                raise CandidateBuildError(
                    f"material overlay root is not a directory: {root}", stage="render"
                )
            if root not in roots:
                roots.append(root)
        return tuple(roots)

    def _texture_cache_root(self, candidate: CandidateBuild) -> Path:
        identity = hashlib.sha256(
            str(candidate.workspace.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:16]
        return Path(tempfile.gettempdir()).resolve() / "maximum-vtf-cache" / identity

    @staticmethod
    def _optional_file_digest(path: Path | None) -> str:
        if path is None or not path.is_file() or _is_reparse(path):
            return hashlib.sha256(b"missing").hexdigest()
        return _sha256_file(path)

    def _reference_identity(
        self, manifest: FamilyManifest, profile: FidelityProfile
    ) -> ReferenceBundleIdentity:
        cache_key = (manifest.family_id, profile.version)
        cached = self._reference_identities.get(cache_key)
        if cached is not None:
            return cached
        material_roots = tuple(str(path) for path in self._materials_roots())
        identity = ReferenceBundleIdentity(
            family_id=manifest.family_id,
            family_hash=manifest.input_hash,
            renderer_sha256=self._optional_file_digest(
                self.config.repo_root / "render_previews.py"
            ),
            profile_sha256=self._optional_file_digest(self.config.profile_path),
            dependency_digest=str(
                _dependency_proof(self.config, self.cancel_event)["digest"]
            ),
            material_roots_sha256=hashlib.sha256(
                canonical_json(material_roots).encode("utf-8")
            ).hexdigest(),
            vtfcmd_sha256=self._optional_file_digest(self._vtfcmd()),
        )
        self._reference_identities[cache_key] = identity
        return identity

    def _publish_reference_bundle(
        self,
        identity: ReferenceBundleIdentity,
        render_root: Path,
        state_names: Sequence[str],
        workspace: Path,
    ) -> ReferenceBundle:
        source = workspace / "reference-bundle-source"
        state_count = max(1, len(state_names))
        cleanup_limits = {
            "max_files": state_count * _REFERENCE_RENDER_FILES_PER_STATE,
            "max_bytes": state_count * _REFERENCE_RENDER_BYTES_PER_STATE,
        }
        if os.path.lexists(source):
            _remove_workspace_owned_tree(
                workspace,
                source,
                "reference bundle staging source",
                **cleanup_limits,
            )
        source.mkdir()
        try:
            for state_name in state_names:
                original = render_root / state_name / "original"
                if not (original / "render_manifest.json").is_file():
                    raise CandidateBuildError(
                        "reference render manifest is unavailable", stage="render"
                    )
                _copytree_cancellable(
                    original, source / state_name / "original", self.cancel_event
                )
            return self._reference_store.publish(identity, source)
        finally:
            if os.path.lexists(source):
                _remove_workspace_owned_tree(
                    workspace,
                    source,
                    "reference bundle staging source",
                    **cleanup_limits,
                )

    def _materialize_reference_state(
        self,
        bundle: ReferenceBundle,
        state_name: str,
        destination: Path,
    ) -> None:
        source = bundle.root / state_name / "original"
        if not (source / "render_manifest.json").is_file():
            raise CandidateBuildError(
                f"sealed reference state is missing: {state_name}", stage="render"
            )
        _copytree_cancellable(source, destination, self.cancel_event)

    def inventory(self, config: MaximumRunConfig) -> Sequence[FamilyManifest]:
        return build_family_manifests(
            config.addon_dir / "models",
            config.work_dir / "logs" / "decompile_manifest.json",
            config.work_dir / "src",
        )

    def inventory_diagnostics(self, config: MaximumRunConfig) -> Mapping[str, str]:
        try:
            payload = json.loads(
                (config.work_dir / "logs" / "decompile_manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
        result: dict[str, str] = {}
        for record in payload.get("results", ()) if type(payload) is dict else ():
            if type(record) is not dict or record.get("status") == "ok":
                continue
            model_rel = str(record.get("model_rel") or record.get("model_rel_fallback") or "").replace("\\", "/")
            if not model_rel:
                continue
            result[model_rel] = str(
                record.get("error") or record.get("message") or f"decompile status={record.get('status')}"
            )
        return result

    def build(
        self,
        manifest: FamilyManifest,
        spec: CandidateSpec,
        workspace: Path,
        tools: CandidateTools,
        cancel_event: threading.Event,
    ) -> CandidateBuild:
        if spec.engine == "fidelity":
            adapter = FidelityAdapter(cancel_event=cancel_event)
        elif spec.engine == "blender":
            adapter = BlenderAdapter(cancel_event=cancel_event)
        elif spec.engine == "meshoptimizer":
            adapter = MeshoptimizerAdapter(cancel_event=cancel_event)
        else:
            raise CandidateBuildError(
                f"unknown candidate engine: {spec.engine!r}",
                stage="candidate-validation",
            )
        return adapter.generate(manifest, spec, workspace, tools)

    def visual(
        self,
        manifest: FamilyManifest,
        control: CandidateBuild,
        candidate: CandidateBuild,
        profile: FidelityProfile,
        *,
        focused_profile: FidelityProfile | None = None,
    ) -> ValidationResult:
        whole_index_path = candidate.workspace / "logs" / "whole-visual-index.json"
        if focused_profile is not None:
            if not isinstance(focused_profile, FidelityProfile):
                raise TypeError("focused profile is invalid")
            for stale in (
                whole_index_path,
                candidate.workspace / "logs" / "focused-region-gate.json",
                candidate.workspace / "logs" / "focused-region-gate.partial.json",
            ):
                self._remove_evidence_file(candidate.workspace, stale)
            self._whole_index_seals.pop(candidate.workspace.resolve(), None)
            self._focused_runtime.pop(candidate.workspace.resolve(), None)
        source_root = candidate.workspace / "render-source"
        if source_root.exists():
            shutil.rmtree(source_root)
        render_root = candidate.workspace / "renders"
        if render_root.exists():
            if _is_reparse(render_root) or not render_root.is_dir():
                raise CandidateBuildError("render root is unsafe", stage="render")
            shutil.rmtree(render_root)
        _copytree_cancellable(manifest.source_dir, source_root, self.cancel_event)
        expression = (
            "import sys;from pathlib import Path;"
            f"sys.path.insert(0,{str(self.config.repo_root)!r});"
            "from batch_optimize_maximum import write_source_region_manifest;"
            f"write_source_region_manifest(Path({str(source_root)!r}))"
        )
        manifest_log = candidate.workspace / "logs" / "render-manifest.log"
        result = run_process(
            self._blender_command("--background", "--python-expr", expression),
            cwd=self.config.repo_root,
            log_path=manifest_log,
            cancel_event=self.cancel_event,
        )
        if result.returncode != 0:
            raise CandidateBuildError(
                f"region manifest generation exited with code {result.returncode}",
                stage="render",
                log_path=manifest_log,
            )
        region_manifest = source_root / "maximum_region_manifest.json"
        try:
            full_region_manifest = load_region_manifest_payload(
                json.loads(region_manifest.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise CandidateBuildError(
                f"region manifest is invalid: {exc}",
                stage="render",
                log_path=manifest_log,
            ) from exc
        candidate_source_root = candidate.workspace / "src"
        original_qcs = _matching_qcs(manifest.source_dir, manifest.model_rel, optimized=False)
        if len(original_qcs) != 1:
            raise CandidateBuildError(
                "original QC for render validation is missing or ambiguous",
                stage="render",
            )
        try:
            original_graph = parse_qc_graph(original_qcs[0], manifest.source_dir)
            candidate_graph = parse_qc_graph(candidate.optimized_qc, candidate_source_root)
            original_states = _graph_visual_configurations(original_graph)
            candidate_states = _graph_visual_configurations(candidate_graph)
        except (OSError, ValueError) as exc:
            raise CandidateBuildError(f"render QC graph is invalid: {exc}", stage="render") from exc
        try:
            _validate_visual_configuration_pairing(original_states, candidate_states)
        except ValueError as exc:
            raise CandidateBuildError(
                f"render bodygroup/LOD state pairs are ambiguous: {exc}",
                stage="render",
            ) from exc

        original_visual_sources = tuple(
            source for state in original_states for source in state.sources
        )
        candidate_visual_sources = tuple(
            source for state in candidate_states for source in state.sources
        )
        deformation_required = (
            _smd_deformation_required(original_visual_sources)
            or _smd_deformation_required(candidate_visual_sources)
        )
        animation = (
            _paired_representative_animation(original_graph, candidate_graph)
            if deformation_required
            else None
        )
        if not deformation_required:
            classification = {
                "schema": 1,
                "required": False,
                "reason": "rigid-or-bind-only",
                "selected_frame": None,
                "original_source_identity": None,
                "candidate_source_identity": None,
            }
        elif animation is not None:
            classification = {
                "schema": 1,
                "required": True,
                "reason": "deformable-with-representative-animation",
                "selected_frame": animation[2],
                "original_source_identity": _canonical_graph_source_identity(
                    original_graph, animation[0]
                ),
                "candidate_source_identity": _canonical_graph_source_identity(
                    candidate_graph, animation[1]
                ),
            }
        else:
            classification = {
                "schema": 1,
                "required": True,
                "reason": "representative-animation-unavailable",
                "selected_frame": None,
                "original_source_identity": None,
                "candidate_source_identity": None,
            }
        atomic_write_json(
            candidate.workspace / "logs" / "render-animation-classification.json",
            classification,
        )
        if deformation_required and animation is None:
            return ValidationResult(
                False,
                (GateFailure(
                    "representative-animation-unavailable",
                    manifest.model_rel,
                    "missing-or-ambiguous",
                    "paired-real-animation",
                    "No paired real animation SMD with a common non-bind frame is available.",
                ),),
                {},
                manifest.model_rel,
            )
        pose_arg = f"bind:0,representative:{animation[2]}" if animation else "bind:0"
        vtfcmd = self._vtfcmd()
        reference_identity = self._reference_identity(manifest, profile)
        reference_bundle = self._reference_store.lookup(reference_identity)
        state_results: list[tuple[str, ValidationResult]] = []
        state_index_records: list[dict[str, object]] = []
        for state_index, (before_state, after_state) in enumerate(
            zip(original_states, candidate_states)
        ):
            if self.cancel_event.is_set():
                raise ProcessCancelledError("cancelled before render validation")
            state_name = before_state.name
            state_root = render_root / state_name
            if reference_bundle is not None:
                self._materialize_reference_state(
                    reference_bundle, state_name, state_root / "original"
                )
            before = tuple(
                source_root / source.relative_to(manifest.source_dir.resolve(strict=True))
                for source in before_state.sources
            )
            after = tuple(after_state.sources)
            if any(not path.is_file() for path in (*before, *after)):
                raise CandidateBuildError("render source pairs are missing", stage="render")
            state_region_manifest = region_manifest.with_name(
                f"maximum_region_manifest.state-{state_index:03d}-{state_name}.json"
            )
            configuration_manifest = region_manifest.with_name(
                f"maximum_configuration.state-{state_index:03d}-{state_name}.json"
            )
            try:
                copied_source_root = source_root.resolve(strict=True)
                source_identities = tuple(
                    path.resolve(strict=True).relative_to(copied_source_root).as_posix()
                    for path in before
                )
                state_manifest_payload = filter_region_manifest(
                    full_region_manifest, source_identities
                ).to_payload()
                load_region_manifest_payload(state_manifest_payload)
                atomic_write_json(state_region_manifest, state_manifest_payload)
                atomic_write_json(configuration_manifest, {
                    "schema": 1,
                    "name": state_name,
                    "bodygroups": dict(before_state.bodygroup_indices),
                    "lod_index": before_state.lod_index,
                    "source_pairs": [
                        {
                            "source_identity": path.resolve(strict=True).relative_to(
                                copied_source_root
                            ).as_posix(),
                            "reference_sha256": _sha256_file(path, self.cancel_event),
                            "candidate_sha256": _sha256_file(candidate_path, self.cancel_event),
                        }
                        for path, candidate_path in zip(before, after)
                    ],
                })
                load_region_manifest_payload(json.loads(
                    state_region_manifest.read_text(encoding="utf-8")
                ))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise CandidateBuildError(
                    f"state region manifest is invalid for {state_name}: {exc}",
                    stage="render",
                ) from exc
            command: list[str] = list(self._blender_command(
                "--background", "--python",
                str(self.config.repo_root / "render_previews.py"), "--",
            ))
            for path in before:
                command.extend(("--before", str(path)))
            for path in after:
                command.extend(("--after", str(path)))
            command.extend((
                "--out", str(state_root), "--size", "512",
                "--passes", "textured,clay", "--poses", pose_arg,
                "--render-side", (
                    "candidate" if reference_bundle is not None else "both"
                ),
                "--region-manifest", str(state_region_manifest),
                "--source-root", str(copied_source_root),
                "--configuration-manifest", str(configuration_manifest),
                "--texture-cache", str(self._texture_cache_root(candidate)),
            ))
            for materials_root in self._materials_roots():
                command.extend(("--materials-root", str(materials_root)))
            if animation is not None:
                original_animation = source_root / animation[0].relative_to(
                    manifest.source_dir.resolve(strict=True)
                )
                command.extend((
                    "--animation-before", str(original_animation),
                    "--animation-after", str(animation[1]),
                ))
            if vtfcmd is not None:
                command.extend(("--vtfcmd", str(vtfcmd)))
            render_log = candidate.workspace / "logs" / f"render-{state_name}.log"
            process = run_process(
                command,
                cwd=self.config.repo_root,
                log_path=render_log,
                cancel_event=self.cancel_event,
            )
            if process.returncode != 0:
                raise CandidateBuildError(
                    f"render validation exited with code {process.returncode}",
                    stage="render",
                    log_path=render_log,
                )
            required = (
                state_root / "original" / "render_manifest.json",
                state_root / "optimized" / "render_manifest.json",
            )
            if any(not path.is_file() for path in required):
                raise CandidateBuildError(
                    "render validation produced no fresh manifest",
                    stage="render",
                    log_path=render_log,
                )
            state_result = compare_render_sets(
                state_root / "original", state_root / "optimized", profile
            )
            state_results.append((state_name, state_result))
            if not state_result.passed:
                aggregate = _aggregate_visual_results(state_results)
                metrics = dict(aggregate.metrics)
                metrics.update({
                    "early_rejected": 1.0,
                    "completed_state_count": float(len(state_results)),
                    "total_state_count": float(len(original_states)),
                })
                return ValidationResult(
                    False, aggregate.failures, metrics, aggregate.worst_scope
                )
            if focused_profile is not None:
                candidate_render_manifest = state_root / "optimized" / "render_manifest.json"
                try:
                    render_manifest_bytes = _read_regular_no_follow(
                        candidate_render_manifest, self.cancel_event,
                        contained_root=candidate.workspace,
                    )
                    render_payload = json.loads(render_manifest_bytes.decode("utf-8"))
                    raw_geometry = render_payload["geometry"]
                    geometry_rows = [
                        {
                            "scope": row["scope"], "pose": row["pose"],
                            "surface_bidirectional_p95": row["surface_bidirectional_p95"],
                            "surface_max": row["surface_max"],
                        }
                        for row in raw_geometry
                    ]
                except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise CandidateBuildError(
                        "whole visual render geometry evidence is invalid", stage="render"
                    ) from exc
                state_index_records.append({
                    "state_index": state_index,
                    "state_name": state_name,
                    "bodygroups": [list(item) for item in before_state.bodygroup_indices],
                    "lod_index": before_state.lod_index,
                    "poses": ["bind", "representative"] if animation else ["bind"],
                    "region_manifest": self._workspace_proof(
                        candidate.workspace, state_region_manifest, self.cancel_event
                    ),
                    "configuration_manifest": self._workspace_proof(
                        candidate.workspace, configuration_manifest, self.cancel_event
                    ),
                    "reference_manifest": self._workspace_proof(
                        candidate.workspace, state_root / "original/render_manifest.json",
                        self.cancel_event,
                    ),
                    "candidate_manifest": self._workspace_proof(
                        candidate.workspace, candidate_render_manifest, self.cancel_event
                    ) | {
                        "sha256": hashlib.sha256(render_manifest_bytes).hexdigest()
                    },
                    "sources": sorted((
                        {
                            "source_identity": identity,
                            "reference": self._workspace_proof(
                                candidate.workspace, before_path, self.cancel_event
                            ),
                            "candidate": self._workspace_proof(
                                candidate.workspace, after_path, self.cancel_event
                            ),
                        }
                        for identity, before_path, after_path in zip(
                            source_identities, before, after
                        )
                    ), key=lambda item: (item["source_identity"].casefold(), item["source_identity"])),
                    "geometry_rows": sorted(
                        geometry_rows, key=lambda row: (row["scope"], row["pose"])
                    ),
                })
        if reference_bundle is None:
            reference_bundle = self._publish_reference_bundle(
                reference_identity,
                render_root,
                tuple(state.name for state in original_states),
                candidate.workspace,
            )
        aggregate = _aggregate_visual_results(state_results)
        completion_metrics = dict(aggregate.metrics)
        completion_metrics.update({
            "early_rejected": 0.0,
            "completed_state_count": float(len(state_results)),
            "total_state_count": float(len(original_states)),
        })
        aggregate = ValidationResult(
            aggregate.passed,
            aggregate.failures,
            completion_metrics,
            aggregate.worst_scope,
        )
        if focused_profile is not None and aggregate.passed:
            profile_file_hash = _sha256_file(self.config.profile_path, self.cancel_event)
            dependency = _dependency_proof(self.config, self.cancel_event)
            renderer_path = self.config.repo_root / "render_previews.py"
            candidate_digest = self._candidate_cache_digests.get(candidate.workspace.resolve())
            if candidate_digest is None:
                raise CandidateBuildError(
                    "candidate cache digest was not bound before whole validation",
                    stage="render",
                )
            animation_reference = animation_candidate = None
            if animation is not None:
                animation_reference = self._workspace_proof(
                    candidate.workspace,
                    source_root / animation[0].relative_to(manifest.source_dir.resolve(strict=True)),
                    self.cancel_event,
                )
                animation_candidate = self._workspace_proof(
                    candidate.workspace, animation[1], self.cancel_event
                )
            payload = {
                "schema": 1,
                "selector_version": "surface-risk-top-k-v1",
                "family": {
                    "family_id": manifest.family_id, "model_rel": manifest.model_rel,
                    "input_sha256": manifest.input_hash,
                },
                "candidate": {
                    "candidate_id": candidate.spec.candidate_id,
                    "spec": candidate.spec.cache_payload(),
                    "cache_digest": candidate_digest,
                },
                "profiles": {
                    "whole": {
                        "version": profile.version, "corpus_hash": profile.corpus_hash,
                        "file_sha256": profile_file_hash,
                    },
                    "focused": {
                        "version": focused_profile.version,
                        "corpus_hash": focused_profile.corpus_hash,
                        "file_sha256": profile_file_hash,
                    },
                },
                "dependency": {"digest": dependency["digest"]},
                "renderer": {
                    "version": "focus-render-v1",
                    "digest": _sha256_file(renderer_path, self.cancel_event),
                },
                "full_region_manifest": self._workspace_proof(
                    candidate.workspace, region_manifest, self.cancel_event
                ),
                "animation": {
                    "classification": self._workspace_proof(
                        candidate.workspace,
                        candidate.workspace / "logs/render-animation-classification.json",
                        self.cancel_event,
                    ),
                    "reference": animation_reference,
                    "candidate": animation_candidate,
                },
                "states": state_index_records,
            }
            seal = _write_whole_visual_index(
                whole_index_path, candidate.workspace, payload, self.cancel_event
            )
            self._whole_index_seals[candidate.workspace.resolve()] = seal
            if self.cancel_event.is_set():
                self._remove_evidence_file(candidate.workspace, whole_index_path)
                self._whole_index_seals.pop(candidate.workspace.resolve(), None)
                raise ProcessCancelledError("cancelled after whole visual index publication")
        return aggregate

    def focused_visual(
        self,
        manifest: FamilyManifest,
        control: CandidateBuild,
        candidate: CandidateBuild,
        whole_profile: FidelityProfile,
        focused_profile: FidelityProfile,
        policy: FocusedRegionPolicy,
    ) -> FocusedGateResult:
        del control
        if (
            not isinstance(whole_profile, FidelityProfile)
            or not isinstance(focused_profile, FidelityProfile)
            or not isinstance(policy, FocusedRegionPolicy)
        ):
            raise TypeError("focused visual arguments are invalid")
        workspace = candidate.workspace.resolve(strict=True)
        expected_seal = self._whole_index_seals.get(workspace)
        if expected_seal is None:
            raise CandidateBuildError("fresh whole visual index is unavailable", stage="focused-render")
        index = _load_whole_visual_index(
            workspace / "logs/whole-visual-index.json",
            workspace,
            expected_seal,
            self.cancel_event,
        )
        profile_file_sha = _sha256_file(self.config.profile_path, self.cancel_event)
        current_dependency = _dependency_proof(self.config, self.cancel_event)["digest"]
        current_renderer = _sha256_file(
            self.config.repo_root / "render_previews.py", self.cancel_event
        )
        current_candidate_digest = self._candidate_cache_digests.get(workspace)
        if current_candidate_digest is None:
            raise CandidateBuildError(
                "candidate cache digest is not currently bound", stage="focused-render"
            )
        try:
            _validate_whole_index_current_bindings(
                index, whole_profile=whole_profile, focused_profile=focused_profile,
                profile_file_sha256=profile_file_sha,
                dependency_digest=str(current_dependency),
                renderer_digest=current_renderer,
                candidate_cache_digest=current_candidate_digest,
            )
        except ValueError as exc:
            raise CandidateBuildError(str(exc), stage="focused-render") from exc
        if (
            index["family"]["family_id"] != manifest.family_id
            or index["family"]["model_rel"] != manifest.model_rel
            or index["family"]["input_sha256"] != manifest.input_hash
            or index["candidate"]["candidate_id"] != candidate.spec.candidate_id
            or canonical_payload(index["candidate"]["spec"])
            != canonical_payload(candidate.spec.cache_payload())
        ):
            raise CandidateBuildError("whole visual index identity differs", stage="focused-render")
        full_manifest_proof = index["full_region_manifest"]
        full_manifest_path = workspace / PurePosixPath(full_manifest_proof["path"])
        try:
            full_manifest = load_region_manifest_payload(json.loads(
                _read_regular_no_follow(
                    full_manifest_path, self.cancel_event, contained_root=workspace
                ).decode("utf-8")
            ))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise CandidateBuildError("whole region manifest cannot authorize focus", stage="focused-render") from exc
        states = tuple(
            WholeStateEvidence(
                state["state_index"], state["state_name"],
                tuple(tuple(item) for item in state["bodygroups"]), state["lod_index"],
                tuple(state["poses"]),
                tuple(
                    (
                        source["source_identity"], source["reference"]["sha256"],
                        source["candidate"]["sha256"],
                    )
                    for source in state["sources"]
                ),
                state["reference_manifest"]["path"],
                state["reference_manifest"]["sha256"],
                state["candidate_manifest"]["path"],
                state["candidate_manifest"]["sha256"],
                tuple(dict(row) for row in state["geometry_rows"]),
            )
            for state in index["states"]
        )
        selection = select_focus_targets_with_evidence(
            states, full_manifest, focused_profile, policy
        )
        cache = FocusedRenderCache(self.config.work_dir / "focused-render-cache")
        whole_proof = FocusProfileProof(
            whole_profile.version, whole_profile.corpus_hash,
            index["profiles"]["whole"]["file_sha256"], whole_profile.limits,
        )
        focused_proof = FocusProfileProof(
            focused_profile.version, focused_profile.corpus_hash,
            profile_file_sha, focused_profile.limits,
        )
        material_proofs: dict[str, Mapping[str, object]] = {}
        records = []
        results: dict[str, FocusRegionResult] = {}
        partial_path = workspace / "logs/focused-region-gate.partial.json"
        final_path = workspace / "logs/focused-region-gate.json"
        self._remove_evidence_file(workspace, final_path)
        for target in selection.selected:
            _check_cancelled(self.cancel_event, "cancelled between focused targets")
            state = index["states"][target.state_index]
            descriptor = full_manifest.by_key[target.region_key].descriptor
            roots = tuple(
                (f"material-root-{position:03d}", root)
                for position, root in enumerate(self._materials_roots())
            )
            requests = tuple({
                "material_identity": material,
                "search_paths": (),
            } for material in (descriptor.materials or ("none",)))
            material = material_resolution_proof(roots, requests, self.cancel_event)
            material_payload = canonical_payload(material)
            material_proofs[target.region_key] = material_payload
            poses = tuple(state["poses"])
            expected = FocusExpectedMatrix(
                target.region_key, poses, EXPECTED_PASSES, EXPECTED_ANGLES,
                512, 512,
                len(poses) * len(EXPECTED_PASSES) * len(EXPECTED_ANGLES),
                len(poses) * len(EXPECTED_PASSES) * len(EXPECTED_ANGLES),
            )
            state_proof = FocusStateProof(
                state["state_index"], state["state_name"],
                tuple(tuple(item) for item in state["bodygroups"]),
                state["lod_index"], poses, target.anchor_pose,
                0 if target.anchor_pose == "bind" else int(
                    json.loads(_read_regular_no_follow(
                        workspace / PurePosixPath(index["animation"]["classification"]["path"]),
                        self.cancel_event, contained_root=workspace,
                    ).decode("utf-8"))["selected_frame"]
                ),
                "source" if index["animation"]["reference"] is not None else "none",
                (
                    index["animation"]["reference"]["sha256"]
                    if index["animation"]["reference"] is not None else None
                ),
            )
            if material.cacheable:
                context = FocusCacheContext(
                    1, manifest.input_hash, index["candidate"]["cache_digest"],
                    tuple(
                        (
                            source["source_identity"], source["reference"]["sha256"],
                            source["candidate"]["sha256"],
                        )
                        for source in state["sources"]
                    ),
                    descriptor, target, state_proof,
                    state["region_manifest"]["sha256"],
                    state["configuration_manifest"]["sha256"],
                    whole_proof, focused_proof,
                    TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256,
                    index["selector_version"], index["renderer"]["version"],
                    index["dependency"]["digest"], material_payload, expected,
                )
                key = FocusCacheKey.build(context.to_payload())
                metadata = FocusCacheMetadata(
                    1, context.to_payload(), canonical_payload(target), canonical_payload(expected)
                )
                validation_cache = cache
            else:
                key = None
                metadata = UncachedFocusMetadata(
                    canonical_payload(target), canonical_payload(expected), material_payload,
                )
                validation_cache = None
            expected_files = []
            target_root = workspace / "focused-renders" / f"{target.rank:03d}-{target.region_key}"
            input_root = workspace / "focused-inputs" / f"{target.rank:03d}-{target.region_key}"

            def materialize_focus_inputs(
                *, state=state, input_root=input_root,
            ) -> dict[str, object]:
                if os.path.lexists(input_root):
                    _remove_workspace_owned_tree(
                        workspace, input_root, "focused input snapshot root"
                    )
                _safe_workspace_mkdir(
                    workspace, input_root, "focused input snapshot root"
                )

                def copy_proof(proof, destination: Path, *, root=workspace) -> Path:
                    source = root / PurePosixPath(proof["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _focused_copy_file_no_follow(
                        source, destination, self.cancel_event, contained_root=root,
                    )
                    _size, digest = _focused_file_proof(
                        destination, self.cancel_event, contained_root=input_root,
                    )
                    if digest != proof["sha256"]:
                        raise CandidateBuildError(
                            "focused input changed while snapshotting", stage="focused-render"
                        )
                    return destination

                before_paths = []
                after_paths = []
                for source in state["sources"]:
                    relative = PurePosixPath(source["source_identity"])
                    before_paths.append(copy_proof(
                        source["reference"], input_root / "reference" / relative
                    ))
                    after_paths.append(copy_proof(
                        source["candidate"], input_root / "candidate" / relative
                    ))
                region_path = copy_proof(
                    state["region_manifest"], input_root / "control/region.json"
                )
                configuration_path = copy_proof(
                    state["configuration_manifest"],
                    input_root / "control/configuration.json",
                )
                renderer_source = self.config.repo_root / "render_previews.py"
                renderer_proof = {
                    "path": "render_previews.py",
                    "sha256": index["renderer"]["digest"],
                }
                renderer_path = copy_proof(
                    renderer_proof, input_root / "control/render_previews.py",
                    root=self.config.repo_root,
                )
                animation_before = animation_after = None
                if index["animation"]["reference"] is not None:
                    animation_before = copy_proof(
                        index["animation"]["reference"],
                        input_root / "animation/reference.smd",
                    )
                    animation_after = copy_proof(
                        index["animation"]["candidate"],
                        input_root / "animation/candidate.smd",
                    )
                return {
                    "before": tuple(before_paths), "after": tuple(after_paths),
                    "region": region_path, "configuration": configuration_path,
                    "renderer": renderer_path,
                    "animation_before": animation_before,
                    "animation_after": animation_after,
                }

            def render_fresh(
                *, target=target, state=state, target_root=target_root,
                expected_files=expected_files,
            ) -> FocusRenderDirectories:
                inputs = materialize_focus_inputs()
                if os.path.lexists(target_root):
                    _remove_workspace_owned_tree(
                        workspace, target_root, "focused render root"
                    )
                _safe_workspace_mkdir(
                    workspace, target_root, "focused render root"
                )
                command: list[str] = list(self._blender_command(
                    "--background", "--python",
                    str(inputs["renderer"]), "--",
                ))
                for source in inputs["before"]:
                    command.extend(("--before", str(source)))
                for source in inputs["after"]:
                    command.extend(("--after", str(source)))
                pose_arg = ",".join(
                    "bind:0" if pose == "bind" else f"{pose}:{state_proof.selected_frame}"
                    for pose in poses
                )
                command.extend((
                    "--out", str(target_root), "--size", "512",
                    "--passes", "textured,clay", "--poses", pose_arg,
                    "--region-manifest", str(inputs["region"]),
                    "--source-root", str(input_root / "reference"),
                    "--configuration-manifest", str(inputs["configuration"]),
                    "--texture-cache", str(self._texture_cache_root(candidate)),
                    "--focus-region", target.region_key,
                ))
                for root in self._materials_roots():
                    command.extend(("--materials-root", str(root)))
                if inputs["animation_before"] is not None:
                    command.extend((
                        "--animation-before", str(inputs["animation_before"]),
                        "--animation-after", str(inputs["animation_after"]),
                    ))
                process = run_process(
                    command, cwd=self.config.repo_root,
                    log_path=workspace / "logs" / f"focused-render-{target.rank:03d}.log",
                    cancel_event=self.cancel_event,
                )
                if process.returncode != 0:
                    raise CandidateBuildError("focused render failed", stage="focused-render")
                directories = FocusRenderDirectories(
                    target_root / "original", target_root / "optimized"
                )
                expected_files.extend(_focused_render_file_manifest(
                    directories, self.cancel_event
                ))
                return directories

            result, record = validate_focused_target(
                validation_cache, key, target, focused_profile, render_fresh,
                workspace / "focused-snapshots" / f"{target.rank:03d}-{target.region_key}",
                metadata, expected_files, self.cancel_event,
            )
            results[target.region_key] = result
            records.append(record)
            _check_cancelled(self.cancel_event, "cancelled before focused partial evidence")
            _safe_workspace_atomic_json(workspace, partial_path, {
                "schema": "focused-progress-v1",
                "family_id": manifest.family_id,
                "candidate_id": candidate.spec.candidate_id,
                "completed": len(records),
                "selected": len(selection.selected),
                "records": tuple(canonical_payload(item) for item in records),
            }, "focused partial evidence")
        evidence_context = FocusedEvidenceContext(
            1, manifest.family_id, candidate.spec.candidate_id, policy,
            canonical_payload(whole_proof), canonical_payload(focused_proof),
            TRUSTED_CALIBRATION_EVIDENCE_V3_SHA256,
            index["dependency"]["digest"], material_proofs,
        )
        _check_cancelled(self.cancel_event, "cancelled before focused evidence publication")
        evidence = focused_gate_evidence_payload(
            evidence_context, selection, tuple(records), recoveries=()
        )
        validate_focused_gate_evidence_payload(
            evidence, family_id=manifest.family_id,
            candidate_id=candidate.spec.candidate_id,
            eligible_targets=selection.eligible_ranking,
            targets=selection.selected, regions=results,
        )
        _safe_workspace_atomic_json(
            workspace, final_path, evidence, "focused authoritative evidence"
        )
        try:
            published_evidence = json.loads(_read_regular_no_follow(
                final_path, self.cancel_event, contained_root=workspace,
            ).decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CandidateBuildError(
                "focused evidence cannot be read safely", stage="focused-render"
            ) from exc
        validate_focused_gate_evidence_payload(
            published_evidence, family_id=manifest.family_id,
            candidate_id=candidate.spec.candidate_id,
            eligible_targets=selection.eligible_ranking,
            targets=selection.selected, regions=results,
        )
        self._remove_evidence_file(workspace, partial_path)
        focused_validation = _aggregate_visual_results(tuple(
            (target.region_key, results[target.region_key].validation)
            for target in selection.selected
        ))
        self._focused_runtime[workspace] = (
            evidence_context, selection, tuple(records), deep_freeze(published_evidence)
        )
        return FocusedGateResult(
            focused_validation, selection.selected, results,
            published_evidence["evidence_sha256"],
        )

    def _seed_recovery_focus_index(
        self,
        base: CandidateBuild,
        candidate: CandidateBuild,
        cache_digest: str,
    ) -> None:
        base_workspace = base.workspace.resolve(strict=True)
        candidate_workspace = candidate.workspace.resolve(strict=True)
        seal = self._whole_index_seals.get(base_workspace)
        if seal is None:
            raise CandidateBuildError(
                "base whole index is unavailable for recovery", stage="focused-recovery"
            )
        payload = json.loads(canonical_json(_load_whole_visual_index(
            base_workspace / "logs/whole-visual-index.json", base_workspace,
            seal, self.cancel_event,
        )))
        copied: set[str] = set()

        def visit(value: object) -> None:
            if type(value) is dict:
                if set(value) == {"path", "sha256"}:
                    relative = _whole_relative(value["path"], "recovery index proof")
                    source = base_workspace / PurePosixPath(relative)
                    destination = candidate_workspace / PurePosixPath(relative)
                    if relative.startswith("src/"):
                        if not destination.is_file():
                            raise CandidateBuildError(
                                "composed recovery source is missing", stage="focused-recovery"
                            )
                        _size, value["sha256"] = _focused_file_proof(
                            destination, self.cancel_event,
                            contained_root=candidate_workspace,
                        )
                    elif relative not in copied:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if os.path.lexists(destination):
                            if _is_reparse(destination) or not destination.is_file():
                                raise CandidateBuildError(
                                    "recovery index destination is unsafe", stage="focused-recovery"
                                )
                            destination.unlink()
                        _focused_copy_file_no_follow(
                            source, destination, self.cancel_event,
                            contained_root=base_workspace,
                        )
                        _size, digest = _focused_file_proof(
                            destination, self.cancel_event,
                            contained_root=candidate_workspace,
                        )
                        if digest != value["sha256"]:
                            raise CandidateBuildError(
                                "recovery focus seed changed while copying", stage="focused-recovery"
                            )
                        copied.add(relative)
                    return
                for item in value.values():
                    visit(item)
            elif type(value) is list:
                for item in value:
                    visit(item)

        visit(payload)
        payload["candidate"] = {
            "candidate_id": candidate.spec.candidate_id,
            "spec": candidate.spec.cache_payload(),
            "cache_digest": cache_digest,
        }
        index_path = candidate_workspace / "logs/whole-visual-index.json"
        new_seal = _write_whole_visual_index(
            index_path, candidate_workspace, payload, self.cancel_event
        )
        self._whole_index_seals[candidate_workspace] = new_seal

    @staticmethod
    def _compile_file_proofs(
        manifest: FamilyManifest,
        build: CandidateBuild,
        cancel_event: threading.Event,
    ) -> tuple[CompileFileProof, ...]:
        return _current_recovery_compile_files(manifest, build, cancel_event)

    @staticmethod
    def _structural_recovery_evidence(
        cache_digest: str,
        composition_sha256: str,
        compile_sha256: str,
        manifest: FamilyManifest,
        validation: ValidationResult,
    ) -> StructuralAuthorizationEvidence:
        fingerprint_sha = hashlib.sha256(
            canonical_json(canonical_payload(manifest.fingerprint)).encode("utf-8")
        ).hexdigest()
        payload = {
            "candidate_cache_digest": cache_digest,
            "composition_evidence_sha256": composition_sha256,
            "compile_manifest_sha256": compile_sha256,
            "fingerprint_sha256": fingerprint_sha,
            "validation": validation_result_payload(validation),
        }
        evidence_sha = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        return StructuralAuthorizationEvidence(
            cache_digest, composition_sha256, compile_sha256, fingerprint_sha,
            validation, evidence_sha,
        )

    @staticmethod
    def _recovery_visual_infrastructure_failure(
        gate: str, message: str
    ) -> ValidationResult:
        metrics = {name: 0.0 for name in REQUIRED_METRICS}
        metrics["fidelity_score"] = 0.0
        return ValidationResult(
            False,
            (GateFailure(gate, "focused-recovery", None, "successful-stage", message),),
            metrics,
            "focused-recovery",
        )

    def recover_focused_candidate(
        self,
        *,
        manifest: FamilyManifest,
        control_build: CandidateBuild,
        base_build: CandidateBuild,
        base_evaluation: CandidateEvaluation,
        recipe,
        spec: CandidateSpec,
        cache_key: CacheKey,
        snapshots_by_sha256: Mapping[str, RecoverySourceSnapshot],
        workspace: Path,
        whole_profile: FidelityProfile,
        focused_profile: FidelityProfile,
        policy: FocusedRegionPolicy,
        structural_validator,
        tools: CandidateTools,
        prior_recoveries,
        cancel_event: threading.Event,
    ) -> FocusedRecoveryAdapterResult:
        try:
            composed = compose_candidate_sources(
                base_build, recipe, snapshots_by_sha256, workspace, cancel_event
            )
            original_qcs = _matching_qcs(
                manifest.source_dir, manifest.model_rel, optimized=False
            )
            if len(original_qcs) != 1:
                raise ValueError("recovery original QC is missing or ambiguous")
            _validate_complete_recovery_graph_pairing(
                parse_qc_graph(original_qcs[0], manifest.source_dir),
                parse_qc_graph(composed.optimized_qc, composed.workspace / "src"),
            )
        except ProcessCancelledError:
            raise
        except Exception:
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "composition_failed", recipe, None, (), (),
                (), None, (), None,
            )
            return FocusedRecoveryAdapterResult(None, None, evidence, None)
        try:
            build = _compile_composed_candidate(
                manifest, spec, composed.workspace, composed.optimized_qc,
                tools, cancel_event,
            )
        except ProcessCancelledError:
            raise
        except Exception:
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "compile_failed", recipe,
                composed.composition, composed.composition.changed_sources,
                (), (), None, (), None,
            )
            return FocusedRecoveryAdapterResult(None, None, evidence, None)

        try:
            compile_files = self._compile_file_proofs(manifest, build, cancel_event)
            compile_sha = compile_manifest_sha256(compile_files)
            structural = structural_validator(manifest, build)
            structural_evidence = self._structural_recovery_evidence(
                cache_key.digest, composed.composition.evidence_sha256,
                compile_sha, manifest, structural,
            )
        except ProcessCancelledError:
            raise
        except Exception:
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "compile_failed", recipe,
                composed.composition, composed.composition.changed_sources,
                (), (), None, (), None,
            )
            return FocusedRecoveryAdapterResult(None, None, evidence, None)
        if not structural.passed:
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "structural_failed", recipe,
                composed.composition, composed.composition.changed_sources,
                (), compile_files, structural_evidence, (), None,
            )
            return FocusedRecoveryAdapterResult(None, None, evidence, None)

        try:
            self.bind_candidate_cache_digest(build, cache_key.digest)
            self._seed_recovery_focus_index(base_build, build, cache_key.digest)
            focused_gate = self.focused_visual(
                manifest, control_build, build, whole_profile, focused_profile, policy
            )
            base_runtime = self._focused_runtime.get(base_build.workspace.resolve())
            candidate_runtime = self._focused_runtime.get(build.workspace.resolve())
            if base_runtime is None or candidate_runtime is None:
                raise CandidateBuildError(
                    "typed focused runtime is unavailable", stage="focused-recovery"
                )
        except ProcessCancelledError:
            raise
        except Exception as exc:
            failed_structural = self._recovery_visual_infrastructure_failure(
                "focused-recovery-infrastructure", str(exc)
            )
            failed_evidence = self._structural_recovery_evidence(
                cache_key.digest, composed.composition.evidence_sha256,
                compile_sha, manifest, failed_structural,
            )
            try:
                self._remove_evidence_file(
                    build.workspace,
                    build.workspace / "logs/focused-region-gate.json",
                )
            except Exception:
                pass
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "structural_failed", recipe,
                composed.composition, composed.composition.changed_sources,
                (), compile_files, failed_evidence, (), None,
            )
            return FocusedRecoveryAdapterResult(None, None, evidence, None)
        # Composite schema-1 output is an intermediate input only. It must never
        # remain authoritative; only terminal schema 2 may be published.
        self._remove_evidence_file(
            build.workspace, build.workspace / "logs/focused-region-gate.json"
        )
        base_context, base_selection, base_records, base_payload = base_runtime
        candidate_context, candidate_selection, candidate_records, _candidate_payload = candidate_runtime
        if candidate_selection.selected != base_selection.selected:
            raise CandidateBuildError(
                "recovery changed the trusted focus selection", stage="focused-recovery"
            )
        artifact_root = self._materialize_recovery_focused_artifacts(
            build.workspace, recipe.round_index, tuple(candidate_records),
            tuple(prior_recoveries), cancel_event,
        )
        if not focused_gate.validation.passed:
            evidence = build_focused_recovery_evidence(
                recipe.round_index, "focused_failed", recipe,
                composed.composition, composed.composition.changed_sources,
                (), compile_files, structural_evidence, candidate_records, None,
            )
            self._recovery_artifact_roots[evidence.evidence_sha256] = artifact_root
            size = _family_snapshot(
                scan_compiled_models(build.compiled_models_dir), manifest.model_rel
            )
            diagnostic = CandidateEvaluation(
                spec, size, structural,
                _aggregate_focused_gate(base_evaluation.whole_visual, focused_gate),
                build.compiled_models_dir, base_evaluation.whole_visual,
                focused_gate.regions,
            )
            return FocusedRecoveryAdapterResult(None, diagnostic, evidence, None)

        # One and only one fresh whole render for the terminal recovery round.
        try:
            final_whole = self.visual(
                manifest, control_build, build, whole_profile,
                focused_profile=focused_profile,
            )
        except ProcessCancelledError:
            raise
        except Exception as exc:
            final_whole = self._recovery_visual_infrastructure_failure(
                "final-whole-infrastructure", str(exc)
            )
        workspace_key = build.workspace.resolve()
        whole_index_path = build.workspace / "logs/whole-visual-index.json"
        whole_index_sha = self._whole_index_seals.get(workspace_key)
        if final_whole.passed and whole_index_sha is None:
            final_whole = self._recovery_visual_infrastructure_failure(
                "final-whole-index-missing",
                "passing final whole produced no sealed index",
            )
        if not final_whole.passed:
            # A failed whole gate has no authoritative passing index. Publish a
            # bounded diagnostic proof so the failed round remains byte-bound.
            self._whole_index_seals.pop(workspace_key, None)
            diagnostic = {
                "schema": 1,
                "candidate_id": spec.candidate_id,
                "validation": validation_result_payload(final_whole),
            }
            _safe_workspace_atomic_json(
                build.workspace, whole_index_path, diagnostic,
                "failed final whole diagnostic",
            )
            whole_index_sha = _sha256_file(whole_index_path, cancel_event)
        whole_render_sha = hashlib.sha256(canonical_json({
            "whole_index_sha256": whole_index_sha,
            "validation": validation_result_payload(final_whole),
        }).encode("utf-8")).hexdigest()
        final_evidence = build_final_whole_authorization_evidence(
            spec.candidate_id, cache_key.digest, recipe.recipe_sha256,
            composed.composition.evidence_sha256, compile_sha,
            "logs/whole-visual-index.json", whole_index_sha,
            whole_render_sha, final_whole,
        )
        terminal_status = "authorized" if final_whole.passed else "final_whole_failed"
        # visual() clears focused runtime/evidence while producing the final whole;
        # retain the already sealed focused records for schema-2 folding.
        self._focused_runtime[workspace_key] = candidate_runtime
        current = build_focused_recovery_evidence(
            recipe.round_index, terminal_status, recipe,
            composed.composition, composed.composition.changed_sources,
            (), compile_files, structural_evidence, candidate_records,
            final_evidence,
        )
        self._recovery_artifact_roots[current.evidence_sha256] = artifact_root
        if not final_whole.passed:
            return FocusedRecoveryAdapterResult(None, None, current, None)

        refs = tuple(
            FocusedEvidenceRef(record.target.region_key, record.evidence_sha256)
            for record in candidate_records
        )
        snapshot = build_recovery_source_snapshot(
            kind="candidate", family_id=manifest.family_id,
            family_input_sha256=manifest.input_hash,
            optimizer_contract_sha256=recipe.optimizer_contract_sha256,
            whole_profile_sha256=recipe.whole_profile_sha256,
            focused_profile_sha256=recipe.focused_profile_sha256,
            dependency_proof_sha256=recipe.dependency_proof_sha256,
            candidate_id=spec.candidate_id,
            candidate_cache_digest=cache_key.digest,
            source_root=build.workspace / "src",
            source_manifest=composed.source_manifest,
            focused_evidence=refs,
        )
        build = replace(build, source_snapshot=snapshot)
        aggregate = _aggregate_focused_gate(final_whole, focused_gate)
        size = _family_snapshot(
            scan_compiled_models(build.compiled_models_dir), manifest.model_rel
        )
        evaluation = CandidateEvaluation(
            spec, size, structural, aggregate, build.compiled_models_dir,
            final_whole, focused_gate.regions,
        )
        context = FocusedRecoveryContext(
            2, base_context, base_build.source_snapshot.candidate_cache_digest,
            str(base_payload["authorization_sha256"]),
        )
        all_recoveries = (*tuple(prior_recoveries), current)
        authorization = focused_recovery_evidence_payload(
            context, base_selection, base_records, all_recoveries
        )
        validate_focused_gate_evidence_payload(
            authorization, family_id=manifest.family_id,
            candidate_id=spec.candidate_id,
            eligible_targets=base_selection.eligible_ranking,
            targets=base_selection.selected, regions=focused_gate.regions,
            recovery_context=context, initial_records=base_records,
            recoveries=all_recoveries,
        )
        return FocusedRecoveryAdapterResult(
            build, evaluation, current, authorization,
            context, tuple(base_records), base_selection, tuple(all_recoveries),
        )

    def tool_versions(self) -> Mapping[str, str]:
        def digest(path: Path) -> str:
            try:
                return _sha256_file(path, self.cancel_event)
            except OSError:
                return "missing"
        return {
            "optimizer": "maximum-v1",
            "blender_sha256": digest(self.config.blender_path),
            "studiomdl_sha256": digest(self.config.studiomdl_path),
            "meshoptimizer_preferred": str(MESHOPT_ENGINE_PREFERRED).lower(),
            "profile_sha256": digest(self.config.profile_path),
            "meshopt_bridge_sha256": digest(self.config.repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll"),
            "candidate_adapter_sha256": digest(self.config.repo_root / "maximum_optimizer" / "candidates.py"),
            "render_previews_sha256": digest(self.config.repo_root / "render_previews.py"),
            "vtfcmd_sha256": digest(self._vtfcmd()) if self._vtfcmd() is not None else "missing",
        }


def _resolved_blender(raw: object) -> Path:
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    candidates = (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation" / "Blender 5.0" / "blender.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation" / "Blender 4.3" / "blender.exe",
    )
    found = next((path.resolve() for path in candidates if path.is_file()), None)
    if found is None:
        raise MaximumConfigError("Blender is required for Maximum mode")
    return found


def run_maximum_from_existing_args(
    args: Any,
    *,
    repo_root: Path,
    addon_path: Path,
    out_addon_dir: Path,
    work_dir: Path,
) -> int:
    """Bridge the established Models CLI inputs into the isolated Maximum pipeline."""
    try:
        profile_path = Path(
            getattr(args, "maximum_profile", None)
            or repo_root / "maximum_optimizer" / "profiles" / "maximum-experimental-v1.json"
        ).expanduser().resolve()
        # Fail closed before decompilation or output changes.
        load_fidelity_profile_set(profile_path)
        blender = _resolved_blender(getattr(args, "blender", None))
        studiomdl_raw = getattr(args, "studiomdl", None)
        if studiomdl_raw:
            studiomdl = Path(studiomdl_raw).expanduser().resolve()
        else:
            from batch_compile_opt_qc import DEFAULT_STUDIOMDL
            studiomdl = Path(DEFAULT_STUDIOMDL).expanduser().resolve()
        if not blender.is_file() or not studiomdl.is_file():
            raise MaximumConfigError("Maximum Blender/StudioMDL tools were not found")

        budget = SearchBudget(
            max_candidates=getattr(args, "maximum_max_candidates", 18),
            min_ratio_step=getattr(args, "maximum_min_ratio_step", 0.025),
            min_marginal_saving=getattr(args, "maximum_min_marginal_saving", 0.005),
        )
        parallelism = resolve_maximum_parallelism(
            int(getattr(args, "maximum_jobs", 0)),
            memory=detect_memory_snapshot(),
        )
        config = MaximumRunConfig(
            addon_path,
            out_addon_dir,
            work_dir,
            blender,
            studiomdl,
            repo_root,
            budget,
            profile_path,
            bool(getattr(args, "maximum_resume", False) or getattr(args, "resume_opt", False)),
            bool(getattr(args, "overwrite", False)),
            parallelism,
        )
        validate_run_paths(config, create=False)
        _recover_output_transaction(config.output_dir)
        required_runtime = (
            repo_root / "batch_decompile_organize.py",
            repo_root / "batch_optimize_qc.py",
            repo_root / "batch_optimize_round_parts_policy.py",
            repo_root / "batch_optimize_maximum.py",
            repo_root / "batch_compile_opt_qc.py",
            repo_root / "render_previews.py",
            repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll",
        )
        missing_runtime = [path.name for path in required_runtime if not path.is_file() or _is_reparse(path)]
        if missing_runtime:
            raise MaximumConfigError(
                f"Maximum runtime dependencies are missing or unsafe: {sorted(missing_runtime)}"
            )

        logs = work_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        decompile_script = repo_root / "batch_decompile_organize.py"
        result = run_process(
            (
                sys.executable,
                str(decompile_script),
                str(addon_path),
                "--out",
                str(work_dir),
                "--force",
                "--jobs",
                str(getattr(args, "decompile_jobs", 1)),
            ),
            cwd=repo_root,
            log_path=logs / "maximum_decompile.log",
            cancel_event=threading.Event(),
        )
        if result.returncode != 0:
            return 1
        manifest_path = logs / "decompile_manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if type(payload) is not dict or int(payload.get("total", 0)) <= 0:
            raise MaximumConfigError("decompile produced no model families")

        import selective_policy_models
        selective_policy_models.write_final_policy_files(
            addon_path=addon_path,
            decompile_results=list(payload.get("results", ())),
            src_root=work_dir / "src",
            logs_dir=logs,
        )
        report = run_maximum_addon(config)
        return 0 if report.status == "success" else 1
    except (MaximumConfigError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Maximum configuration/input error: {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"[ERROR] Maximum run failed: {exc}", flush=True)
        return 1
