from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
import stat
import threading
import time
import uuid
from collections.abc import Callable, Sequence

from .candidates import _direct_input_material_proofs, _validate_direct_smd_output
from .domain import (
    DirectSourceBuildRequest,
    SourceFileProof,
    direct_candidate_id,
)
from .focused_cache import (
    _copy_file_no_follow,
    _file_proof,
    _has_reparse_ancestor,
    _is_reparse,
    _read_regular_no_follow,
)
from .processes import (
    ProcessCancelledError,
    ProcessResult,
    run_process,
)


ProcessRunner = Callable[
    [Sequence[str | Path], Path, Path, threading.Event], ProcessResult
]


_DEFAULT_MAX_SOURCE_BYTES = 512 * 1024**2
_DEFAULT_MAX_ARTIFACT_BYTES = 2 * 1024**3
_DEFAULT_MAX_ARTIFACT_FILES = 64
_DEFAULT_MAX_PROCESS_SECONDS = 30 * 60.0


def _abspath(path: Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _overlaps(first: Path, second: Path) -> bool:
    first_text = os.path.normcase(os.path.abspath(first))
    second_text = os.path.normcase(os.path.abspath(second))
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError:
        return False
    return common in {first_text, second_text}


def _stat_is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory_identity(path: Path) -> tuple[int, int, int]:
    info = os.lstat(path)
    if _is_reparse(path) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("direct source runner root is not a regular directory")
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
    )


def _regular_identity(path: Path) -> tuple[int, int, int, int]:
    info = os.lstat(path)
    if _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"direct source runner tool is not a regular file: {path}")
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1e9))),
    )


def _cancel(event, message: str) -> None:
    if event is not None and event.is_set():
        raise ProcessCancelledError(message)


class _DeadlineEvent:
    def __init__(self, event: threading.Event, seconds: float) -> None:
        self._event = event
        self._deadline = time.monotonic() + seconds

    def is_set(self) -> bool:
        return self._event.is_set() or time.monotonic() >= self._deadline


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _acquire_run_root(work_root: Path, request_sha256: str) -> tuple[Path, tuple[int, int, int]]:
    suffix = uuid.uuid4().hex
    staging = work_root / f".{request_sha256[:16]}.direct-source-acquire-{suffix}"
    destination = work_root / f"run-{request_sha256[:16]}-{suffix}"
    staging.mkdir(parents=False, exist_ok=False)
    identity = _directory_identity(staging)
    try:
        os.rename(staging, destination)
    except BaseException:
        _preserve_quarantine(staging, identity)
        raise
    if _directory_identity(destination) != identity:
        raise ValueError("direct source runner root identity changed during acquisition")
    return destination, identity


def _preserve_quarantine(
    root: Path, identity: tuple[int, int, int] | None,
) -> Path | None:
    if identity is None or not os.path.lexists(root):
        return None
    try:
        if _directory_identity(root) != identity:
            return None
    except (OSError, ValueError):
        return None
    quarantine = root.with_name(
        f".{root.name}.direct-source-quarantine-{uuid.uuid4().hex}"
    )
    try:
        os.rename(root, quarantine)
    except OSError:
        return None
    try:
        if _directory_identity(quarantine) != identity:
            if not os.path.lexists(root):
                os.rename(quarantine, root)
            return None
    except (OSError, ValueError):
        return None
    return quarantine


def _artifact_kind(relative_path: str) -> str:
    folded = relative_path.casefold()
    if folded.endswith(".qc"):
        return "qc"
    if folded in {"source.smd", "output/source_opt.smd"}:
        return "visual-source"
    return "auxiliary"


def _bounded_artifact_proofs(
    root: Path,
    event,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[SourceFileProof, ...]:
    _directory_identity(root)
    pending = [root]
    paths: list[Path] = []
    while pending:
        _cancel(event, "direct source runner cancelled during artifact inventory")
        directory = pending.pop()
        _directory_identity(directory)
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda item: (item.name.casefold(), item.name))
        for entry in entries:
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(info) or stat.S_ISLNK(info.st_mode):
                raise ValueError("direct source runner artifact tree contains a reparse point")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                paths.append(path)
                if len(paths) > max_files:
                    raise ValueError("direct source runner artifact file budget exceeded")
            else:
                raise ValueError("direct source runner artifact tree contains a special file")
    proofs: list[SourceFileProof] = []
    total = 0
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        size, digest = _file_proof(
            path, event, contained_root=root, max_bytes=max_bytes - total,
        )
        total += size
        if total > max_bytes:
            raise ValueError("direct source runner artifact byte budget exceeded")
        proofs.append(SourceFileProof(
            file_identity=relative,
            kind=_artifact_kind(relative),
            relative_path=relative,
            size=size,
            sha256=digest,
        ))
    return tuple(proofs)


def _toolchain_proof(tools: "DirectSourceRunnerTools") -> tuple[object, ...]:
    script = tools.repo_root / "batch_optimize_maximum.py"
    return (
        _regular_identity(tools.blender_exe),
        _file_proof(script, contained_root=tools.repo_root, max_bytes=8 * 1024**2),
        _file_proof(tools.meshopt_dll, max_bytes=64 * 1024**2),
    )


def _validate_metrics(
    root: Path,
    request: DirectSourceBuildRequest,
    source_sha256: str,
    output_sha256: str,
    output_triangles: int,
    event,
    *,
    max_source_bytes: int,
) -> None:
    metrics_path = root / "candidate_metrics.json"
    raw = _read_regular_no_follow(
        metrics_path, event, contained_root=root, max_bytes=max_source_bytes,
    )
    try:
        metrics = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("direct source runner metrics are invalid JSON") from exc
    before = sum(item.triangles_before for item in request.expected_materials)
    if (
        type(metrics) is not dict
        or metrics.get("schema_version") != 1
        or metrics.get("candidate_id") != direct_candidate_id(request)
        or metrics.get("engine") != "meshoptimizer"
        or type(metrics.get("triangles_before")) is not int
        or metrics["triangles_before"] != before
        or type(metrics.get("triangles_after")) is not int
        or metrics["triangles_after"] != output_triangles
        or not 0 < metrics["triangles_after"] < before
    ):
        raise ValueError("direct source runner metrics identity or totals differ")
    files = metrics.get("files")
    provenance = metrics.get("provenance")
    if type(files) is not list or len(files) != 1 or type(files[0]) is not dict:
        raise ValueError("direct source runner metrics file coverage differs")
    file_item = files[0]
    objects = file_item.get("objects")
    if (
        Path(os.path.abspath(file_item.get("source", ""))) != root / "source.smd"
        or Path(os.path.abspath(file_item.get("output", ""))) != root / "output" / "source_opt.smd"
        or file_item.get("triangles_before") != metrics["triangles_before"]
        or file_item.get("triangles_after") != metrics["triangles_after"]
        or type(objects) is not list
        or not objects
        or any(
            type(item) is not dict
            or item.get("strategy") != "meshopt-direct-position-v1"
            or item.get("transfer") != "direct-v1"
            for item in objects
        )
    ):
        raise ValueError("direct source runner per-file strategy evidence differs")
    if type(provenance) is not list or len(provenance) != 1 or type(provenance[0]) is not dict:
        raise ValueError("direct source runner provenance coverage differs")
    record = provenance[0]
    if (
        record.get("graph_file") != "model.qc"
        or record.get("directive") != "$body"
        or record.get("logical_path") != "source.smd"
        or record.get("role") != "visual"
        or record.get("status") != "optimized"
        or record.get("source_sha256") != source_sha256
        or record.get("output") != "output/source_opt.smd"
        or record.get("output_sha256") != output_sha256
    ):
        raise ValueError("direct source runner provenance differs")
    manifest = root / "maximum_region_manifest.json"
    manifest_size, manifest_hash = _file_proof(
        manifest, event, contained_root=root, max_bytes=max_source_bytes,
    )
    if (
        manifest_size < 1
        or metrics.get("region_manifest") != "maximum_region_manifest.json"
        or metrics.get("region_manifest_sha256") != manifest_hash
    ):
        raise ValueError("direct source runner region manifest proof differs")


def _publish_no_replace(
    source: Path,
    destination: Path,
    event,
    *,
    source_root: Path,
    expected_size: int,
    expected_sha256: str,
) -> None:
    temporary = destination.with_name(
        f".{destination.name}.direct-source-publish-{uuid.uuid4().hex}"
    )
    temporary_identity: tuple[int, int] | None = None
    try:
        ownership = _copy_file_no_follow(
            source, temporary, event, contained_root=source_root,
        )
        temporary_identity = ownership[:2]
        if _file_proof(temporary, event, max_bytes=expected_size) != (
            expected_size, expected_sha256,
        ):
            raise ValueError("direct source runner private publication differs")
        _cancel(event, "direct source runner cancelled before publication")
        os.link(temporary, destination, follow_symlinks=False)
        # Publication is the terminal linearization point.  Cancellation after
        # this link is observed by the enclosing snapshot boundary.
    finally:
        if os.path.lexists(temporary):
            try:
                info = os.lstat(temporary)
                if (
                    temporary_identity is not None
                    and (int(info.st_dev), int(info.st_ino)) == temporary_identity
                    and stat.S_ISREG(info.st_mode)
                    and not _stat_is_reparse(info)
                ):
                    temporary.unlink()
            except OSError:
                pass


@dataclass(frozen=True)
class DirectSourceRunnerTools:
    blender_exe: Path
    repo_root: Path
    meshopt_dll: Path
    work_root: Path
    process_runner: ProcessRunner = field(default=run_process, repr=False, compare=False)
    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES
    max_artifact_files: int = _DEFAULT_MAX_ARTIFACT_FILES
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES
    max_process_seconds: float = _DEFAULT_MAX_PROCESS_SECONDS

    def __post_init__(self) -> None:
        for name in ("blender_exe", "repo_root", "meshopt_dll", "work_root"):
            object.__setattr__(self, name, _abspath(getattr(self, name)))
        if not callable(self.process_runner):
            raise TypeError("direct source process runner is invalid")
        if (
            type(self.max_source_bytes) is not int or self.max_source_bytes < 1
            or type(self.max_artifact_files) is not int or self.max_artifact_files < 1
            or type(self.max_artifact_bytes) is not int or self.max_artifact_bytes < 1
            or type(self.max_process_seconds) not in (int, float)
            or not math.isfinite(float(self.max_process_seconds))
            or self.max_process_seconds <= 0
        ):
            raise ValueError("direct source runner budgets are invalid")
        if (
            not self.repo_root.is_dir()
            or not self.work_root.is_dir()
            or _has_reparse_ancestor(self.repo_root)
            or _has_reparse_ancestor(self.work_root)
        ):
            raise ValueError("direct source runner roots are unavailable or unsafe")
        _directory_identity(self.work_root)
        _regular_identity(self.blender_exe)
        _regular_identity(self.meshopt_dll)
        script = self.repo_root / "batch_optimize_maximum.py"
        _regular_identity(script)


@dataclass(frozen=True)
class DirectSourceRunResult:
    request_sha256: str
    run_root: Path
    process: ProcessResult
    artifacts: tuple[SourceFileProof, ...]
    toolchain_sha256: str
    output_size: int
    output_sha256: str

    def __post_init__(self) -> None:
        if self.request_sha256.casefold() != self.request_sha256 or len(self.request_sha256) != 64:
            raise ValueError("direct source run request proof is invalid")
        if not Path(self.run_root).is_absolute() or not isinstance(self.process, ProcessResult):
            raise ValueError("direct source run result identity is invalid")
        if any(not isinstance(item, SourceFileProof) for item in self.artifacts):
            raise TypeError("direct source run artifacts are invalid")
        if len(self.toolchain_sha256) != 64 or self.toolchain_sha256.casefold() != self.toolchain_sha256:
            raise ValueError("direct source run toolchain proof is invalid")
        if (
            type(self.output_size) is not int or self.output_size < 1
            or len(self.output_sha256) != 64
            or self.output_sha256.casefold() != self.output_sha256
        ):
            raise ValueError("direct source run output proof is invalid")


class BlenderDirectSourceRunner:
    """One-process production callback for ``build_direct_source_snapshot``.

    Each call owns one fresh mini-root.  Successful roots are retained as the
    returned diagnostic artifact.  Failed roots are atomically renamed and
    retained as quarantines; this class never recursively deletes by pathname.
    """

    def __init__(self, tools: DirectSourceRunnerTools) -> None:
        if not isinstance(tools, DirectSourceRunnerTools):
            raise TypeError("direct source runner tools are invalid")
        self.tools = tools

    def __call__(
        self,
        input_path: Path,
        output_path: Path,
        request: DirectSourceBuildRequest,
        cancel_event: threading.Event | None,
    ) -> DirectSourceRunResult:
        if not isinstance(request, DirectSourceBuildRequest):
            raise TypeError("direct source runner request is invalid")
        if (
            request.strategy != "meshopt-direct-position-v1"
            or request.transfer != "direct-position-v1"
        ):
            raise ValueError("direct source runner strategy is invalid")
        event = cancel_event or threading.Event()
        _cancel(event, "direct source runner cancelled before preflight")
        source = _abspath(input_path)
        destination = _abspath(output_path)
        if (
            not source.is_file()
            or _has_reparse_ancestor(source)
            or not destination.parent.is_dir()
            or _has_reparse_ancestor(destination.parent)
            or os.path.lexists(destination)
            or _overlaps(self.tools.work_root, source)
            or _overlaps(self.tools.work_root, destination)
        ):
            raise ValueError("direct source runner input/output boundary is unsafe")
        source_bytes = _read_regular_no_follow(
            source, event, contained_root=source.parent,
            max_bytes=self.tools.max_source_bytes,
        )
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("direct source runner input is not UTF-8") from exc
        if _direct_input_material_proofs(source_text) != request.expected_materials:
            raise ValueError("direct source runner input material proof differs")
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        initial_tools = _toolchain_proof(self.tools)
        run_root: Path | None = None
        ownership: tuple[int, int, int] | None = None
        try:
            _cancel(event, "direct source runner cancelled before root acquisition")
            run_root, ownership = _acquire_run_root(
                self.tools.work_root, request.request_sha256,
            )
            _copy_file_no_follow(
                source, run_root / "source.smd", event, contained_root=source.parent,
            )
            qc = b'$modelname "maximum/direct.mdl"\n$body "body" "source.smd"\n'
            _write_new(run_root / "model.qc", qc)
            candidate = {
                "candidate_id": direct_candidate_id(request),
                "engine": "meshoptimizer",
                "ratio": request.direct_ratio,
                "target_error": 0.01,
                "update_vertices": False,
                "region_overrides": [],
                "strategy": "meshopt-direct-position-v1",
                "direct_degenerate_prefilter": "direct-degenerate-prefilter-v1",
                # The sealed request calls this transfer direct-position-v1;
                # batch_optimize_maximum's native serializer contract calls the
                # same operation direct-v1.
                "transfer": "direct-v1",
            }
            candidate_bytes = json.dumps(
                candidate, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            _write_new(run_root / "candidate.json", candidate_bytes)
            command = (
                str(self.tools.blender_exe),
                "--background",
                "--python-exit-code",
                "1",
                "--python",
                str(self.tools.repo_root / "batch_optimize_maximum.py"),
                "--",
                str(run_root),
                "--candidate-json",
                str(run_root / "candidate.json"),
                "--meshopt-dll",
                str(self.tools.meshopt_dll),
            )
            deadline_event = _DeadlineEvent(event, float(self.tools.max_process_seconds))
            result = self.tools.process_runner(
                command, run_root, run_root / "blender.log", deadline_event,
            )
            if not isinstance(result, ProcessResult):
                raise TypeError("direct source runner process result is invalid")
            if result.command != command or Path(result.log_path) != run_root / "blender.log":
                raise ValueError("direct source runner process identity differs")
            if result.elapsed_seconds > self.tools.max_process_seconds:
                raise ProcessCancelledError("direct source runner process time budget exceeded")
            _cancel(deadline_event, "direct source runner cancelled after process")
            if result.returncode != 0:
                raise RuntimeError(
                    f"direct source Blender failed with exit code {result.returncode}"
                )
            if _toolchain_proof(self.tools) != initial_tools:
                raise ValueError("direct source runner tool changed during process")
            optimized = run_root / "output" / "source_opt.smd"
            output_size, output_sha256 = _file_proof(
                optimized, event, contained_root=run_root,
                max_bytes=self.tools.max_source_bytes,
            )
            try:
                optimized_text = _read_regular_no_follow(
                    optimized, event, contained_root=run_root,
                    max_bytes=self.tools.max_source_bytes,
                ).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("direct source runner output is not UTF-8") from exc
            _before, output_triangles, _materials = _validate_direct_smd_output(
                source_text, optimized_text, request.direct_ratio,
            )
            _validate_metrics(
                run_root, request, source_sha256, output_sha256, output_triangles, event,
                max_source_bytes=self.tools.max_source_bytes,
            )
            artifacts = _bounded_artifact_proofs(
                run_root, event,
                max_files=self.tools.max_artifact_files,
                max_bytes=self.tools.max_artifact_bytes,
            )
            toolchain_sha256 = hashlib.sha256(
                json.dumps(initial_tools, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if _bounded_artifact_proofs(
                run_root, event,
                max_files=self.tools.max_artifact_files,
                max_bytes=self.tools.max_artifact_bytes,
            ) != artifacts:
                raise ValueError("direct source runner artifacts changed before publication")
            # Copy to a private sibling, then atomically link without replacement.
            # The private copy is rehashed before the terminal link, so mutation of
            # the only published artifact still fails before publication.
            completed = DirectSourceRunResult(
                request_sha256=request.request_sha256,
                run_root=run_root,
                process=result,
                artifacts=artifacts,
                toolchain_sha256=toolchain_sha256,
                output_size=output_size,
                output_sha256=output_sha256,
            )
            _publish_no_replace(
                optimized, destination, event, source_root=run_root,
                expected_size=output_size, expected_sha256=output_sha256,
            )
            return completed
        except BaseException:
            if run_root is not None:
                _preserve_quarantine(run_root, ownership)
            raise
