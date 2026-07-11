from __future__ import annotations

import json
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


@dataclass(frozen=True)
class CandidateTools:
    python_exe: Path
    blender_exe: Path
    studiomdl_exe: Path
    repo_root: Path
    heuristic_map: Path | None = None
    compile_jobs: int = 1

    def __post_init__(self) -> None:
        for name in ("python_exe", "blender_exe", "studiomdl_exe", "repo_root"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        if self.heuristic_map is not None:
            object.__setattr__(
                self, "heuristic_map", Path(self.heuristic_map).expanduser().resolve()
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
        object.__setattr__(self, "compile_record", MappingProxyType(dict(self.compile_record)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


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
        workspace_path = Path(workspace)
        if source_path.is_symlink():
            raise CandidateBuildError("source directory cannot be a symlink", stage="workspace")
        if workspace_path.is_symlink():
            raise CandidateBuildError("workspace cannot be a symlink", stage="workspace")
        source = source_path.expanduser().resolve()
        destination = workspace_path.expanduser().resolve()
        if not source.is_dir():
            raise CandidateBuildError(f"source directory not found: {source}", stage="workspace")
        if _is_within(destination, source):
            raise CandidateBuildError("workspace cannot be inside source directory", stage="workspace")
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
            record = next(
                item
                for item in records
                if _normalized_model_name(str(item.get("model_rel") or ""))
                == _normalized_model_name(manifest.model_rel)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
            raise CandidateBuildError(
                f"compile summary has no record for {manifest.model_rel}: {exc}",
                stage="compile",
                log_path=compile_log,
            ) from exc
        if str(record.get("status", "")).casefold() != "ok":
            raise CandidateBuildError(
                f"compile record failed for {manifest.model_rel}: {record.get('status')}",
                stage="compile",
                log_path=compile_log,
            )

        compiled_models = compiled / "models"
        provenance: dict[str, str] = {}
        if compiled_models.is_dir():
            artifacts = sorted(
                (
                    path
                    for path in compiled_models.rglob("*")
                    if path.is_file() and path.suffix.casefold() in {".mdl", ".vvd", ".vtx", ".ani", ".phy"}
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
