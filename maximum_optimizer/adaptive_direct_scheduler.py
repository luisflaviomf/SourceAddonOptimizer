from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import os
from pathlib import Path
import threading
import uuid
from typing import Callable

from .adaptive_direct_evidence import (
    AdaptiveDirectEvidence,
    build_adaptive_direct_evidence,
)
from .candidates import CandidateBuild
from .composite import (
    AdaptiveDirectSourceUnionRecord, _has_reparse_ancestor,
    _hash_current_file, _safe_tree_files,
)
from .domain import (
    AdaptiveDirectCoverageManifest, ArtifactStat,
    CandidateEvaluation,
    CompiledSizeSnapshot, CompositeRecipe,
    ComposedSourceTree,
    StructuralAuthorizationEvidence,
    validation_result_payload,
)
from .focused_cache import (
    FinalWholeAuthorizationEvidence, FocusedRenderEvidence,
    _copy_file_no_follow,
)
from .monaco_schedule import (
    MonacoCancelledReservation,
    MonacoFailedReservation,
    MonacoScheduledCandidate,
    MONACO_DIRECT_RATIOS,
    build_monaco_schedule,
)
from .monaco_selection import (
    RetainedMonacoBaseProof, require_current_retained_monaco_base,
)
from .processes import ProcessCancelledError
from .production_adapters import (
    AdaptiveDirectCompileResult, require_current_composed_source_tree,
)
from .reporting import canonical_json
from .search import select_winner
from .source_union import _quarantine_cleanup_if_owned, _workspace_root_identity


def _evaluation_attempt_payload(value: CandidateEvaluation | None):
    if value is None:
        return None
    return {
        "candidate_id": value.spec.candidate_id,
        "compiled_models_dir": str(Path(value.compiled_models_dir)),
        "size": {
            "root": str(Path(value.size.root)),
            "total_bytes": value.size.total_bytes,
            "bytes_by_kind": dict(value.size.bytes_by_kind),
            "vertices_by_lod": dict(value.size.vertices_by_lod),
            "artifacts": [{
                "relative_path": item.relative_path, "kind": item.kind,
                "size_bytes": item.size_bytes,
                "lod_vertices": list(item.lod_vertices),
            } for item in value.size.artifacts],
        },
        "structural": validation_result_payload(value.structural),
        "visual": validation_result_payload(value.visual),
        "whole_visual": validation_result_payload(value.whole_visual),
        "focused": [{
            "region_key": key,
            "evidence_sha256": item.evidence_sha256,
            "cache_hit": item.cache_hit,
        } for key, item in sorted(value.focused_by_region.items())],
    }


def _attempt_payload(value) -> dict[str, object]:
    return {
        "schema": 1, "ratio": float(value.ratio), "status": value.status,
        "candidate_id": value.candidate_id,
        "recipe_sha256": None if value.recipe is None else value.recipe.recipe_sha256,
        "evaluation": _evaluation_attempt_payload(value.evaluation),
        "build": None if value.build is None else {
            "candidate_id": value.build.spec.candidate_id,
            "compiled_models_dir": str(Path(value.build.compiled_models_dir)),
        },
        "evidence_sha256": (
            None if value.evidence is None else value.evidence.evidence_sha256
        ),
        "error": value.error,
    }


def require_adaptive_direct_source_count(
    sources, *, before_io: Callable[[], object] | None = None,
):
    """Fail closed on the exact-fallback cardinality before an I/O continuation."""
    eligible = tuple(
        item for item in tuple(sources)
        if (
            getattr(item, "eligibility_kind", None)
            or getattr(item, "kind", None)
        ) == "eligible-exact-v1"
    )
    if not 1 <= len(eligible) <= 8:
        raise ValueError("adaptive-direct requires one through eight eligible sources")
    if before_io is not None:
        if not callable(before_io):
            raise TypeError("adaptive-direct I/O continuation is invalid")
        return before_io()
    return tuple(item.source_identity for item in eligible)


@dataclass(frozen=True)
class AdaptiveDirectExecutionAttempt:
    ratio: float
    status: str
    candidate_id: str | None = None
    evaluation: CandidateEvaluation | None = None
    build: CandidateBuild | None = None
    evidence: AdaptiveDirectEvidence | None = None
    error: str = ""
    recipe: CompositeRecipe | None = None
    attempt_sha256: str = ""

    def __post_init__(self) -> None:
        statuses = {
            "schedule_failed", "composition_failed", "compile_failed",
            "structural_failed", "focused_failed", "final_whole_failed",
            "authorized", "cancelled",
        }
        if (
            self.status not in statuses
            or isinstance(self.ratio, bool)
            or type(self.ratio) not in (int, float)
            or not math.isfinite(float(self.ratio))
            or float(self.ratio) not in MONACO_DIRECT_RATIOS
            or (self.candidate_id is not None and (
                type(self.candidate_id) is not str or not self.candidate_id
            ))
            or type(self.error) is not str or len(self.error) > 2048
            or (self.evaluation is not None and not isinstance(
                self.evaluation, CandidateEvaluation
            ))
            or (self.build is not None and not isinstance(self.build, CandidateBuild))
            or (self.evidence is not None and not isinstance(
                self.evidence, AdaptiveDirectEvidence
            ))
        ):
            raise ValueError("adaptive-direct attempt identity is invalid")
        if self.recipe is not None and (
            not isinstance(self.recipe, CompositeRecipe)
            or self.recipe.kind != "adaptive-direct-fallback-v1"
            or self.recipe.round_index != 0
        ):
            raise ValueError("adaptive-direct attempt recipe is invalid")
        authorized = self.status == "authorized"
        if authorized != all((
            self.candidate_id is not None, self.evaluation is not None,
            self.build is not None, self.evidence is not None,
        )):
            raise ValueError("adaptive-direct authorized attempt matrix is invalid")
        if self.evidence is not None and self.evidence.terminal_status != self.status:
            raise ValueError("adaptive-direct attempt/evidence status differs")
        if self.evaluation is not None and not authorized:
            raise ValueError("non-authorized adaptive-direct attempt exposed an evaluation")
        expected_id = (
            None if self.recipe is None else "recovery-" + self.recipe.recipe_sha256
        )
        if self.recipe is not None and self.candidate_id != expected_id:
            raise ValueError("adaptive-direct attempt candidate/recipe differs")
        if self.evidence is not None and (
            self.recipe is None or self.evidence.recipe != self.recipe
        ):
            raise ValueError("adaptive-direct attempt evidence/recipe differs")
        if self.build is not None and (
            self.candidate_id is None or self.build.spec.candidate_id != self.candidate_id
            or self.build.spec.composite_recipe != self.recipe
        ):
            raise ValueError("adaptive-direct attempt build identity differs")
        if self.evaluation is not None and (
            self.build is None or self.evaluation.spec != self.build.spec
            or Path(self.evaluation.compiled_models_dir) != Path(self.build.compiled_models_dir)
        ):
            raise ValueError("adaptive-direct attempt evaluation/build differs")
        if self.evaluation is not None:
            files = self.evidence.compile_files
            artifacts = tuple(
                (item.relative_path, item.kind, item.size)
                for item in files
            )
            size_artifacts = tuple(
                (item.relative_path, item.kind, item.size_bytes)
                for item in self.evaluation.size.artifacts
            )
            expected_by_kind = {}
            for item in files:
                expected_by_kind[item.kind] = expected_by_kind.get(item.kind, 0) + item.size
            if (
                Path(self.evaluation.size.root) != Path(self.build.compiled_models_dir)
                or self.evaluation.size.total_bytes != sum(item.size for item in files)
                or size_artifacts != artifacts
                or dict(self.evaluation.size.bytes_by_kind) != dict(sorted(expected_by_kind.items()))
                or dict(self.evaluation.size.vertices_by_lod)
                or self.evaluation.structural != self.evidence.structural.validation
                or self.evaluation.visual != self.evidence.final_whole.validation
                or self.evaluation.whole_visual != self.evidence.final_whole.validation
                or self.evaluation.focused_by_region
            ):
                raise ValueError("adaptive-direct authorized evaluation differs from evidence")
        if self.status not in {"schedule_failed", "cancelled"} and self.recipe is None:
            raise ValueError("adaptive-direct scheduled attempt has no recipe")
        expected_seal = hashlib.sha256(
            canonical_json(_attempt_payload(self)).encode("utf-8")
        ).hexdigest()
        if self.attempt_sha256 != expected_seal:
            raise ValueError("adaptive-direct attempt seal mismatch")

    @classmethod
    def create(cls, ratio: float, status: str, **values):
        values["error"] = str(values.get("error", "")).strip()[:2048]
        raw = dict(ratio=ratio, status=status, attempt_sha256="", **values)
        provisional = object.__new__(cls)
        for name, value in raw.items():
            object.__setattr__(provisional, name, value)
        raw["attempt_sha256"] = hashlib.sha256(
            canonical_json(_attempt_payload(provisional)).encode("utf-8")
        ).hexdigest()
        return cls(**raw)


@dataclass(frozen=True)
class AdaptiveDirectScheduleExecution:
    base: CandidateEvaluation
    selected: CandidateEvaluation
    attempts: tuple[AdaptiveDirectExecutionAttempt, ...]
    cancelled: bool

    def __post_init__(self) -> None:
        attempts = tuple(self.attempts)
        if (
            not isinstance(self.base, CandidateEvaluation)
            or not isinstance(self.selected, CandidateEvaluation)
            or type(self.cancelled) is not bool
            or any(not isinstance(item, AdaptiveDirectExecutionAttempt) for item in attempts)
        ):
            raise TypeError("adaptive-direct execution result is invalid")
        authorized = tuple(
            item.evaluation for item in attempts if item.status == "authorized"
        )
        if self.selected not in (self.base, *authorized):
            raise ValueError("adaptive-direct selected evaluation was not authorized")
        if self.cancelled != any(item.status == "cancelled" for item in attempts):
            raise ValueError("adaptive-direct execution cancellation matrix differs")
        if self.selected is not select_winner([self.base, *authorized]):
            raise ValueError("adaptive-direct provisional ranking differs")
        object.__setattr__(self, "attempts", attempts)


def _overlaps(left: Path, right: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.abspath(left), os.path.abspath(right)))
    except ValueError:
        return False
    return os.path.normcase(common) in {
        os.path.normcase(os.path.abspath(left)), os.path.normcase(os.path.abspath(right)),
    }


def _validate_workspace_root(root: Path, base_build: CandidateBuild) -> Path:
    root = Path(root)
    if not root.is_absolute() or os.path.lexists(root):
        raise ValueError("adaptive-direct workspace root must be absolute and fresh")
    parent = root.parent
    if not parent.is_dir() or _has_reparse_ancestor(parent):
        raise ValueError("adaptive-direct workspace parent is unavailable or unsafe")
    protected = (
        Path(base_build.workspace), Path(base_build.compiled_models_dir),
        Path(base_build.optimized_qc).parent,
    )
    snapshot = base_build.source_snapshot
    if snapshot is not None:
        protected += (Path(snapshot.source_root),)
    if any(_overlaps(root, item) for item in protected):
        raise ValueError("adaptive-direct workspace overlaps retained base")
    return root


def _cleanup_empty_owned_root(root: Path | None, identity) -> bool:
    if root is None or identity is None or not os.path.lexists(root):
        return False
    try:
        if _workspace_root_identity(root) != identity:
            return False
        with os.scandir(root) as entries:
            if next(entries, None) is not None:
                return False
    except (OSError, ValueError):
        return False
    return _quarantine_cleanup_if_owned(root, identity)


def _current_compile_files(
    compiled: AdaptiveDirectCompileResult, event: threading.Event,
) -> tuple:
    expected = compiled.compile_files
    root = Path(compiled.build.compiled_models_dir)
    paths = _safe_tree_files(root, event)
    if len(paths) != len(expected):
        raise ValueError("adaptive-direct current compile inventory differs")
    current = []
    by_path = {item.relative_path: item for item in expected}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        proof = by_path.get(relative)
        if proof is None:
            raise ValueError("adaptive-direct current compile contains an extra file")
        size, digest = _hash_current_file(path, root, event, max_bytes=proof.size)
        if (size, digest) != (proof.size, proof.sha256):
            raise ValueError("adaptive-direct current compile bytes differ")
        current.append(proof)
    result = tuple(current)
    if result != expected:
        raise ValueError("adaptive-direct current compile order differs")
    return result


def _size_from_compile_files(build: CandidateBuild, files) -> CompiledSizeSnapshot:
    by_kind = {}
    artifacts = []
    for item in files:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + item.size
        artifacts.append(ArtifactStat(item.relative_path, item.kind, item.size))
    return CompiledSizeSnapshot(
        Path(build.compiled_models_dir), sum(item.size for item in files),
        dict(sorted(by_kind.items())), {}, tuple(artifacts),
    )


def _require_compile_binding(
    outcome: MonacoScheduledCandidate,
    composed: ComposedSourceTree,
    compiled: AdaptiveDirectCompileResult,
    event: threading.Event,
) -> None:
    workspace = Path(composed.workspace)
    compiled_root = Path(compiled.build.compiled_models_dir)
    try:
        relative = compiled_root.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("adaptive-direct compiled root escapes reserved workspace") from exc
    if (
        compiled.composition_evidence_sha256 != composed.composition.evidence_sha256
        or compiled.build.spec != outcome.spec
        or Path(compiled.build.workspace) != workspace
        or Path(compiled.build.optimized_qc) != Path(composed.optimized_qc)
        or not relative.parts
        or _has_reparse_ancestor(compiled_root)
    ):
        raise ValueError("adaptive-direct compile/composition binding differs")
    _current_compile_files(compiled, event)


def _snapshot_authorized_compile(
    compiled: AdaptiveDirectCompileResult,
    event: threading.Event,
) -> AdaptiveDirectCompileResult:
    source_root = Path(compiled.build.compiled_models_dir)
    private_root = Path(compiled.build.workspace) / (
        ".authorized-compile-" + uuid.uuid4().hex
    )
    identity = None
    try:
        private_root.mkdir(exist_ok=False)
        identity = _workspace_root_identity(private_root)
        models_root = private_root / "models"
        models_root.mkdir()
        for proof in compiled.compile_files:
            if event.is_set():
                raise ProcessCancelledError(
                    "adaptive-direct cancelled during authorized compile snapshot"
                )
            source = source_root / Path(*proof.relative_path.split("/"))
            destination = models_root / Path(*proof.relative_path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_no_follow(
                source, destination, event, contained_root=source_root,
            )
        replacement_build = replace(
            compiled.build, compiled_models_dir=models_root,
        )
        replacement = AdaptiveDirectCompileResult.create(
            replacement_build, compiled.compile_files,
            compiled.composition_evidence_sha256,
        )
        _current_compile_files(replacement, event)
        if _workspace_root_identity(private_root) != identity:
            raise ValueError("adaptive-direct authorized compile ownership changed")
        if event.is_set():
            raise ProcessCancelledError(
                "adaptive-direct cancelled after authorized compile snapshot"
            )
        return replacement
    except BaseException:
        _quarantine_cleanup_if_owned(private_root, identity)
        raise


def execute_adaptive_direct_schedule(
    *,
    base_proof: RetainedMonacoBaseProof,
    coverage: AdaptiveDirectCoverageManifest,
    remaining_candidates: int,
    reserve_ratio: Callable[[int, float], str],
    access_ratio: Callable[[float], object],
    workspace_root: Path,
    compose_candidate: Callable,
    compile_candidate: Callable,
    authorize_structural: Callable,
    rerender_base_focus: Callable,
    render_source_union: Callable,
    authorize_final_whole: Callable,
    cancel_event: threading.Event | None = None,
) -> AdaptiveDirectScheduleExecution:
    """Execute only the fixed adaptive-direct terminal fallback schedule.

    Ratio slots are reserved by :func:`build_monaco_schedule` before its first
    ratio accessor is allowed to perform direct-source I/O.
    """
    event = cancel_event or threading.Event()
    pre_cancelled = event.is_set()
    if pre_cancelled:
        if not isinstance(base_proof, RetainedMonacoBaseProof):
            raise TypeError("adaptive-direct retained base proof is invalid")
        proof = base_proof
    else:
        proof = require_current_retained_monaco_base(base_proof, event)
    base_evaluation = proof.evaluation
    base_build = proof.build
    if (
        base_evaluation.spec != base_build.spec
        or not base_evaluation.passed
        or not base_evaluation.whole_visual.passed
        or not base_evaluation.focused_by_region
        or not all(
            item.validation.passed
            for item in base_evaluation.focused_by_region.values()
        )
        or base_evaluation.spec.strategy != "blender-adaptive-v1"
        or base_evaluation.spec.composite_recipe is not None
    ):
        raise ValueError("adaptive-direct scheduler base is not an approved ordinary adaptive candidate")
    if not isinstance(coverage, AdaptiveDirectCoverageManifest):
        raise TypeError("adaptive-direct scheduler coverage is invalid")
    if (
        coverage.metrics_proof != proof.metrics
        or coverage.state_inventory != proof.state_inventory
        or coverage.base_candidate_id != proof.candidate_id
        or coverage.base_cache_digest != proof.candidate_cache_digest
        or coverage.base_spec_sha256 != proof.base_spec_sha256
        or coverage.base_source_manifest_sha256 != proof.source_manifest_sha256
        or coverage.base_source_snapshot_sha256 != proof.source_snapshot_sha256
    ):
        raise ValueError("adaptive-direct coverage differs from retained base authority")
    for callback in (
        reserve_ratio, access_ratio, compose_candidate, compile_candidate,
        authorize_structural, rerender_base_focus, render_source_union,
        authorize_final_whole,
    ):
        if not callable(callback):
            raise TypeError("adaptive-direct scheduler callback is invalid")
    require_adaptive_direct_source_count(coverage.sources)
    if type(remaining_candidates) is not int or remaining_candidates < 0:
        raise ValueError("adaptive-direct remaining candidate budget is invalid")
    root = _validate_workspace_root(Path(workspace_root), base_build)
    reservation_root = None
    reservation_identity = None
    if remaining_candidates > 0 and not pre_cancelled:
        reservation_root = root.with_name(
            f".{root.name}.adaptive-reservation-{uuid.uuid4().hex}"
        )
        reservation_root.mkdir(exist_ok=False)
        reservation_identity = _workspace_root_identity(reservation_root)
    try:
        schedule = build_monaco_schedule(
            base_spec=base_evaluation.spec,
            coverage=coverage,
            remaining_candidates=remaining_candidates,
            reserve=reserve_ratio,
            access_ratio=access_ratio,
            cancel_event=event,
        )
    except BaseException:
        if reservation_root is not None:
            _cleanup_empty_owned_root(reservation_root, reservation_identity)
        raise
    if not event.is_set():
        try:
            proof = require_current_retained_monaco_base(base_proof, event)
        except BaseException:
            if reservation_root is not None:
                _cleanup_empty_owned_root(reservation_root, reservation_identity)
            raise
        if proof.evaluation is not base_evaluation or proof.build is not base_build:
            if reservation_root is not None:
                _cleanup_empty_owned_root(reservation_root, reservation_identity)
            raise ValueError("adaptive-direct retained authority changed during schedule")
    attempts: list[AdaptiveDirectExecutionAttempt] = []
    scheduled = tuple(
        outcome for outcome in schedule
        if isinstance(outcome, MonacoScheduledCandidate)
    )
    workspace_error = ""
    if scheduled:
        snapshots = tuple(
            snapshot for outcome in scheduled for snapshot in outcome.snapshots
        )
        if any(
            _overlaps(root, Path(snapshot.source_root))
            or _overlaps(root, Path(snapshot.input_source_root))
            for snapshot in snapshots
        ):
            workspace_error = "adaptive-direct workspace overlaps direct source inputs"
        elif reservation_root is None:
            workspace_error = "adaptive-direct workspace reservation is unavailable"
        else:
            try:
                with os.scandir(reservation_root) as entries:
                    reservation_not_empty = next(entries, None) is not None
                if (
                    _workspace_root_identity(reservation_root) != reservation_identity
                    or reservation_not_empty
                    or os.path.lexists(root)
                ):
                    raise ValueError("adaptive-direct workspace reservation changed")
                os.rename(reservation_root, root)
                reservation_root = None
                if _workspace_root_identity(root) != reservation_identity:
                    raise ValueError("adaptive-direct workspace ownership changed")
            except (OSError, ValueError) as exc:
                workspace_error = str(exc)
    if reservation_root is not None:
        _cleanup_empty_owned_root(reservation_root, reservation_identity)
        reservation_root = None
    for outcome in schedule:
        if isinstance(outcome, MonacoFailedReservation):
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, "schedule_failed", error=outcome.failure_reason,
            ))
            continue
        if isinstance(outcome, MonacoCancelledReservation):
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, "cancelled", error=outcome.failure_reason,
            ))
            continue
        if not isinstance(outcome, MonacoScheduledCandidate):
            raise TypeError("adaptive-direct schedule returned an invalid outcome")
        stage = "composition"
        recipe = outcome.spec.composite_recipe
        composed = compiled = structural = None
        base_records = direct_records = ()
        if workspace_error:
            evidence = build_adaptive_direct_evidence(
                terminal_status="composition_failed", recipe=recipe,
                composition=None, changed_sources=(), compile_files=(),
                structural=None, base_focus_records=(),
                direct_focus_records=(), final_whole=None,
            )
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, "composition_failed",
                candidate_id=outcome.spec.candidate_id,
                evidence=evidence, recipe=recipe, error=workspace_error,
            ))
            continue
        try:
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled before composition")
            workspace = root / outcome.spec.candidate_id
            composed = compose_candidate(outcome, workspace, event)
            composed = require_current_composed_source_tree(
                composed, workspace, recipe, event,
                base_build=base_build,
                snapshots_by_sha256={
                    item.snapshot_sha256: item for item in outcome.snapshots
                },
                coverage_manifest=coverage,
            )
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after composition")
            stage = "compile"
            compiled = compile_candidate(outcome, composed, event)
            if not isinstance(compiled, AdaptiveDirectCompileResult):
                raise TypeError("adaptive-direct compiler returned an invalid result")
            _require_compile_binding(outcome, composed, compiled, event)
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after compile")

            stage = "structural"
            structural = authorize_structural(outcome, composed, compiled, event)
            if not isinstance(structural, StructuralAuthorizationEvidence):
                raise TypeError("adaptive-direct structural authorization is invalid")
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after structural")
            if not structural.validation.passed:
                evidence = build_adaptive_direct_evidence(
                    terminal_status="structural_failed",
                    recipe=outcome.spec.composite_recipe,
                    composition=composed.composition,
                    changed_sources=composed.composition.changed_sources,
                    compile_files=compiled.compile_files,
                    structural=structural,
                    base_focus_records=(), direct_focus_records=(),
                    final_whole=None,
                )
                attempts.append(AdaptiveDirectExecutionAttempt.create(
                    outcome.ratio, "structural_failed",
                    candidate_id=outcome.spec.candidate_id,
                    build=compiled.build, evidence=evidence, recipe=recipe,
                ))
                continue

            stage = "focused"
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled before base focus")
            base_records = tuple(
                rerender_base_focus(outcome, compiled, event)
            )
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after base focus")
            expected_targets = tuple(
                item.target for item in sorted(
                    base_evaluation.focused_by_region.values(),
                    key=lambda item: item.target.rank,
                )
            )
            if (
                not base_records
                or any(not isinstance(item, FocusedRenderEvidence) for item in base_records)
                or any(item.cache_hit for item in base_records)
                or tuple(item.target for item in base_records) != expected_targets
                or tuple(item.target.rank for item in base_records) != tuple(range(len(base_records)))
            ):
                raise TypeError("adaptive-direct fresh base focus records are invalid")
            changed = tuple(composed.composition.changed_sources)
            source_by_identity = {
                item.source_identity: item for item in coverage.sources
            }
            snapshot_by_identity = {
                item.request.source_identity: item for item in outcome.snapshots
            }
            if tuple(item.source_identity for item in changed) != tuple(
                item.source_identity for item in coverage.sources
                if item.eligibility_kind == "eligible-exact-v1"
            ):
                raise ValueError("adaptive-direct changed source set differs from coverage")
            direct_records = []
            for item in changed:
                if event.is_set():
                    raise ProcessCancelledError(
                        "adaptive-direct cancelled before source-union render"
                    )
                record = render_source_union(
                    outcome, compiled, source_by_identity[item.source_identity],
                    snapshot_by_identity[item.source_identity],
                    workspace / "source-union" / item.source_identity.replace("/", "-"),
                    event,
                )
                if not isinstance(record, AdaptiveDirectSourceUnionRecord):
                    raise TypeError("adaptive-direct source-union result is invalid")
                direct_records.append(record)
                if event.is_set():
                    raise ProcessCancelledError(
                        "adaptive-direct cancelled after source-union render"
                    )
            direct_records = tuple(direct_records)
            focused_passed = all(
                item.validation.passed for item in (*base_records, *direct_records)
            )
            if not focused_passed:
                evidence = build_adaptive_direct_evidence(
                    terminal_status="focused_failed",
                    recipe=outcome.spec.composite_recipe,
                    composition=composed.composition,
                    changed_sources=changed,
                    compile_files=compiled.compile_files,
                    structural=structural,
                    base_focus_records=base_records,
                    direct_focus_records=direct_records,
                    final_whole=None,
                )
                attempts.append(AdaptiveDirectExecutionAttempt.create(
                    outcome.ratio, "focused_failed",
                    candidate_id=outcome.spec.candidate_id,
                    build=compiled.build, evidence=evidence, recipe=recipe,
                ))
                continue

            stage = "final_whole"
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled before final whole")
            final_whole = authorize_final_whole(
                outcome, composed, compiled, structural, event
            )
            if not isinstance(final_whole, FinalWholeAuthorizationEvidence):
                raise TypeError("adaptive-direct final-whole authorization is invalid")
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after final whole")
            terminal = "authorized" if final_whole.validation.passed else "final_whole_failed"
            evidence = build_adaptive_direct_evidence(
                terminal_status=terminal,
                recipe=outcome.spec.composite_recipe,
                composition=composed.composition,
                changed_sources=changed,
                compile_files=compiled.compile_files,
                structural=structural,
                base_focus_records=base_records,
                direct_focus_records=direct_records,
                final_whole=final_whole,
            )
            current_files = _current_compile_files(compiled, event)
            if event.is_set():
                raise ProcessCancelledError(
                    "adaptive-direct cancelled after final compile validation"
                )
            compiled = _snapshot_authorized_compile(compiled, event)
            current_files = compiled.compile_files
            size = _size_from_compile_files(compiled.build, current_files)
            evaluation = CandidateEvaluation(
                outcome.spec, size, structural.validation,
                final_whole.validation, compiled.build.compiled_models_dir,
                final_whole.validation, {},
            )
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, terminal, candidate_id=outcome.spec.candidate_id,
                evaluation=evaluation if terminal == "authorized" else None,
                build=compiled.build, evidence=evidence, recipe=recipe,
            ))
        except ProcessCancelledError as exc:
            event.set()
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, "cancelled", candidate_id=outcome.spec.candidate_id,
                recipe=recipe, error=str(exc),
            ))
        except Exception as exc:
            if event.is_set():
                attempts.append(AdaptiveDirectExecutionAttempt.create(
                    outcome.ratio, "cancelled", candidate_id=outcome.spec.candidate_id,
                    recipe=recipe, error=str(exc) or "adaptive-direct cancelled",
                ))
                continue
            failure_status = {
                "composition": "composition_failed",
                "compile": "compile_failed",
                "structural": "structural_failed",
                "focused": "focused_failed",
                "final_whole": "final_whole_failed",
            }[stage]
            evidence = None
            if stage == "composition":
                evidence = build_adaptive_direct_evidence(
                    terminal_status="composition_failed", recipe=recipe,
                    composition=None, changed_sources=(), compile_files=(),
                    structural=None, base_focus_records=(),
                    direct_focus_records=(), final_whole=None,
                )
            elif stage == "compile" and composed is not None:
                evidence = build_adaptive_direct_evidence(
                    terminal_status="compile_failed", recipe=recipe,
                    composition=composed.composition,
                    changed_sources=composed.composition.changed_sources,
                    compile_files=(), structural=None, base_focus_records=(),
                    direct_focus_records=(), final_whole=None,
                )
            attempts.append(AdaptiveDirectExecutionAttempt.create(
                outcome.ratio, failure_status,
                candidate_id=outcome.spec.candidate_id,
                build=(compiled.build if compiled is not None else None),
                evidence=evidence, recipe=recipe, error=str(exc),
            ))
    authorized = [
        item.evaluation for item in attempts if item.status == "authorized"
    ]
    selected = select_winner([base_evaluation, *authorized])
    cancelled = schedule.cancelled or any(
        item.status == "cancelled" for item in attempts
    )
    return AdaptiveDirectScheduleExecution(
        base_evaluation, selected, tuple(attempts), cancelled,
    )
