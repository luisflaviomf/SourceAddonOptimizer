from __future__ import annotations

from pathlib import Path


def build_commands(
    *,
    repo: Path,
    workspace: Path,
    candidate_json: Path,
    blender_exe: Path,
    studiomdl_exe: Path,
    python_exe: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Build Task 8D commands; both tools intentionally receive the source root."""
    repo = Path(repo).resolve()
    workspace = Path(workspace).resolve()
    candidate_json = Path(candidate_json).resolve()
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
    return optimize, compile_command
