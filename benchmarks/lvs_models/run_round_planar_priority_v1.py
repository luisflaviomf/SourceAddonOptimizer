from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence


@dataclass(frozen=True)
class ExperimentArm:
    subject: str
    name: str
    source: Path
    workspace: Path
    candidate_json: Path
    candidate: dict[str, object]
    optimize_command: tuple[str, ...]
    compile_command: tuple[str, ...]


def _candidate(subject: str, name: str) -> dict[str, object]:
    if name == "b050":
        ratio = 0.5
        strategy = "blender-adaptive-v1"
        candidate_id = f"{subject}-b050"
    elif name == "planar-only":
        ratio = 1.0
        strategy = "round-planar-priority-v1"
        candidate_id = f"{subject}-round-planar-only"
    elif name == "round-planar-priority":
        ratio = 0.35
        strategy = "round-planar-priority-v1"
        candidate_id = f"{subject}-round-planar-priority-r035"
    else:
        raise ValueError(f"unknown experiment arm: {name}")
    return {
        "candidate_id": candidate_id,
        "engine": "blender",
        "ratio": ratio,
        "target_error": 0.0,
        "update_vertices": True,
        "region_overrides": [],
        "strategy": strategy,
        "transfer": "blender-native-v1",
    }


def build_experiment_arms(
    *,
    repo: Path,
    source_root: Path,
    output_root: Path,
    blender_exe: Path,
    studiomdl_exe: Path,
    python_exe: Path,
) -> tuple[ExperimentArm, ...]:
    """Build immutable R&D commands without touching disk or starting Blender."""
    repo = Path(repo).resolve()
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    sources = (
        ("wheel", source_root / "diggercars" / "pontiac_transam3" / "wheel"),
        (
            "steering-wheel",
            source_root / "diggercars" / "dodge_charger" / "charger",
        ),
    )
    arms = []
    for subject, source in sources:
        for name in ("b050", "planar-only", "round-planar-priority"):
            workspace = output_root / subject / name / "source"
            candidate_json = workspace / "candidate.json"
            candidate = _candidate(subject, name)
            optimize = (
                str(Path(blender_exe)),
                "--background",
                "--python",
                str(repo / "batch_optimize_maximum.py"),
                "--",
                str(workspace),
                "--candidate-json",
                str(candidate_json),
            )
            compile_command = (
                str(Path(python_exe)),
                str(repo / "batch_compile_opt_qc.py"),
                str(workspace),
                "--out",
                str(workspace / "compiled"),
                "--studiomdl",
                str(Path(studiomdl_exe)),
                "--compile-jobs",
                "1",
                "--no-restore-phy",
            )
            arms.append(ExperimentArm(
                subject,
                name,
                source,
                workspace,
                candidate_json,
                candidate,
                optimize,
                compile_command,
            ))
    return tuple(arms)


@contextmanager
def exclusive_blender_lock(path: Path) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"pid={os.getpid()} token={secrets.token_hex(16)}\n".encode("ascii")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Blender experiment lock is busy: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            if path.read_bytes() == payload:
                path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _stage_arm(arm: ExperimentArm) -> None:
    if not arm.source.is_dir():
        raise FileNotFoundError(f"experiment source is missing: {arm.source}")
    if arm.workspace.exists():
        raise FileExistsError(f"experiment workspace already exists: {arm.workspace}")
    arm.workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(arm.source, arm.workspace, copy_function=shutil.copy2)
    arm.candidate_json.write_text(
        json.dumps(arm.candidate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def execute_experiment(
    arms: Sequence[ExperimentArm],
    *,
    lock_path: Path,
    runner: Callable[[tuple[str, ...]], object] | None = None,
) -> None:
    """Stage and execute arms only while holding the explicit shared Blender lock."""
    if not arms:
        raise ValueError("experiment requires at least one arm")
    invoke = runner or (
        lambda command: subprocess.run(command, check=True, shell=False)
    )
    with exclusive_blender_lock(lock_path):
        for arm in arms:
            _stage_arm(arm)
            invoke(arm.optimize_command)
            invoke(arm.compile_command)
