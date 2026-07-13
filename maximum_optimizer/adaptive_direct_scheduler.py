from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import threading
from typing import Callable

from .adaptive_direct_evidence import (
    AdaptiveDirectEvidence,
    build_adaptive_direct_evidence,
)
from .candidates import CandidateBuild
from .compiled_size import scan_compiled_models
from .composite import AdaptiveDirectSourceUnionRecord
from .domain import (
    AdaptiveDirectCoverageManifest,
    CandidateEvaluation,
    ComposedSourceTree,
    StructuralAuthorizationEvidence,
)
from .focused_cache import FinalWholeAuthorizationEvidence, FocusedRenderEvidence
from .monaco_schedule import (
    MonacoCancelledReservation,
    MonacoFailedReservation,
    MonacoScheduledCandidate,
    MONACO_DIRECT_RATIOS,
    build_monaco_schedule,
)
from .processes import ProcessCancelledError
from .production_adapters import AdaptiveDirectCompileResult


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
            or type(self.error) is not str
            or (self.evaluation is not None and not isinstance(
                self.evaluation, CandidateEvaluation
            ))
            or (self.build is not None and not isinstance(self.build, CandidateBuild))
            or (self.evidence is not None and not isinstance(
                self.evidence, AdaptiveDirectEvidence
            ))
        ):
            raise ValueError("adaptive-direct attempt identity is invalid")
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
        object.__setattr__(self, "attempts", attempts)


def execute_adaptive_direct_schedule(
    *,
    base_evaluation: CandidateEvaluation,
    base_build: CandidateBuild,
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
    if (
        not isinstance(base_evaluation, CandidateEvaluation)
        or not isinstance(base_build, CandidateBuild)
        or base_evaluation.spec != base_build.spec
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
    for callback in (
        reserve_ratio, access_ratio, compose_candidate, compile_candidate,
        authorize_structural, rerender_base_focus, render_source_union,
        authorize_final_whole,
    ):
        if not callable(callback):
            raise TypeError("adaptive-direct scheduler callback is invalid")
    require_adaptive_direct_source_count(coverage.sources)
    event = cancel_event or threading.Event()
    schedule = build_monaco_schedule(
        base_spec=base_evaluation.spec,
        coverage=coverage,
        remaining_candidates=remaining_candidates,
        reserve=reserve_ratio,
        access_ratio=access_ratio,
        cancel_event=event,
    )
    attempts: list[AdaptiveDirectExecutionAttempt] = []
    selected = base_evaluation
    root = Path(workspace_root)
    if schedule.outcomes:
        root.mkdir(parents=True, exist_ok=True)
    for outcome in schedule:
        if isinstance(outcome, MonacoFailedReservation):
            attempts.append(AdaptiveDirectExecutionAttempt(
                outcome.ratio, "schedule_failed", error=outcome.failure_reason,
            ))
            continue
        if isinstance(outcome, MonacoCancelledReservation):
            attempts.append(AdaptiveDirectExecutionAttempt(
                outcome.ratio, "cancelled", error=outcome.failure_reason,
            ))
            continue
        if not isinstance(outcome, MonacoScheduledCandidate):
            raise TypeError("adaptive-direct schedule returned an invalid outcome")
        stage = "composition"
        try:
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled before composition")
            workspace = root / outcome.spec.candidate_id
            composed = compose_candidate(outcome, workspace, event)
            if not isinstance(composed, ComposedSourceTree):
                raise TypeError("adaptive-direct compositor returned an invalid result")
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after composition")
            stage = "compile"
            compiled = compile_candidate(outcome, composed, event)
            if not isinstance(compiled, AdaptiveDirectCompileResult):
                raise TypeError("adaptive-direct compiler returned an invalid result")
            if compiled.build.spec != outcome.spec:
                raise ValueError("adaptive-direct compiled candidate identity differs")
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled after compile")

            stage = "structural"
            structural = authorize_structural(outcome, composed, compiled)
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
                attempts.append(AdaptiveDirectExecutionAttempt(
                    outcome.ratio, "structural_failed", outcome.spec.candidate_id,
                    build=compiled.build, evidence=evidence,
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
                attempts.append(AdaptiveDirectExecutionAttempt(
                    outcome.ratio, "focused_failed", outcome.spec.candidate_id,
                    build=compiled.build, evidence=evidence,
                ))
                continue

            stage = "final_whole"
            if event.is_set():
                raise ProcessCancelledError("adaptive-direct cancelled before final whole")
            final_whole = authorize_final_whole(
                outcome, composed, compiled, structural
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
            size = scan_compiled_models(compiled.build.compiled_models_dir)
            evaluation = CandidateEvaluation(
                outcome.spec, size, structural.validation,
                final_whole.validation, compiled.build.compiled_models_dir,
                final_whole.validation, {},
            )
            attempts.append(AdaptiveDirectExecutionAttempt(
                outcome.ratio, terminal, outcome.spec.candidate_id,
                evaluation if terminal == "authorized" else None,
                compiled.build, evidence,
            ))
            if (
                terminal == "authorized"
                and evaluation.size.total_bytes < selected.size.total_bytes
            ):
                selected = evaluation
        except ProcessCancelledError as exc:
            event.set()
            attempts.append(AdaptiveDirectExecutionAttempt(
                outcome.ratio, "cancelled", outcome.spec.candidate_id, error=str(exc),
            ))
        except Exception as exc:
            failure_status = {
                "composition": "composition_failed",
                "compile": "compile_failed",
                "structural": "structural_failed",
                "focused": "focused_failed",
                "final_whole": "final_whole_failed",
            }[stage]
            attempts.append(AdaptiveDirectExecutionAttempt(
                outcome.ratio, failure_status, outcome.spec.candidate_id,
                error=str(exc),
            ))
    return AdaptiveDirectScheduleExecution(
        base_evaluation, selected, tuple(attempts), schedule.cancelled or event.is_set(),
    )
