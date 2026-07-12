from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .cache import CacheKey, CandidateCache, atomic_replace_tree
from .candidates import (
    BlenderAdapter,
    CandidateBuild,
    CandidateBuildError,
    CandidateTools,
    FidelityAdapter,
    MeshoptimizerAdapter,
)
from .compiled_size import compare_snapshots, scan_compiled_models
from .domain import (
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    FamilyManifest,
    SearchBudget,
    ValidationResult,
)
from .meshopt_bridge import MESHOPT_ENGINE_PREFERRED
from .processes import ProcessCancelledError, run_process
from .qc_inventory import build_family_manifests, parse_qc_fingerprint
from .reporting import atomic_write_json, canonical_payload, deep_freeze, event_line
from .search import choose_next, select_winner
from .structural_validation import validate_structure
from .visual_validation import FidelityProfile, compare_render_sets, load_profile


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
    }
)
_RATIOS = (0.75, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05)


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

    def __post_init__(self) -> None:
        for field_name in (
            "addon_dir", "output_dir", "work_dir", "blender_path",
            "studiomdl_path", "repo_root", "profile_path",
        ):
            raw = Path(getattr(self, field_name)).expanduser()
            object.__setattr__(self, field_name, raw.resolve(strict=False))
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "savings", deep_freeze(dict(self.savings)))
        object.__setattr__(self, "worst_metrics", deep_freeze(dict(self.worst_metrics)))
        object.__setattr__(self, "worst_scopes", deep_freeze(dict(self.worst_scopes)))
        object.__setattr__(self, "provenance", deep_freeze(dict(self.provenance)))


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
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("unsupported Maximum report schema")
        object.__setattr__(self, "tool_versions", deep_freeze(dict(self.tool_versions)))


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
    ) -> ValidationResult: ...
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


def _family_snapshot(snapshot: CompiledSizeSnapshot, model_rel: str) -> CompiledSizeSnapshot:
    model = PurePosixPath(model_rel.replace("\\", "/"))
    stem = model.with_suffix("").as_posix().casefold()
    artifacts = tuple(
        item for item in snapshot.artifacts
        if PurePosixPath(item.relative_path).with_suffix("").as_posix().casefold() == stem
        or item.relative_path.casefold().startswith(stem + ".")
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


def _validate_paths(config: MaximumRunConfig) -> None:
    if _is_reparse(config.addon_dir) or not config.addon_dir.is_dir():
        raise MaximumConfigError("addon_dir must be an existing non-reparse directory")
    if not (config.addon_dir / "models").is_dir():
        raise MaximumConfigError("addon_dir must contain models")
    internal = _first_reparse(config.addon_dir)
    if internal is not None:
        raise MaximumConfigError(f"addon_dir contains a symlink or junction: {internal.name}")
    for first_name, first, second_name, second in (
        ("output", config.output_dir, "addon", config.addon_dir),
        ("work", config.work_dir, "addon", config.addon_dir),
        ("output", config.output_dir, "work", config.work_dir),
    ):
        if _overlaps(first, second):
            raise MaximumConfigError(f"{first_name} and {second_name} directories overlap")
    for path, label in ((config.output_dir, "output"), (config.work_dir, "work")):
        if _is_reparse(path):
            raise MaximumConfigError(f"{label} directory cannot be a symlink or junction")
    if config.output_dir.exists() and not config.overwrite:
        raise MaximumConfigError("output exists and overwrite is disabled")
    config.output_dir.parent.mkdir(parents=True, exist_ok=True)
    config.work_dir.mkdir(parents=True, exist_ok=True)


def _default_schedule() -> tuple[CandidateSpec, ...]:
    fidelity = CandidateSpec("fidelity-baseline", "fidelity", 0.50, 0.0, "fidelity-current")
    blender = tuple(
        CandidateSpec(f"blender-r{str(ratio).replace('.', '')}", "blender", ratio, 0.01, "transfer-v1")
        for ratio in _RATIOS
    )
    meshopt = tuple(
        CandidateSpec(f"meshopt-r{str(ratio).replace('.', '')}", "meshoptimizer", ratio, 0.01, "transfer-v1")
        for ratio in _RATIOS
    )
    return (fidelity, *meshopt, *blender) if MESHOPT_ENGINE_PREFERRED else (fidelity, *blender, *meshopt)


def _default_sink(event: dict[str, Any]) -> None:
    print(event_line(event), flush=True)


def _validation_payload(result: ValidationResult) -> dict[str, Any]:
    return canonical_payload(result)


def _validation_from_payload(payload: Mapping[str, Any]) -> ValidationResult:
    from .domain import GateFailure
    failures = tuple(GateFailure(**item) for item in payload.get("failures", ()))
    return ValidationResult(
        bool(payload["passed"]), failures,
        dict(payload.get("metrics", {})), str(payload.get("worst_scope", "")),
    )


def _cache_record_path(workspace: Path) -> Path:
    return workspace / "maximum_cache_record.json"


def _store_cache_record(
    workspace: Path,
    build: CandidateBuild,
    structural: ValidationResult,
    visual: ValidationResult,
) -> None:
    relative_compiled = build.compiled_models_dir.relative_to(workspace).as_posix()
    relative_qc = build.optimized_qc.relative_to(workspace).as_posix()
    atomic_write_json(
        _cache_record_path(workspace),
        {
            "compiled_models_dir": relative_compiled,
            "optimized_qc": relative_qc,
            "compile_record": build.compile_record,
            "provenance": build.provenance,
            "commands": build.commands,
            "structural": structural,
            "visual": visual,
        },
    )


def _load_cached_build(cache_entry: Path, spec: CandidateSpec) -> tuple[CandidateBuild, ValidationResult, ValidationResult]:
    workspace = cache_entry / "payload"
    payload = json.loads(_cache_record_path(workspace).read_text(encoding="utf-8"))
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
    )
    return (
        build,
        _validation_from_payload(payload["structural"]),
        _validation_from_payload(payload["visual"]),
    )


def _copy_selected_family(
    build: CandidateBuild, output_models: Path, model_rel: str
) -> dict[str, str]:
    source_root = build.compiled_models_dir.resolve()
    destination_root = output_models.resolve()
    promoted: dict[str, str] = {}
    model_stem = PurePosixPath(model_rel.replace("\\", "/")).with_suffix("").as_posix().casefold()
    for logical, provenance in sorted(build.provenance.items()):
        relative = PurePosixPath(logical.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"candidate provenance escapes models root: {logical}")
        logical_folded = relative.as_posix().casefold()
        artifact_stem = relative.with_suffix("").as_posix().casefold()
        if artifact_stem != model_stem and not logical_folded.startswith(model_stem + "."):
            continue
        source = source_root.joinpath(*relative.parts)
        destination = destination_root.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file() or not _within(source.resolve(), source_root):
            raise ValueError(f"candidate artifact is invalid: {logical}")
        if not _within(destination.resolve(strict=False), destination_root):
            raise ValueError(f"candidate destination escapes models root: {logical}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        promoted[relative.as_posix()] = str(provenance)
    return promoted


def _worst(attempts: Sequence[AttemptReport]) -> tuple[dict[str, float], dict[str, str]]:
    metrics: dict[str, float] = {}
    scopes: dict[str, str] = {}
    for attempt in attempts:
        for result in (attempt.structural, attempt.visual):
            if result is None:
                continue
            for metric, value in result.metrics.items():
                if metric not in metrics or value > metrics[metric]:
                    metrics[metric] = value
                    scopes[metric] = result.worst_scope
    return metrics, scopes


def run_maximum_addon(
    config: MaximumRunConfig,
    cancel_event: threading.Event | None = None,
    *,
    adapters: AdapterSet | None = None,
    validator: StructuralValidator | None = None,
    event_sink: EventSink | None = None,
) -> MaximumRunReport:
    if not isinstance(config, MaximumRunConfig):
        raise TypeError("config must be MaximumRunConfig")
    # Loading the calibrated profile is deliberately first: the production sentinel
    # must fail closed before any candidate, cache mutation, or output promotion.
    profile = load_profile(config.profile_path)
    _validate_paths(config)
    cancel = cancel_event or threading.Event()
    sink = event_sink or _default_sink
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
    versions["profile_sha256"] = hashlib.sha256(config.profile_path.read_bytes()).hexdigest()
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
    cache = CandidateCache(config.work_dir / "cache")
    event_history: list[dict[str, Any]] = []
    tools = CandidateTools(
        sys.executable,
        config.blender_path,
        config.studiomdl_path,
        config.repo_root,
        heuristic_map=config.work_dir / "logs" / "selective_policy_map.json",
        meshopt_dll=config.repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll",
    )

    def partial(status: str) -> None:
        atomic_write_json(
            report_path,
            {
                "schema": 1,
                "status": status,
                "original_size": original,
                "control_size": _combine_snapshots(config.work_dir, control_snapshots),
                "selected_size": _combine_snapshots(config.work_dir, selected_snapshots),
                "tool_versions": versions,
                "families": tuple(outcomes),
                "events": tuple(event_history),
            },
        )

    def emit(kind: str, **payload: Any) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown Maximum event kind: {kind}")
        event = {"schema": 1, "kind": kind, **payload}
        # Canonicalization rejects non-finite data and unsupported/path objects.
        canonical_payload(event)
        frozen_event = canonical_payload(event)
        event_history.append(frozen_event)
        sink(dict(frozen_event))
        if kind not in {"run_finished", "run_cancelled"}:
            partial("running")

    emit("run_started", family_count=0)
    manifests = tuple(adapter_set.inventory(config))
    if not manifests:
        raise MaximumConfigError("no model families were inventoried")

    for family_index, manifest in enumerate(manifests):
        original_family = _family_snapshot(original, manifest.model_rel)
        attempts: list[AttemptReport] = []
        emit("family_started", family=manifest.model_rel, index=family_index, total=len(manifests))
        control_spec = CandidateSpec("roundtrip-control", "blender", 1.0, 0.0, "roundtrip-control")
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
            break

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
            if not structural.passed:
                raise CandidateBuildError("control roundtrip failed structural gates", stage="structural")
            attempts.append(AttemptReport(
                control_spec.candidate_id, "blender", "control", control_size,
                structural, None, False, "", control_build.provenance,
            ))
            control_snapshots.append(control_size)
            control_valid = True
            emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="control", compiled_bytes=control_size.total_bytes)
        except ProcessCancelledError as exc:
            cancel.set()
            attempts.append(AttemptReport(control_spec.candidate_id, "blender", "cancelled", None, None, None, False, str(exc), {}))
            emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="cancelled")
        except (CandidateBuildError, OSError, ValueError) as exc:
            attempts.append(AttemptReport(control_spec.candidate_id, "blender", "compile_failed", None, None, None, False, str(exc), {}))
            emit("candidate_finished", family=manifest.model_rel, candidate=control_spec.candidate_id, status="compile_failed", stage=getattr(exc, "stage", "orchestrator"))

        if control_build is None or control_size is None or not control_valid:
            status = "cancelled" if cancel.is_set() else "failed"
            reason = "run cancelled during control compile" if cancel.is_set() else "mandatory control compile failed"
            outcome = FamilyRunOutcome(
                manifest.family_id, manifest.model_rel, status, None,
                original_family, control_size, original_family, {}, tuple(attempts), reason,
                *_worst(attempts), {"source": "original-preserved"},
            )
            outcomes.append(outcome)
            selected_snapshots.append(original_family)
            emit("family_finished", family=manifest.model_rel, status=status, reason=reason)
            if cancel.is_set():
                break
            continue

        schedule_method = getattr(adapter_set, "candidate_schedule", None)
        schedule = tuple(schedule_method(manifest)) if callable(schedule_method) else _default_schedule()
        if (
            not schedule
            or len({item.candidate_id for item in schedule}) != len(schedule)
            or any(
                not isinstance(item, CandidateSpec)
                or not math.isfinite(item.target_ratio)
                or not 0 < item.target_ratio <= 1
                for item in schedule
            )
        ):
            raise MaximumConfigError("candidate schedule is empty, duplicated, or invalid")
        evaluations: list[CandidateEvaluation] = []
        attempted_ids: set[str] = set()
        last_best: str | None = None
        while not cancel.is_set():
            spec = choose_next(
                evaluations,
                config.budget,
                initial=schedule,
                attempted_ids=attempted_ids,
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
                if cache_entry is not None:
                    build, structural, visual = _load_cached_build(cache_entry, spec)
                    cache_hit = True
                    emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="cache_hit")
                    if cancel.is_set():
                        raise ProcessCancelledError("cancelled after cache restore")
                else:
                    if workspace.exists():
                        shutil.rmtree(workspace)
                    build = adapter_set.build(manifest, spec, workspace, tools, cancel)
                    if cancel.is_set():
                        raise ProcessCancelledError("cancelled after candidate compile")
                    emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="compiled_size")
                    structural = structural_validator(manifest, build)
                    emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="structural")
                    if cancel.is_set():
                        raise ProcessCancelledError("cancelled after structural validation")
                    visual = adapter_set.visual(manifest, control_build, build, profile) if structural.passed else ValidationResult(False, worst_scope=structural.worst_scope)
                    emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="visual")
                    if cancel.is_set():
                        raise ProcessCancelledError("cancelled after visual validation")
                    _store_cache_record(workspace, build, structural, visual)
                    cache.store(key, workspace, {"candidate": spec.candidate_id, "profile": profile.version})
                size = _family_snapshot(scan_compiled_models(build.compiled_models_dir), manifest.model_rel)
                evaluation = CandidateEvaluation(spec, size, structural, visual, build.compiled_models_dir)
                evaluations.append(evaluation)
                status = "passed" if evaluation.passed else "rejected"
                attempts.append(AttemptReport(
                    spec.candidate_id, spec.engine, status, size, structural, visual,
                    cache_hit, "", build.provenance,
                ))
                emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status=status, compiled_bytes=size.total_bytes, cache_hit=cache_hit)
                best = select_winner(evaluations)
                if best is not None and best.spec.candidate_id != last_best:
                    last_best = best.spec.candidate_id
                    selected_builds[manifest.family_id] = build if best is evaluation else selected_builds[manifest.family_id]
                    emit("best_updated", family=manifest.model_rel, candidate=best.spec.candidate_id, compiled_bytes=best.size.total_bytes)
                    if best is evaluation:
                        selected_builds[manifest.family_id] = build
            except ProcessCancelledError as exc:
                cancel.set()
                attempts.append(AttemptReport(spec.candidate_id, spec.engine, "cancelled", None, None, None, cache_hit, str(exc), {}))
                emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status="cancelled")
            except (CandidateBuildError, OSError, ValueError, KeyError, TypeError) as exc:
                attempts.append(AttemptReport(spec.candidate_id, spec.engine, "compile_failed", None, None, None, cache_hit, str(exc), {}))
                emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status="compile_failed", stage=getattr(exc, "stage", "orchestrator"))

        winner = select_winner(evaluations)
        if cancel.is_set():
            status, reason, selected = "cancelled", "run cancelled; original family retained", original_family
            selected_id = None
            provenance = {"source": "original-preserved"}
        elif winner is None:
            status, reason, selected = "preserved", "no candidate passed all hard gates", original_family
            selected_id = None
            provenance = {"source": "original-preserved"}
        else:
            status, reason, selected = "optimized", "smallest passing compiled candidate selected", winner.size
            selected_id = winner.spec.candidate_id
            chosen_attempt = next(item for item in attempts if item.candidate_id == selected_id)
            provenance = dict(chosen_attempt.provenance)
        selected_snapshots.append(selected)
        savings = compare_snapshots(original_family, control_size, selected) if winner is not None else {}
        metrics, scopes = _worst(attempts)
        outcome = FamilyRunOutcome(
            manifest.family_id, manifest.model_rel, status, selected_id,
            original_family, control_size, selected, savings, tuple(attempts), reason,
            metrics, scopes, provenance,
        )
        outcomes.append(outcome)
        emit("family_finished", family=manifest.model_rel, status=status, selected=selected_id, reason=reason)
        if cancel.is_set():
            break

    # Families not reached after cancellation remain explicit.
    if cancel.is_set() and len(outcomes) < len(manifests):
        for manifest in manifests[len(outcomes):]:
            original_family = _family_snapshot(original, manifest.model_rel)
            outcomes.append(FamilyRunOutcome(
                manifest.family_id, manifest.model_rel, "cancelled", None,
                original_family, None, original_family, {}, (),
                "run cancelled before family started", {}, {}, {"source": "original-preserved"},
            ))
            selected_snapshots.append(original_family)

    control_total = _combine_snapshots(config.work_dir, control_snapshots)
    selected_total = _combine_snapshots(config.work_dir, selected_snapshots)
    if cancel.is_set():
        final = original
        report = MaximumRunReport(
            1, "cancelled", original, control_total, selected_total, final,
            versions, tuple(outcomes), report_path, True,
        )
        atomic_write_json(report_path, report)
        emit("run_cancelled", completed_families=len([item for item in outcomes if item.status != "cancelled"]), total_families=len(manifests))
        return report

    staging = config.output_dir.parent / f".{config.output_dir.name}.maximum-staging-{uuid.uuid4().hex}"
    if staging.exists():
        raise RuntimeError("unique output staging path unexpectedly exists")
    shutil.copytree(config.addon_dir, staging)
    try:
        for outcome in outcomes:
            if cancel.is_set():
                raise ProcessCancelledError("cancelled during output staging")
            if outcome.status != "optimized":
                continue
            build = selected_builds.get(outcome.family_id)
            if build is None:
                raise RuntimeError(f"selected build is unavailable for {outcome.model_rel}")
            _copy_selected_family(build, staging / "models", outcome.model_rel)
        if cancel.is_set():
            raise ProcessCancelledError("cancelled before output promotion")
        atomic_replace_tree(staging, config.output_dir)
    except ProcessCancelledError:
        cancel.set()
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        report = MaximumRunReport(
            1, "cancelled", original, control_total, selected_total, original,
            versions, tuple(outcomes), report_path, True,
        )
        atomic_write_json(report_path, report)
        emit("run_cancelled", completed_families=len(outcomes), total_families=len(manifests))
        return report
    except BaseException as exc:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        final = (
            scan_compiled_models(config.output_dir / "models")
            if (config.output_dir / "models").is_dir()
            else original
        )
        report = MaximumRunReport(
            1, "failed", original, control_total, selected_total, final,
            versions, tuple(outcomes), report_path, False,
        )
        atomic_write_json(report_path, report)
        emit(
            "run_finished",
            status="failed",
            optimized=sum(item.status == "optimized" for item in outcomes),
            preserved=sum(item.status == "preserved" for item in outcomes),
            failed=sum(item.status == "failed" for item in outcomes),
            final_bytes=final.total_bytes,
            reason=type(exc).__name__,
        )
        return report
    final = scan_compiled_models(config.output_dir / "models")
    status = "success" if all(item.status in {"optimized", "preserved"} for item in outcomes) else "failed"
    report = MaximumRunReport(
        1, status, original, control_total, selected_total, final,
        versions, tuple(outcomes), report_path, False,
    )
    atomic_write_json(report_path, report)
    emit("run_finished", status=status, optimized=sum(item.status == "optimized" for item in outcomes), preserved=sum(item.status == "preserved" for item in outcomes), failed=sum(item.status == "failed" for item in outcomes), final_bytes=final.total_bytes)
    return report


class ProductionAdapters:
    def __init__(self, config: MaximumRunConfig, cancel_event: threading.Event) -> None:
        self.config = config
        self.cancel_event = cancel_event

    def _vtfcmd(self) -> Path | None:
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "GmodAddonOptimizer" / "tools" / "VTFEdit" / "VTFCmd.exe"
        candidates = (
            Path(os.environ["VTFCMD"]).expanduser() if os.environ.get("VTFCMD") else None,
            self.config.repo_root / "VTFEdit" / "VTFCmd.exe",
            self.config.repo_root / "tools" / "VTFEdit" / "VTFCmd.exe",
            local,
        )
        return next((path.resolve() for path in candidates if path is not None and path.is_file()), None)

    def inventory(self, config: MaximumRunConfig) -> Sequence[FamilyManifest]:
        return build_family_manifests(
            config.addon_dir / "models",
            config.work_dir / "logs" / "decompile_manifest.json",
            config.work_dir / "src",
        )

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
        else:
            adapter = MeshoptimizerAdapter(cancel_event=cancel_event)
        return adapter.generate(manifest, spec, workspace, tools)

    def visual(
        self,
        manifest: FamilyManifest,
        control: CandidateBuild,
        candidate: CandidateBuild,
        profile: FidelityProfile,
    ) -> ValidationResult:
        source_root = candidate.workspace / "render-source"
        if source_root.exists():
            shutil.rmtree(source_root)
        shutil.copytree(manifest.source_dir, source_root)
        expression = (
            "import sys;from pathlib import Path;"
            f"sys.path.insert(0,{str(self.config.repo_root)!r});"
            "from batch_optimize_maximum import write_source_region_manifest;"
            f"write_source_region_manifest(Path({str(source_root)!r}))"
        )
        manifest_log = candidate.workspace / "logs" / "render-manifest.log"
        result = run_process(
            (str(self.config.blender_path), "--background", "--python-expr", expression),
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
        optimized_fingerprint = parse_qc_fingerprint(candidate.optimized_qc)
        before = tuple(source_root / Path(*name.replace("\\", "/").split("/")) for name in manifest.fingerprint.mesh_files)
        after = tuple(candidate.optimized_qc.parent / Path(*name.replace("\\", "/").split("/")) for name in optimized_fingerprint.mesh_files)
        if not before or len(before) != len(after) or any(not path.is_file() for path in (*before, *after)):
            raise CandidateBuildError(
                "render source pairs are missing or ambiguous",
                stage="render",
            )
        render_root = candidate.workspace / "renders"
        command: list[str] = [
            str(self.config.blender_path), "--background", "--python",
            str(self.config.repo_root / "render_previews.py"), "--",
        ]
        for path in before:
            command.extend(("--before", str(path)))
        for path in after:
            command.extend(("--after", str(path)))
        command.extend((
            "--out", str(render_root),
            "--size", "512",
            "--passes", "textured,clay",
            "--poses", "bind:0",
            "--materials-root", str(self.config.addon_dir / "materials"),
            "--region-manifest", str(region_manifest),
        ))
        vtfcmd = self._vtfcmd()
        if vtfcmd is not None:
            command.extend(("--vtfcmd", str(vtfcmd)))
        render_log = candidate.workspace / "logs" / "render.log"
        result = run_process(
            command,
            cwd=self.config.repo_root,
            log_path=render_log,
            cancel_event=self.cancel_event,
        )
        if result.returncode != 0:
            raise CandidateBuildError(
                f"render validation exited with code {result.returncode}",
                stage="render",
                log_path=render_log,
            )
        return compare_render_sets(render_root / "original", render_root / "optimized", profile)

    def tool_versions(self) -> Mapping[str, str]:
        def digest(path: Path) -> str:
            try:
                return hashlib.sha256(path.read_bytes()).hexdigest()
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
        load_profile(profile_path)
        blender = _resolved_blender(getattr(args, "blender", None))
        studiomdl_raw = getattr(args, "studiomdl", None)
        if studiomdl_raw:
            studiomdl = Path(studiomdl_raw).expanduser().resolve()
        else:
            from batch_compile_opt_qc import DEFAULT_STUDIOMDL
            studiomdl = Path(DEFAULT_STUDIOMDL).expanduser().resolve()
        if not blender.is_file() or not studiomdl.is_file():
            raise MaximumConfigError("Maximum Blender/StudioMDL tools were not found")

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
        budget = SearchBudget(
            max_candidates=int(getattr(args, "maximum_max_candidates", 18)),
            min_ratio_step=float(getattr(args, "maximum_min_ratio_step", 0.025)),
            min_marginal_saving=float(getattr(args, "maximum_min_marginal_saving", 0.005)),
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
        )
        report = run_maximum_addon(config)
        return 0 if report.status == "success" else 1
    except (MaximumConfigError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Maximum configuration/input error: {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"[ERROR] Maximum run failed: {exc}", flush=True)
        return 1
