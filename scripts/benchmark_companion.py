#!/usr/bin/env python3
"""Run a reproducible, paired Maximum-v2 benchmark with and without runtime evidence."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
from typing import NamedTuple, Sequence


SCRIPT_REPOSITORY = Path(__file__).resolve().parents[1]
if str(SCRIPT_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPOSITORY))

from maximum_optimizer.benchmarking import (  # noqa: E402
    FamilyResult,
    Lane,
    analyze_family_result,
    assert_isolated_lane_paths,
    build_lane_command,
    copy_family_input,
    load_corpus,
    result_payload,
    run_monitored,
    sha256_file,
    write_json,
)
from maximum_optimizer.companion_contract import read_bundle  # noqa: E402


LANE_NO_PROFILE = "maximum-no-profile"
LANE_PROFILE = "maximum-profile"
LANES = {
    LANE_NO_PROFILE: Lane(LANE_NO_PROFILE, "maximum", None, "current"),
    LANE_PROFILE: Lane(LANE_PROFILE, "maximum", None, "current"),
}
CORPUS_RELATIVE = Path("benchmarks/lvs_models_adaptive/corpus.json")


class ScheduleItem(NamedTuple):
    family_id: str
    phase: str
    index: int
    lane: str


def _key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _overlaps(first: Path, second: Path) -> bool:
    left = _key(first)
    right = _key(second)
    try:
        common = os.path.commonpath((left, right))
    except ValueError:
        return False
    return common == left or common == right


def validate_evidence_root(candidate: Path, protected_roots: Sequence[Path]) -> Path:
    resolved = Path(candidate).resolve()
    if resolved.parent == resolved:
        raise ValueError("evidence root cannot be a filesystem root")
    for protected in protected_roots:
        if _overlaps(resolved, Path(protected)):
            raise ValueError(f"evidence root overlaps protected root: {resolved} / {Path(protected).resolve()}")
    return resolved


def validate_run_shape(
    families: Sequence[str], *, repetitions: int, warmups: int, pilot: bool
) -> None:
    if len(set(families)) != len(families):
        raise ValueError("family selection contains duplicates")
    if pilot:
        if len(families) < 1 or repetitions < 1 or warmups < 0:
            raise ValueError("pilot requires a family and at least one measured repetition")
        return
    if not 5 <= len(families) <= 10:
        raise ValueError("acceptance benchmark requires 5 to 10 families")
    if repetitions < 3:
        raise ValueError("acceptance benchmark requires at least 3 measured repetitions per lane")
    if warmups < 1:
        raise ValueError("acceptance benchmark requires at least one warm-up per lane")


def build_schedule(
    families: Sequence[str], *, repetitions: int, warmups: int
) -> tuple[ScheduleItem, ...]:
    schedule: list[ScheduleItem] = []
    for family_id in families:
        for index in range(1, warmups + 1):
            for lane in (LANE_NO_PROFILE, LANE_PROFILE):
                schedule.append(ScheduleItem(family_id, "warmup", index, lane))
        for index in range(1, repetitions + 1):
            order = (LANE_NO_PROFILE, LANE_PROFILE) if index % 2 else (LANE_PROFILE, LANE_NO_PROFILE)
            for lane in order:
                schedule.append(ScheduleItem(family_id, "measured", index, lane))
    return tuple(schedule)


def build_companion_command(
    lane_id: str,
    *,
    repo_root: Path,
    addon_path: Path,
    work_path: Path,
    blender: Path,
    studiomdl: Path,
    framework_root: Path | None,
    profile_root: Path,
) -> list[str]:
    try:
        LANES[lane_id]
    except KeyError as exc:
        raise ValueError(f"unknown Companion benchmark lane: {lane_id}") from exc
    # build_lane_command keys current Maximum behavior (including the optional
    # framework resolver) from this canonical implementation identifier. The
    # result still carries the A/B lane identifier separately.
    lane = Lane("maximum-adaptive-v2", "maximum", None, "current")
    command = build_lane_command(
        lane,
        repo_root=Path(repo_root),
        addon_path=Path(addon_path),
        work_path=Path(work_path),
        blender=Path(blender),
        studiomdl=Path(studiomdl),
        framework_root=Path(framework_root) if framework_root else None,
    )
    if lane_id == LANE_PROFILE:
        command.extend(("--maximum-runtime-profile", str(Path(profile_root).resolve())))
    return command


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True, help="Current repository containing WPF and worker.")
    parser.add_argument("--addon-root", type=Path, required=True, help="Immutable lvs_cars_pack source.")
    parser.add_argument("--framework-root", type=Path, help="Optional lvs_framework resolver root.")
    parser.add_argument("--profile-root", type=Path, required=True, help="Approved sao-runtime-profile-v1 bundle.")
    parser.add_argument("--evidence-root", type=Path, required=True, help="External raw-evidence directory.")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--studiomdl", type=Path, required=True)
    parser.add_argument("--family", action="append", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--pilot", action="store_true", help="Allow a non-acceptance one-family timing pilot.")
    parser.add_argument("--pilot-lane", choices=(LANE_NO_PROFILE, LANE_PROFILE), help="Run only one lane; valid only with --pilot.")
    parser.add_argument("--resume", action="store_true", help="Reuse completed run directories after manifest validation.")
    return parser.parse_args()


def _require_repository(root: Path) -> None:
    required = (
        "build_optimized_addon.py",
        "worker/worker_main.py",
        "GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj",
        CORPUS_RELATIVE.as_posix(),
    )
    missing = [relative for relative in required if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"repo root is missing required WPF/worker files: {missing}")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip() if completed.returncode == 0 else f"unavailable:{completed.stderr.strip()}"


def _command_output(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{exc}"
    text = (completed.stdout or completed.stderr).strip()
    return text[:4096] if text else f"exit:{completed.returncode}"


def _existing_parent(path: Path) -> Path:
    current = Path(path).resolve()
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _disk_snapshot(path: Path) -> dict[str, int]:
    usage = shutil.disk_usage(_existing_parent(path))
    return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}


def _environment_manifest(
    *, repo_root: Path, addon_root: Path, framework_root: Path | None,
    profile_root: Path, evidence_root: Path, blender: Path, studiomdl: Path,
    pilot: bool,
) -> dict[str, object]:
    tools = {
        "build_optimized_addon": repo_root / "build_optimized_addon.py",
        "worker": repo_root / "worker/worker_main.py",
        "blender": blender,
        "studiomdl": studiomdl,
        "profile_index": profile_root / "run-index.json",
    }
    return {
        "schema": 1,
        "pilot": pilot,
        "platform": platform.platform(),
        "python": sys.version,
        "repo_root": str(repo_root),
        "repo_head": _git(repo_root, "rev-parse", "HEAD"),
        "repo_status": _git(repo_root, "status", "--short"),
        "addon_root": str(addon_root),
        "framework_root": str(framework_root) if framework_root else None,
        "profile_root": str(profile_root),
        "evidence_root": str(evidence_root),
        "power_plan": _command_output(("powercfg", "/getactivescheme")),
        "blender_version": _command_output((str(blender), "--version")),
        "disk_before": _disk_snapshot(evidence_root),
        "cache_policy": "one unmeasured warm-up per lane; OS filesystem cache is not flushed; measured runs are warm-cache",
        "tools": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in tools.items() if path.is_file()
        },
    }


def _tree_hashes(root: Path, suffixes: tuple[str, ...], *, limit: int | None = None) -> list[dict[str, object]]:
    paths = [
        path for path in Path(root).rglob("*")
        if path.is_file() and any(path.name.casefold().endswith(suffix) for suffix in suffixes)
    ]
    paths.sort(key=lambda value: value.relative_to(root).as_posix().casefold())
    if limit is not None:
        paths = paths[:limit]
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]


def _artifact_manifest(output_path: Path, work_path: Path) -> dict[str, object]:
    compiled = _tree_hashes(output_path / "models", (".mdl", ".vvd", ".dx90.vtx", ".phy"))
    selected_smd = _tree_hashes(work_path, (".smd",), limit=32)
    digest = hashlib.sha256()
    for item in compiled:
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    report_files = _tree_hashes(work_path, ("maximum_adaptive_report.json",))
    return {
        "compiled_tree_sha256": digest.hexdigest(),
        "compiled_files": compiled,
        "selected_smd_files": selected_smd,
        "maximum_reports": report_files,
        "companion_dependency_hits": [
            item["path"] for item in compiled if "sourceaddonoptimizer" in str(item["path"]).casefold()
        ],
    }


def _median_range(values: Sequence[float]) -> dict[str, float]:
    return {
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def _write_summary(root: Path, results: Sequence[FamilyResult]) -> None:
    measured = [result for result in results if "measured-" in Path(result.work_path).as_posix()]
    grouped: dict[tuple[str, str], list[FamilyResult]] = defaultdict(list)
    for result in measured:
        grouped[(result.family_id, result.lane)].append(result)
    families: dict[str, object] = {}
    for (family_id, lane), values in sorted(grouped.items()):
        family = families.setdefault(family_id, {})
        assert isinstance(family, dict)
        reductions = [
            (value.original_comparable - value.final_comparable) * 100.0 / max(1, value.original_comparable)
            for value in values
        ]
        family[lane] = {
            "runs": len(values),
            "status": sorted({value.status for value in values}),
            "wall_seconds": _median_range([value.wall_seconds for value in values]),
            "cpu_seconds": _median_range([value.cpu_seconds for value in values]),
            "peak_working_set_bytes": _median_range([float(value.peak_working_set_bytes) for value in values]),
            "final_comparable_bytes": _median_range([float(value.final_comparable) for value in values]),
            "reduction_percent": _median_range(reductions),
            "final_triangles": _median_range([float(value.final_triangles) for value in values]),
            "final_vertices": _median_range([float(value.final_vertices) for value in values]),
            "optimized_models": _median_range([float(value.optimized_models) for value in values]),
            "preserved_models": _median_range([float(value.preserved_models) for value in values]),
            "targeted_renders": _median_range([float(value.targeted_renders) for value in values]),
            "full_renders": _median_range([float(value.full_renders) for value in values]),
            "studiomdl_compiles": _median_range([float(value.studiomdl_compiles) for value in values]),
            "simplifier_evaluations": _median_range([float(value.simplifier_evaluations) for value in values]),
            "integrity_failures": sorted({failure for value in values for failure in value.integrity_failures}),
        }
    write_json(root / "summary.json", {"schema": 1, "measured_only": True, "families": families})


def _run_root(evidence_root: Path, item: ScheduleItem) -> Path:
    return evidence_root / "runs" / item.family_id / f"{item.phase}-{item.index:02d}" / item.lane


def main() -> int:
    args = _arguments()
    repo_root = args.repo_root.resolve()
    addon_root = args.addon_root.resolve()
    framework_root = args.framework_root.resolve() if args.framework_root else None
    profile_root = args.profile_root.resolve()
    blender = args.blender.resolve()
    studiomdl = args.studiomdl.resolve()
    local_work = Path(os.environ.get("LOCALAPPDATA", repo_root / ".missing-localappdata")) / "GmodAddonOptimizer/work"
    protected = [repo_root, addon_root, local_work]
    if framework_root:
        protected.append(framework_root)
    evidence_root = validate_evidence_root(args.evidence_root, protected)
    validate_run_shape(tuple(args.family), repetitions=args.repetitions, warmups=args.warmups, pilot=args.pilot)
    if args.pilot_lane and not args.pilot:
        raise ValueError("--pilot-lane is valid only with --pilot")
    _require_repository(repo_root)
    for path, label in ((addon_root, "addon"), (profile_root, "profile")):
        if not path.is_dir():
            raise FileNotFoundError(f"{label} root not found: {path}")
    if framework_root and not framework_root.is_dir():
        raise FileNotFoundError(f"framework root not found: {framework_root}")
    for path, label in ((blender, "Blender"), (studiomdl, "StudioMDL")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    profile = read_bundle(profile_root, expected_schema="sao-runtime-profile-v1")

    corpus = load_corpus(repo_root / CORPUS_RELATIVE)
    by_id = {family.id: family for family in corpus.families}
    missing = sorted(set(args.family) - set(by_id))
    if missing:
        raise ValueError(f"unknown benchmark families: {missing}")
    selected = [by_id[value] for value in args.family]
    schedule = build_schedule(tuple(args.family), repetitions=args.repetitions, warmups=args.warmups)
    if args.pilot_lane:
        schedule = tuple(item for item in schedule if item.lane == args.pilot_lane)
    if evidence_root.exists() and any(evidence_root.iterdir()) and not args.resume:
        raise FileExistsError(f"evidence root is not empty; pass --resume only for this exact run: {evidence_root}")
    evidence_root.mkdir(parents=True, exist_ok=True)

    environment = _environment_manifest(
        repo_root=repo_root, addon_root=addon_root, framework_root=framework_root,
        profile_root=profile_root, evidence_root=evidence_root, blender=blender,
        studiomdl=studiomdl, pilot=args.pilot,
    )
    selection = {
        "schema": 1,
        "profile": {"run_id": str(profile.run_id), "index_sha256": profile.index_sha256},
        "repetitions": args.repetitions,
        "warmups": args.warmups,
        "families": [
            {
                "id": family.id,
                "partition": family.partition,
                "include": list(family.include),
                "focus_regions": list(family.focus_regions),
                "input_tree_sha256": family.tree_sha256,
            }
            for family in selected
        ],
        "schedule": [item._asdict() for item in schedule],
    }
    manifest_path = evidence_root / "benchmark-plan.json"
    if args.resume and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != selection:
            raise ValueError("--resume benchmark plan does not match existing evidence")
    else:
        write_json(manifest_path, selection)
        write_json(evidence_root / "environment.json", environment)

    results: list[FamilyResult] = []
    for position, item in enumerate(schedule, 1):
        family = by_id[item.family_id]
        run_root = _run_root(evidence_root, item)
        result_path = run_root / "result.json"
        if args.resume and result_path.is_file():
            result = FamilyResult.from_dict(json.loads(result_path.read_text(encoding="utf-8")))
            results.append(result)
            print(f"[COMPANION BENCH] reuse {position}/{len(schedule)} {item}", flush=True)
            continue
        if run_root.exists():
            raise FileExistsError(f"incomplete run exists and will not be overwritten: {run_root}")
        addon_path = run_root / "source" / family.id
        work_path = run_root / "work"
        output_path = addon_path.parent / f"{addon_path.name}_benchmark_output"
        log_path = run_root / "run.log"
        assert_isolated_lane_paths(addon_path, work_path, output_path)
        copy_family_input(addon_root, family, addon_path)
        command = build_companion_command(
            item.lane,
            repo_root=repo_root,
            addon_path=addon_path,
            work_path=work_path,
            blender=blender,
            studiomdl=studiomdl,
            framework_root=framework_root,
            profile_root=profile_root,
        )
        write_json(run_root / "run-manifest.json", {
            "schema": 1, "schedule_position": position, "schedule_total": len(schedule),
            "family": family.id, "phase": item.phase, "index": item.index,
            "lane": item.lane, "command": command, "input_tree_sha256": family.tree_sha256,
            "profile_index_sha256": profile.index_sha256 if item.lane == LANE_PROFILE else None,
        })
        print(f"[COMPANION BENCH] start {position}/{len(schedule)} {item}", flush=True)
        measurement = run_monitored(command, cwd=repo_root, log_path=log_path)
        result = analyze_family_result(
            family, LANES[item.lane], addon_path=addon_path, output_path=output_path,
            work_path=work_path, log_path=log_path, measurement=measurement,
        )
        write_json(result_path, result_payload(result))
        write_json(run_root / "artifacts.json", _artifact_manifest(output_path, work_path))
        results.append(result)
        _write_summary(evidence_root, results)
        print(
            f"[COMPANION BENCH] done {item.family_id}/{item.lane} "
            f"status={result.status} wall={result.wall_seconds:.2f}s bytes={result.final_comparable}",
            flush=True,
        )
        if result.status != "ok":
            return 1

    environment["disk_after"] = _disk_snapshot(evidence_root)
    write_json(evidence_root / "environment.json", environment)
    _write_summary(evidence_root, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
