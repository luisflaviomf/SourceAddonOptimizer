#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maximum_optimizer.benchmarking import (  # noqa: E402
    LANES,
    aggregate_payload,
    aggregate_results,
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


CORPUS_PATH = Path(__file__).with_name("corpus.json")
EXPECTED_HISTORICAL = "8812c7ad353ce7e4a368ec96a0922e9e4b6bce65"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated three-lane Models Maximum benchmarks.")
    parser.add_argument("--partition", choices=("development", "holdout", "all"), required=True)
    parser.add_argument("--addon-root", type=Path, required=True)
    parser.add_argument("--framework-root", type=Path, required=True)
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blender", type=Path, default=Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"))
    parser.add_argument("--studiomdl", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common\GarrysMod\bin\studiomdl.exe"))
    parser.add_argument("--cold-cache", action="store_true")
    parser.add_argument("--frozen-profile", action="store_true")
    parser.add_argument("--lane", action="append", choices=tuple(lane.id for lane in LANES))
    parser.add_argument("--family", action="append")
    return parser.parse_args()


def _git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot resolve Git HEAD for {root}")
    return completed.stdout.strip()


def _archive_existing(path: Path, output_root: Path) -> None:
    if not path.exists():
        return
    path = path.resolve()
    output_root = output_root.resolve()
    if os.path.commonpath((str(path), str(output_root))) != str(output_root) or path == output_root:
        raise ValueError(f"refusing to archive path outside benchmark root: {path}")
    archive = output_root / ".archive" / f"{path.parent.name}-{path.name}-{time.time_ns()}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    path.replace(archive)


def _tool_manifest(
    *,
    lane_id: str,
    repo_root: Path,
    blender: Path,
    studiomdl: Path,
    family_sha256: str,
    command: list[str],
) -> dict[str, object]:
    paths = {
        "worker_script": repo_root / "build_optimized_addon.py",
        "crowbar": repo_root / "CrowbarCommandLineDecomp.exe",
        "blender": blender,
        "studiomdl": studiomdl,
    }
    if lane_id == "maximum-adaptive-v2":
        paths["profile"] = repo_root / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json"
        paths["meshopt_bridge"] = repo_root / "maximum_optimizer" / "native" / "bin" / "win-x64" / "meshopt_bridge.dll"
    return {
        "schema": 1,
        "lane": lane_id,
        "repo_root": str(repo_root.resolve()),
        "repo_head": _git_head(repo_root),
        "input_tree_sha256": family_sha256,
        "command": command,
        "tools": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in paths.items()
            if path.is_file()
        },
    }


def main() -> int:
    args = _arguments()
    addon_root = args.addon_root.resolve()
    framework_root = args.framework_root.resolve()
    historical_root = args.historical_root.resolve()
    output_root = args.out.resolve()
    blender = args.blender.resolve()
    studiomdl = args.studiomdl.resolve()
    for path, label in ((addon_root, "addon"), (framework_root, "framework"), (historical_root, "historical repo")):
        if not path.is_dir():
            raise FileNotFoundError(f"{label} root not found: {path}")
    for path, label in ((blender, "Blender"), (studiomdl, "StudioMDL")):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
    historical_head = _git_head(historical_root)
    if historical_head != EXPECTED_HISTORICAL:
        raise RuntimeError(f"historical lane is {historical_head}, expected {EXPECTED_HISTORICAL}")
    if args.frozen_profile:
        profile = json.loads((REPO_ROOT / "maximum_optimizer/profiles/maximum-adaptive-v2.json").read_text(encoding="utf-8"))
        if profile.get("calibrated") is not True:
            raise RuntimeError("--frozen-profile requires a calibrated profile")

    corpus = load_corpus(CORPUS_PATH)
    families = list(corpus.partition(args.partition))
    if args.family:
        selected = set(args.family)
        families = [family for family in families if family.id in selected]
        missing = selected - {family.id for family in families}
        if missing:
            raise ValueError(f"families are not in selected partition: {sorted(missing)}")
    lanes = [lane for lane in LANES if not args.lane or lane.id in set(args.lane)]
    if not families or not lanes:
        raise ValueError("benchmark selection is empty")

    output_root.mkdir(parents=True, exist_ok=True)
    completed_results = []
    for family in families:
        for lane in lanes:
            lane_root = output_root / family.partition / family.id / lane.id
            if lane_root.exists():
                if not args.cold_cache:
                    result_path = lane_root / "result.json"
                    if result_path.is_file():
                        from maximum_optimizer.benchmarking import FamilyResult

                        completed_results.append(FamilyResult.from_dict(json.loads(result_path.read_text(encoding="utf-8"))))
                        print(f"[BENCH] reuse {family.id}/{lane.id}")
                        continue
                    raise FileExistsError(f"incomplete lane exists; use --cold-cache: {lane_root}")
                _archive_existing(lane_root, output_root)

            addon_path = lane_root / "source" / family.id
            work_path = lane_root / "work"
            expected_output = addon_path.parent / f"{addon_path.name}_benchmark_output"
            log_path = lane_root / "run.log"
            assert_isolated_lane_paths(addon_path, work_path, expected_output)
            copy_family_input(addon_root, family, addon_path)
            repo_root = historical_root if lane.implementation == "historical" else REPO_ROOT
            command = build_lane_command(
                lane,
                repo_root=repo_root,
                addon_path=addon_path,
                work_path=work_path,
                blender=blender,
                studiomdl=studiomdl,
                framework_root=framework_root,
            )
            write_json(
                lane_root / "run-manifest.json",
                _tool_manifest(
                    lane_id=lane.id,
                    repo_root=repo_root,
                    blender=blender,
                    studiomdl=studiomdl,
                    family_sha256=family.tree_sha256,
                    command=command,
                ),
            )
            print(f"[BENCH] start {family.id}/{lane.id}")
            measurement = run_monitored(command, cwd=repo_root, log_path=log_path)
            result = analyze_family_result(
                family,
                lane,
                addon_path=addon_path,
                output_path=expected_output,
                work_path=work_path,
                log_path=log_path,
                measurement=measurement,
            )
            write_json(lane_root / "result.json", result_payload(result))
            completed_results.append(result)
            print(
                f"[BENCH] done {family.id}/{lane.id}: status={result.status} "
                f"reduction={(result.original_comparable-result.final_comparable)*100/max(1,result.original_comparable):.2f}% "
                f"time={result.wall_seconds:.1f}s"
            )

    summary: dict[str, object] = {"schema": 1, "partition": args.partition, "lanes": {}}
    for lane in lanes:
        lane_results = [
            result for result in completed_results
            if result.lane == lane.id and result.family_id in {family.id for family in families}
        ]
        aggregate = aggregate_results(lane.id, lane_results)
        summary["lanes"][lane.id] = aggregate_payload(aggregate)  # type: ignore[index]
    write_json(output_root / f"{args.partition}-summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(result.exit_code == 0 for result in completed_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
