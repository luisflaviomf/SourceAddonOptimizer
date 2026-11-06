#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maximum_optimizer.benchmarking import LANES, aggregate_results, load_corpus, load_family_results, sha256_file  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit reproducibility and promotion gates for LVS Models benchmark results.")
    parser.add_argument("--partition", choices=("development", "holdout", "all"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--lanes", help="Comma-separated completed lane IDs; defaults to all lanes.")
    parser.add_argument("--visual-manifest", type=Path, help="Visual manifest to audit instead of <root>/panels.")
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--require-promotion", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    root = args.root.resolve()
    corpus = load_corpus(Path(__file__).with_name("corpus.json"))
    expected_families = {family.id for family in corpus.partition(args.partition)}
    results = load_family_results(root, args.partition)
    lane_by_id = {lane.id: lane for lane in LANES}
    lane_ids = tuple(value.strip() for value in args.lanes.split(",") if value.strip()) if args.lanes else tuple(lane_by_id)
    unknown_lanes = set(lane_ids) - set(lane_by_id)
    if unknown_lanes:
        raise ValueError(f"unknown lanes: {sorted(unknown_lanes)}")
    selected_lanes = tuple(lane_by_id[lane_id] for lane_id in lane_ids)
    failures: list[str] = []
    by_lane = {}
    for lane in selected_lanes:
        lane_results = tuple(result for result in results if result.lane == lane.id and result.family_id in expected_families)
        by_lane[lane.id] = lane_results
        found = {result.family_id for result in lane_results}
        if found != expected_families:
            failures.append(f"{lane.id}: incomplete families {sorted(expected_families - found)}")
        for result in lane_results:
            if result.exit_code != 0 or result.status != "ok":
                failures.append(f"{result.family_id}/{lane.id}: status={result.status} exit={result.exit_code}")
            if result.final_dx80_bytes or result.dx90_count != result.source_models:
                failures.append(f"{result.family_id}/{lane.id}: DX90/DX80 integrity failed")
            if result.integrity_failures:
                failures.extend(f"{result.family_id}/{lane.id}: {value}" for value in result.integrity_failures)
            manifest_path = root / result.partition / result.family_id / result.lane / "run-manifest.json"
            if not manifest_path.is_file():
                failures.append(f"{result.family_id}/{lane.id}: missing run manifest")
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("input_tree_sha256") != result.input_tree_sha256:
                failures.append(f"{result.family_id}/{lane.id}: input hash mismatch")
            for name, tool in manifest.get("tools", {}).items():
                path = Path(tool.get("path", ""))
                if not path.is_file() or sha256_file(path) != tool.get("sha256"):
                    failures.append(f"{result.family_id}/{lane.id}: tool hash changed ({name})")

    aggregates = {lane.id: aggregate_results(lane.id, by_lane[lane.id]) for lane in selected_lanes}
    if args.require_images:
        manifest_path = args.visual_manifest.resolve() if args.visual_manifest else root / "panels" / "visual-manifest.json"
        if not manifest_path.is_file():
            failures.append("missing visual manifest")
        else:
            visual = json.loads(manifest_path.read_text(encoding="utf-8"))
            panel_families = {item.get("family_id") for item in visual.get("panels", []) if Path(item.get("panel", "")).is_file()}
            missing = expected_families - panel_families
            if missing:
                failures.append(f"missing image panels: {sorted(missing)}")

    if args.require_promotion and all(by_lane.get(lane, ()) for lane in ("normal-safe", "maximum-adaptive-v2")):
        normal = aggregates["normal-safe"]
        adaptive = aggregates["maximum-adaptive-v2"]
        historical = aggregates.get("maximum-8812c7a")
        comparison_bytes = normal.final_comparable if historical is None else min(normal.final_comparable, historical.final_comparable)
        if adaptive.final_comparable >= comparison_bytes:
            failures.append("adaptive comparable bytes do not beat completed baselines")
        if adaptive.integrity_failure_count or adaptive.failed_models:
            failures.append("adaptive lane has structural failures")
        if historical is not None:
            if adaptive.full_renders + adaptive.targeted_renders >= historical.full_renders + historical.targeted_renders:
                failures.append("adaptive lane did not reduce renders")
            if adaptive.studiomdl_compiles >= historical.studiomdl_compiles:
                failures.append("adaptive lane did not reduce StudioMDL compiles")
        profile_path = REPO_ROOT / "maximum_optimizer/profiles/maximum-adaptive-v2.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("calibrated") is not True:
            failures.append("adaptive profile is not frozen/calibrated")
        normal_by_family = {result.family_id: result for result in by_lane["normal-safe"]}
        for result in by_lane["maximum-adaptive-v2"]:
            baseline = normal_by_family[result.family_id]
            if result.final_comparable > baseline.final_comparable and result.integrity_failures:
                failures.append(f"{result.family_id}: holdout is worse than Normal in bytes and fidelity")
    elif args.require_promotion:
        failures.append("promotion requires completed normal-safe and maximum-adaptive-v2 lanes")

    payload = {
        "schema": 1,
        "partition": args.partition,
        "passed": not failures,
        "failures": failures,
        "aggregates": {name: aggregate.__dict__ for name, aggregate in aggregates.items()},
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if failures:
        print("PROMOTION AUDIT FAILED" if args.require_promotion else f"{args.partition.upper()} AUDIT FAILED")
        return 1
    print("PROMOTION AUDIT PASSED" if args.require_promotion else f"{args.partition.upper()} AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
