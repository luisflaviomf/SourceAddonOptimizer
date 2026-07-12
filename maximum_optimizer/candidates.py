from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from vehicle_steer_turn_basis_fix import apply_under_root

from .domain import CandidateSpec, FamilyManifest
from .processes import ProcessResult, run_process
from .qc_inventory import _inventory_qc


ProcessRunner = Callable[[Sequence[str | Path], Path, Path, threading.Event], ProcessResult]


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CandidateTools:
    python_exe: Path
    blender_exe: Path
    studiomdl_exe: Path
    repo_root: Path
    heuristic_map: Path | None = None
    meshopt_dll: Path | None = None
    compile_jobs: int = 1

    def __post_init__(self) -> None:
        for name in ("python_exe", "blender_exe", "studiomdl_exe", "repo_root"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        if self.heuristic_map is not None:
            object.__setattr__(
                self, "heuristic_map", Path(self.heuristic_map).expanduser().resolve()
            )
        if self.meshopt_dll is not None:
            object.__setattr__(
                self, "meshopt_dll", Path(self.meshopt_dll).expanduser().resolve()
            )
        if self.compile_jobs < 1:
            raise ValueError("compile_jobs must be at least 1")


@dataclass(frozen=True)
class CandidateBuild:
    spec: CandidateSpec
    workspace: Path
    optimized_qc: Path
    compiled_models_dir: Path
    compile_record: Mapping[str, object]
    provenance: Mapping[str, str]
    commands: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "compile_record", _freeze(self.compile_record))
        object.__setattr__(self, "provenance", _freeze(self.provenance))


class CandidateBuildError(RuntimeError):
    def __init__(self, message: str, *, stage: str, log_path: Path | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.log_path = log_path


class CandidateAdapter(Protocol):
    def generate(
        self,
        manifest: FamilyManifest,
        spec: CandidateSpec,
        workspace: Path,
        tools: CandidateTools,
    ) -> CandidateBuild: ...


def _normalized_model_name(value: str) -> str:
    return value.replace("\\", "/").strip().strip('"').casefold()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _first_internal_symlink(root: Path) -> Path | None:
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in (*directory_names, *file_names):
            candidate = parent / name
            if candidate.is_symlink():
                return candidate
    return None


def _required_artifact_path(expected_mdl: Path, kind: str) -> Path:
    normalized = str(kind)
    if not normalized.startswith("."):
        normalized = "." + normalized
    return expected_mdl.with_name(expected_mdl.stem + normalized)


def _matching_qcs(root: Path, model_rel: str, *, optimized: bool) -> list[Path]:
    matches: list[Path] = []
    wanted = _normalized_model_name(model_rel)
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() == ".qc"
            and path.stem.casefold().endswith("_opt") == optimized
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    for path in candidates:
        try:
            model_name = _inventory_qc(path, family_root=root)[0].model_name
        except (OSError, ValueError) as exc:
            raise CandidateBuildError(
                f"could not inspect QC {path}: {exc}", stage="source-validation"
            ) from exc
        if _normalized_model_name(model_name) == wanted:
            matches.append(path)
    return matches


class _BaseAdapter:
    optimize_script = ""
    needs_heuristic_map = False
    supports_region_overrides = False

    def __init__(
        self,
        *,
        process_runner: ProcessRunner = run_process,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._cancel_event = cancel_event or threading.Event()

    def _validate_tools(self, tools: CandidateTools) -> None:
        for label, path in (
            ("python", tools.python_exe),
            ("blender", tools.blender_exe),
            ("studiomdl", tools.studiomdl_exe),
        ):
            if not path.is_file():
                raise CandidateBuildError(f"{label} tool not found: {path}", stage="tool-validation")
        if not tools.repo_root.is_dir():
            raise CandidateBuildError(
                f"repo root not found: {tools.repo_root}", stage="tool-validation"
            )
        for script in (self.optimize_script, "batch_compile_opt_qc.py"):
            path = tools.repo_root / script
            if not path.is_file():
                raise CandidateBuildError(f"script not found: {path}", stage="tool-validation")
        if self.needs_heuristic_map and (
            tools.heuristic_map is None or not tools.heuristic_map.is_file()
        ):
            raise CandidateBuildError("fidelity heuristic map not found", stage="tool-validation")

    def _prepare_workspace(self, manifest: FamilyManifest, workspace: Path) -> Path:
        source_path = Path(manifest.source_dir)
        original_models_path = Path(manifest.original_models_dir)
        workspace_path = Path(workspace)
        if source_path.is_symlink():
            raise CandidateBuildError("source directory cannot be a symlink", stage="workspace")
        if original_models_path.is_symlink():
            raise CandidateBuildError("original models directory cannot be a symlink", stage="workspace")
        if workspace_path.is_symlink():
            raise CandidateBuildError("workspace cannot be a symlink", stage="workspace")
        source = source_path.expanduser().resolve()
        original_models = original_models_path.expanduser().resolve()
        destination = workspace_path.expanduser().resolve()
        if not source.is_dir():
            raise CandidateBuildError(f"source directory not found: {source}", stage="workspace")
        internal_symlink = _first_internal_symlink(source)
        if internal_symlink is not None:
            raise CandidateBuildError(
                f"source directory contains symlink: {internal_symlink}", stage="workspace"
            )
        if _overlaps(destination, source):
            raise CandidateBuildError(
                "workspace overlap: inside source directory or above it", stage="workspace"
            )
        if _overlaps(destination, original_models):
            raise CandidateBuildError(
                "workspace overlap with original models directory", stage="workspace"
            )
        if destination.exists():
            if not destination.is_dir():
                raise CandidateBuildError("workspace exists and is not a directory", stage="workspace")
            if any(destination.iterdir()):
                raise CandidateBuildError("workspace is not empty", stage="workspace")
        source_qcs = _matching_qcs(source, manifest.model_rel, optimized=False)
        if not source_qcs:
            raise CandidateBuildError(
                f"no source QC declares model {manifest.model_rel}", stage="source-validation"
            )
        if len(source_qcs) > 1:
            raise CandidateBuildError(
                f"ambiguous source QCs for {manifest.model_rel}: {len(source_qcs)}",
                stage="source-validation",
            )
        destination.mkdir(parents=True, exist_ok=True)
        cloned_source = destination / "src"
        shutil.copytree(source, cloned_source)
        (destination / "logs").mkdir()
        return cloned_source

    def _optimize_command(
        self, source: Path, spec: CandidateSpec, workspace: Path, tools: CandidateTools
    ) -> tuple[str, ...]:
        command = [
            str(tools.blender_exe),
            "--background",
            "--python",
            str(tools.repo_root / self.optimize_script),
            "--",
            str(source),
            "--ratio",
            f"{spec.target_ratio:g}",
            "--merge",
            "0",
            "--autosmooth",
            "45",
            "--format",
            "smd",
        ]
        return tuple(command)

    def _after_optimize(self, source: Path, workspace: Path) -> None:
        return None

    def generate(
        self,
        manifest: FamilyManifest,
        spec: CandidateSpec,
        workspace: Path,
        tools: CandidateTools,
    ) -> CandidateBuild:
        if spec.region_overrides and not self.supports_region_overrides:
            raise CandidateBuildError(
                f"{spec.engine} adapter does not support region_overrides",
                stage="candidate-validation",
            )
        workspace = Path(workspace).expanduser().resolve()
        self._validate_tools(tools)
        source = self._prepare_workspace(manifest, workspace)
        commands: list[tuple[str, ...]] = []

        optimize_log = workspace / "logs" / "optimize.log"
        optimize_command = self._optimize_command(source, spec, workspace, tools)
        commands.append(optimize_command)
        result = self._process_runner(
            optimize_command, tools.repo_root.resolve(), optimize_log, self._cancel_event
        )
        if result.returncode != 0:
            raise CandidateBuildError(
                f"optimizer exited with code {result.returncode}",
                stage="optimize",
                log_path=optimize_log,
            )

        try:
            self._after_optimize(source, workspace)
        except Exception as exc:
            report = workspace / "logs" / "vehicle_steer_turn_basis_fix_summary.json"
            raise CandidateBuildError(
                f"post-optimization fix failed: {exc}", stage="steer-fix", log_path=report
            ) from exc

        optimized_qcs = _matching_qcs(source, manifest.model_rel, optimized=True)
        if not optimized_qcs:
            raise CandidateBuildError(
                f"no optimized QC declares model {manifest.model_rel}",
                stage="optimize",
                log_path=optimize_log,
            )
        if len(optimized_qcs) > 1:
            raise CandidateBuildError(
                f"ambiguous optimized QCs for {manifest.model_rel}: {len(optimized_qcs)}",
                stage="optimize",
                log_path=optimize_log,
            )
        optimized_qc = optimized_qcs[0]

        compiled = workspace / "compiled"
        compile_log = workspace / "logs" / "compile.log"
        compile_command = (
            str(tools.python_exe),
            str(tools.repo_root / "batch_compile_opt_qc.py"),
            str(source),
            "--out",
            str(compiled),
            "--studiomdl",
            str(tools.studiomdl_exe),
            "--compile-jobs",
            str(tools.compile_jobs),
            "--no-restore-phy",
        )
        commands.append(compile_command)
        compile_result = self._process_runner(
            compile_command, tools.repo_root.resolve(), compile_log, self._cancel_event
        )
        if compile_result.returncode != 0:
            raise CandidateBuildError(
                f"compiler exited with code {compile_result.returncode}",
                stage="compile",
                log_path=compile_log,
            )

        summary_path = compiled / "compile_summary.json"
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            records = summary["results"]
            if not isinstance(records, list):
                raise TypeError("results must be a list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CandidateBuildError(
                f"compile summary is invalid for {manifest.model_rel}: {exc}",
                stage="compile",
                log_path=compile_log,
            ) from exc
        matching_records = [
            item
            for item in records
            if isinstance(item, Mapping)
            and _normalized_model_name(str(item.get("model_rel") or ""))
            == _normalized_model_name(manifest.model_rel)
        ]
        if len(matching_records) != 1:
            raise CandidateBuildError(
                f"compile summary must contain exactly one record for {manifest.model_rel}; "
                f"found {len(matching_records)}",
                stage="compile",
                log_path=compile_log,
            )
        record = matching_records[0]
        if record.get("status") != "ok":
            raise CandidateBuildError(
                f"compile record failed for {manifest.model_rel}: {record.get('status')}",
                stage="compile",
                log_path=compile_log,
            )
        record_returncode = record.get("returncode")
        if type(record_returncode) is not int:
            raise CandidateBuildError(
                f"compile record has invalid returncode for {manifest.model_rel}",
                stage="compile",
                log_path=compile_log,
            )
        if record_returncode != 0:
            raise CandidateBuildError(
                f"compile record returncode is {record_returncode} for {manifest.model_rel}",
                stage="compile",
                log_path=compile_log,
            )

        compiled_models = compiled / "models"
        if compiled_models.is_symlink() or not compiled_models.is_dir():
            raise CandidateBuildError(
                f"compiled models directory is missing or a symlink: {compiled_models}",
                stage="compile",
                log_path=compile_log,
            )
        compiled_models_root = compiled_models.resolve()
        model_relative_path = Path(*manifest.model_rel.replace("\\", "/").split("/"))
        expected_mdl = (compiled_models / model_relative_path).resolve()
        if not _is_within(expected_mdl, compiled_models_root):
            raise CandidateBuildError(
                f"expected_mdl escapes compiled models root: {expected_mdl}",
                stage="compile",
                log_path=compile_log,
            )
        record_expected_raw = record.get("expected_mdl")
        try:
            record_expected = Path(str(record_expected_raw)).expanduser().resolve()
        except (OSError, ValueError, TypeError) as exc:
            raise CandidateBuildError(
                f"compile record has invalid expected_mdl for {manifest.model_rel}",
                stage="compile",
                log_path=compile_log,
            ) from exc
        if not record_expected_raw or record_expected != expected_mdl or not expected_mdl.is_file():
            raise CandidateBuildError(
                f"compile record expected_mdl does not match compiled output: {record_expected_raw}",
                stage="compile",
                log_path=compile_log,
            )
        for kind in manifest.required_artifact_kinds:
            artifact = _required_artifact_path(expected_mdl, kind)
            if (
                artifact.is_symlink()
                or not artifact.is_file()
                or not _is_within(artifact.resolve(), compiled_models_root)
            ):
                raise CandidateBuildError(
                    f"required compiled artifact is missing or invalid: {artifact}",
                    stage="compile",
                    log_path=compile_log,
                )

        source_suffixes = {".mdl", ".vvd", ".vtx", ".ani", ".phy"}
        for path in compiled_models.rglob("*"):
            if path.suffix.casefold() in source_suffixes and path.is_symlink():
                raise CandidateBuildError(
                    f"compiled Source artifact cannot be a symlink: {path}",
                    stage="compile",
                    log_path=compile_log,
                )

        provenance: dict[str, str] = {}
        artifacts = sorted(
            (
                path
                for path in compiled_models.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and _is_within(path.resolve(), compiled_models_root)
                and path.suffix.casefold() in source_suffixes
            ),
            key=lambda path: path.relative_to(compiled_models).as_posix().casefold(),
        )
        provenance = {
            path.relative_to(compiled_models).as_posix(): "candidate-compile" for path in artifacts
        }

        return CandidateBuild(
            spec=spec,
            workspace=workspace,
            optimized_qc=optimized_qc,
            compiled_models_dir=compiled_models,
            compile_record=dict(record),
            provenance=provenance,
            commands=tuple(commands),
        )


class BlenderAdapter(_BaseAdapter):
    optimize_script = "batch_optimize_qc.py"


class FidelityAdapter(_BaseAdapter):
    optimize_script = "batch_optimize_round_parts_policy.py"
    needs_heuristic_map = True

    def _optimize_command(
        self, source: Path, spec: CandidateSpec, workspace: Path, tools: CandidateTools
    ) -> tuple[str, ...]:
        command = list(super()._optimize_command(source, spec, workspace, tools))
        command.extend(
            (
                "--heuristic-map",
                str(tools.heuristic_map),
                "--ground-final-autosmooth",
                "35",
                "--ground-weighted-mode",
                "FACE_AREA_WITH_ANGLE",
                "--ground-weighted-weight",
                "50",
                "--ground-shade-smooth",
                "--wheel-variant",
                "silhouette_floor_20",
                "--embedded-variant",
                "floor_24",
                "--summary-dir",
                str(workspace / "logs" / "round_parts"),
            )
        )
        return tuple(command)

    def _after_optimize(self, source: Path, workspace: Path) -> None:
        apply_under_root(
            source,
            report_path=workspace / "logs" / "vehicle_steer_turn_basis_fix_summary.json",
        )


class MeshoptimizerAdapter(_BaseAdapter):
    optimize_script = "batch_optimize_maximum.py"
    supports_region_overrides = True

    def _validate_tools(self, tools: CandidateTools) -> None:
        super()._validate_tools(tools)
        if tools.meshopt_dll is None or not tools.meshopt_dll.is_file():
            raise CandidateBuildError(
                "meshoptimizer bridge DLL not found", stage="tool-validation"
            )

    def _optimize_command(
        self, source: Path, spec: CandidateSpec, workspace: Path, tools: CandidateTools
    ) -> tuple[str, ...]:
        candidate_path = workspace / "candidate.json"
        with candidate_path.open("x", encoding="utf-8") as stream:
            json.dump(spec.cache_payload(), stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        return (
            str(tools.blender_exe),
            "--background",
            "--python",
            str(tools.repo_root / self.optimize_script),
            "--",
            str(source),
            "--candidate-json",
            str(candidate_path),
            "--meshopt-dll",
            str(tools.meshopt_dll),
        )
