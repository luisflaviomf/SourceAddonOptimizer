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
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .cache import AtomicReplaceError, CacheKey, CandidateCache
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
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    FamilyManifest,
    GateFailure,
    SearchBudget,
    ValidationResult,
)
from .meshopt_bridge import MESHOPT_ENGINE_PREFERRED
from .processes import ProcessCancelledError, run_process
from .qc_inventory import _inventory_qc, build_family_manifests
from .qc_graph import QcGraph, parse_qc_graph
from .regions import filter_region_manifest, load_region_manifest_payload
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
_ENGINE_NAMES = frozenset({"fidelity", "blender", "meshoptimizer"})
_IO_CHUNK_SIZE = 1024 * 1024


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

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("unsupported Maximum report schema")
        object.__setattr__(self, "tool_versions", deep_freeze(dict(self.tool_versions)))
        object.__setattr__(self, "events", deep_freeze(tuple(self.events)))


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
    payload = {"schema": 1, "files": files}
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


def _cache_record_path(workspace: Path) -> Path:
    return workspace / "maximum_cache_record.json"


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
    atomic_write_json(
        _cache_record_path(workspace),
        {
            "schema": 2,
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
        },
    )


def _load_cached_build(
    cache_entry: Path,
    spec: CandidateSpec,
    *,
    key: CacheKey,
    manifest: FamilyManifest,
    dependency_digest: str,
    materialized_workspace: Path | None = None,
) -> CandidateBuild:
    workspace = materialized_workspace or (cache_entry / "payload")
    payload = json.loads(_cache_record_path(workspace).read_text(encoding="utf-8"))
    expected_fields = {
        "schema", "key_digest", "family_id", "model_rel", "input_hash",
        "candidate", "dependency_digest", "compiled_models_dir", "optimized_qc",
        "compile_record", "provenance", "commands", "structural", "visual",
    }
    if type(payload) is not dict or set(payload) != expected_fields:
        raise ValueError("cached record schema is invalid")
    if (
        type(payload["schema"]) is not int
        or payload["schema"] != 2
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
    )
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


def _recover_output_transaction(destination: Path) -> None:
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
        return
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
    expected = _tree_manifest(addon_dir, cancel_event)
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
    cache = CandidateCache(config.work_dir / "cache")
    event_history: list[dict[str, Any]] = []
    candidate_counts: dict[str, int] = {}
    candidate_indexes: dict[tuple[str, str], int] = {}
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
        if kind not in {"run_finished", "run_cancelled"}:
            partial("running")

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
        )
        atomic_write_json(report_path, report)
        return report
    diagnostic_method = getattr(adapter_set, "inventory_diagnostics", None)
    inventory_diagnostics = (
        dict(diagnostic_method(config)) if callable(diagnostic_method) else {}
    )
    total_family_count = len(manifests) + len(missing_model_rels)
    emit("run_started", family_count=total_family_count)

    for family_index, manifest in enumerate(manifests):
        original_family = _family_snapshot(original, manifest.model_rel)
        attempts: list[AttemptReport] = []
        emit("family_started", family=manifest.model_rel, index=family_index, total=total_family_count)
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
            control_savings = (
                compare_snapshots(original_family, control_size, original_family)
                if control_size is not None
                else {}
            )
            outcome = FamilyRunOutcome(
                manifest.family_id, manifest.model_rel, status, None,
                original_family, control_size, original_family, control_savings, tuple(attempts), reason,
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
                original_family,
                compare_snapshots(original_family, control_size, original_family),
                tuple(attempts),
                reason,
                *_worst(attempts),
                {item.relative_path: "original-preserved" for item in original_family.artifacts},
            )
            outcomes.append(outcome)
            selected_snapshots.append(original_family)
            emit(
                "family_finished",
                family=manifest.model_rel,
                status="failed",
                reason=reason,
                best_bytes=original_family.total_bytes,
                reduction_percent=0.0,
            )
            continue
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
                visual = (
                    adapter_set.visual(manifest, control_build, build, profile)
                    if structural.passed
                    else ValidationResult(False, worst_scope=structural.worst_scope)
                )
                emit("stage", family=manifest.model_rel, candidate=spec.candidate_id, stage="visual")
                if cancel.is_set():
                    raise ProcessCancelledError("cancelled after visual validation")
                if not cache_hit:
                    _store_cache_record(
                        workspace,
                        build,
                        structural,
                        visual,
                        key=key,
                        manifest=manifest,
                        dependency_digest=str(dependency["digest"]),
                    )
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
            except Exception as exc:
                attempts.append(AttemptReport(spec.candidate_id, spec.engine, "compile_failed", None, None, None, cache_hit, str(exc), {}))
                emit("candidate_finished", family=manifest.model_rel, candidate=spec.candidate_id, status="compile_failed", stage=getattr(exc, "stage", "orchestrator"))

        winner = select_winner(evaluations)
        no_positive_saving = (
            winner is not None and winner.size.total_bytes >= original_family.total_bytes
        )
        if no_positive_saving:
            winner = None
        if cancel.is_set():
            status, reason, selected = "cancelled", "run cancelled; original family retained", original_family
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
                original_family,
            )
            selected_id = None
            provenance = {"source": "original-preserved"}
        else:
            status, reason, selected = "optimized", "smallest passing compiled candidate selected", winner.size
            selected_id = winner.spec.candidate_id
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

    for missing_index, model_rel in enumerate(missing_model_rels, start=len(manifests)):
        family_id = hashlib.sha256(model_rel.casefold().encode("utf-8")).hexdigest()
        original_family = _family_snapshot(original, model_rel)
        missing_status = "cancelled" if cancel.is_set() else "preserved"
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
            original_family,
            {},
            (),
            reason,
            {},
            {},
            {item.relative_path: "original-preserved" for item in original_family.artifacts},
        ))
        selected_snapshots.append(original_family)
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
            _copy_selected_family(build, staging / "models", outcome.model_rel, cancel)
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
    )
    atomic_write_json(report_path, report)
    return report


def _graph_visual_states(graph: QcGraph) -> tuple[tuple[Path, ...], ...]:
    base = tuple(
        reference.source_path
        for reference in graph.references
        if reference.role == "visual" and reference.directive != "$lod/replacemodel"
    )
    if not base:
        raise ValueError("QC graph has no base visual sources")
    states: list[tuple[Path, ...]] = [base]
    groups: dict[str, list] = {}
    for reference in graph.references:
        if reference.directive == "$lod/replacemodel":
            groups.setdefault(reference.group, []).append(reference)
    for group, references in groups.items():
        if not group or len(references) % 2:
            raise ValueError("LOD replacement graph is ambiguous")
        replacements = {
            references[index].source_path: references[index + 1].source_path
            for index in range(0, len(references), 2)
        }
        state = tuple(replacements.get(source, source) for source in base)
        if state == base:
            raise ValueError("LOD state does not replace a base source")
        states.append(state)
    return tuple(states)


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
        positive = False
        for index in range(link_count):
            try:
                bone_id = int(fields[10 + (index * 2)])
                weight = float(fields[11 + (index * 2)])
            except ValueError:
                return None
            if bone_id < 0 or bone_id not in nodes or not math.isfinite(weight) or weight < 0:
                return None
            if weight > 0:
                positive = True
                controlling.add(bone_id)
        if not positive:
            return None
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
    ) -> ValidationResult:
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
            original_states = _graph_visual_states(original_graph)
            candidate_states = _graph_visual_states(candidate_graph)
        except (OSError, ValueError) as exc:
            raise CandidateBuildError(f"render QC graph is invalid: {exc}", stage="render") from exc
        if len(original_states) != len(candidate_states) or any(
            len(before) != len(after)
            for before, after in zip(original_states, candidate_states)
        ):
            raise CandidateBuildError("render LOD state pairs are ambiguous", stage="render")

        original_visual_sources = tuple(
            source for state in original_states for source in state
        )
        candidate_visual_sources = tuple(
            source for state in candidate_states for source in state
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
        state_results: list[tuple[str, ValidationResult]] = []
        for state_index, (before_sources, after_sources) in enumerate(
            zip(original_states, candidate_states)
        ):
            if self.cancel_event.is_set():
                raise ProcessCancelledError("cancelled before render validation")
            state_name = "base" if state_index == 0 else f"lod-{state_index}"
            state_root = render_root / state_name
            before = tuple(
                source_root / source.relative_to(manifest.source_dir.resolve(strict=True))
                for source in before_sources
            )
            after = tuple(after_sources)
            if any(not path.is_file() for path in (*before, *after)):
                raise CandidateBuildError("render source pairs are missing", stage="render")
            state_region_manifest = region_manifest.with_name(
                f"maximum_region_manifest.state-{state_index:03d}-{state_name}.json"
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
                load_region_manifest_payload(json.loads(
                    state_region_manifest.read_text(encoding="utf-8")
                ))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise CandidateBuildError(
                    f"state region manifest is invalid for {state_name}: {exc}",
                    stage="render",
                ) from exc
            command: list[str] = [
                str(self.config.blender_path), "--background", "--python",
                str(self.config.repo_root / "render_previews.py"), "--",
            ]
            for path in before:
                command.extend(("--before", str(path)))
            for path in after:
                command.extend(("--after", str(path)))
            command.extend((
                "--out", str(state_root), "--size", "512",
                "--passes", "textured,clay", "--poses", pose_arg,
                "--materials-root", str(self.config.addon_dir / "materials"),
                "--region-manifest", str(state_region_manifest),
            ))
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
            state_results.append((
                state_name,
                compare_render_sets(state_root / "original", state_root / "optimized", profile),
            ))
        return _aggregate_visual_results(state_results)

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

        budget = SearchBudget(
            max_candidates=getattr(args, "maximum_max_candidates", 18),
            min_ratio_step=getattr(args, "maximum_min_ratio_step", 0.025),
            min_marginal_saving=getattr(args, "maximum_min_marginal_saving", 0.005),
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
