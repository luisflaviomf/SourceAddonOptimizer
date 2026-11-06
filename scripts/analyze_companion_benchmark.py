#!/usr/bin/env python3
"""Audit completed paired Companion benchmark evidence and emit objective gates."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Sequence


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from maximum_optimizer.benchmarking import FamilyResult, write_json  # noqa: E402


LANE_A = "maximum-no-profile"
LANE_B = "maximum-profile"


def percent_change(baseline: float, candidate: float) -> float:
    return (candidate - baseline) * 100.0 / baseline if baseline else 0.0


def median_range(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty measurement")
    return {"median": statistics.median(values), "minimum": min(values), "maximum": max(values)}


def region_decision_sha256(report: dict[str, object]) -> str:
    """Fingerprint stable geometry choices while excluding paths and timings."""
    fields = (
        "key",
        "source",
        "material",
        "representation",
        "original_triangles",
        "normal_triangles",
        "selected_triangles",
        "selected_vertices",
        "failed_gates",
        "targeted_render",
    )
    details = report.get("region_details", [])
    if not isinstance(details, list):
        raise ValueError("Maximum report region_details is not a list")
    decisions = []
    for item in details:
        if not isinstance(item, dict):
            raise ValueError("Maximum report contains a non-object region detail")
        decisions.append({field: item.get(field) for field in fields})
    decisions.sort(key=lambda item: (str(item.get("key", "")), str(item.get("source", "")), str(item.get("material", ""))))
    canonical = json.dumps(decisions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def selected_smd_manifest_sha256(artifacts: dict[str, object]) -> str:
    files = artifacts.get("selected_smd_files", [])
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise ValueError("artifact selected_smd_files is not an object list")
    canonical_files = sorted(files, key=lambda item: str(item.get("path", "")))
    canonical = json.dumps(canonical_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def region_run_summary(report: dict[str, object]) -> dict[str, object]:
    details = report.get("region_details", [])
    if not isinstance(details, list):
        raise ValueError("Maximum report region_details is not a list")
    representations: dict[str, int] = defaultdict(int)
    failed_gates: dict[str, int] = defaultdict(int)
    targeted = 0
    for item in details:
        if not isinstance(item, dict):
            raise ValueError("Maximum report contains a non-object region detail")
        representations[str(item.get("representation", "unknown"))] += 1
        targeted += int(item.get("targeted_render") is True)
        gates = item.get("failed_gates", [])
        if not isinstance(gates, list):
            raise ValueError("Maximum region failed_gates is not a list")
        for gate in gates:
            failed_gates[str(gate)] += 1

    uplifts: list[float] = []
    runtime = report.get("runtime_profile")
    if isinstance(runtime, dict):
        regions = runtime.get("regions", [])
        if not isinstance(regions, list):
            raise ValueError("Maximum runtime profile regions is not a list")
        for item in regions:
            if not isinstance(item, dict):
                raise ValueError("Maximum runtime profile contains a non-object region")
            uplifts.append(float(item["effective_risk"]) - float(item["geometric_risk"]))
    return {
        "representations": dict(sorted(representations.items())),
        "failed_gates": dict(sorted(failed_gates.items())),
        "targeted_regions": targeted,
        "runtime_risk_uplift_average": statistics.fmean(uplifts) if uplifts else 0.0,
        "runtime_risk_uplift_maximum": max(uplifts, default=0.0),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--family", action="append", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def _maximum_report(work_path: Path) -> dict[str, object]:
    reports = list(Path(work_path).rglob("maximum_adaptive_report.json"))
    if len(reports) != 1:
        raise ValueError(f"expected one Maximum report under {work_path}, found {len(reports)}")
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Maximum report is not an object: {reports[0]}")
    return payload


def _load_measured(root: Path) -> list[tuple[FamilyResult, dict[str, object], dict[str, object]]]:
    records = []
    for path in sorted(Path(root).rglob("result.json")):
        if not any(part.startswith("measured-") for part in path.parts):
            continue
        result = FamilyResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        artifact_path = path.with_name("artifacts.json")
        if not artifact_path.is_file():
            raise FileNotFoundError(f"missing artifact manifest: {artifact_path}")
        artifacts = json.loads(artifact_path.read_text(encoding="utf-8"))
        records.append((result, artifacts, _maximum_report(Path(result.work_path))))
    return records


def main() -> int:
    args = _arguments()
    root = args.evidence_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    expected = tuple(args.family)
    if len(expected) != len(set(expected)):
        raise ValueError("family selection contains duplicates")
    records = _load_measured(root)
    grouped: dict[tuple[str, str], list[tuple[FamilyResult, dict[str, object], dict[str, object]]]] = defaultdict(list)
    for record in records:
        grouped[(record[0].family_id, record[0].lane)].append(record)

    failures: list[str] = []
    warnings: list[str] = []
    family_payload: dict[str, object] = {}
    lane_wall_totals = {LANE_A: 0.0, LANE_B: 0.0}
    lane_size_totals = {LANE_A: 0.0, LANE_B: 0.0}
    for family_id in expected:
        output: dict[str, object] = {}
        family_payload[family_id] = output
        for lane in (LANE_A, LANE_B):
            values = grouped.get((family_id, lane), [])
            if len(values) != args.runs:
                failures.append(f"{family_id}/{lane}: measured runs {len(values)} != {args.runs}")
                continue
            results = [value[0] for value in values]
            artifacts = [value[1] for value in values]
            reports = [value[2] for value in values]
            region_summaries = [region_run_summary(report) for report in reports]
            wall = median_range([result.wall_seconds for result in results])
            sizes = median_range([float(result.final_comparable) for result in results])
            compiled_hashes = sorted({str(item.get("compiled_tree_sha256", "")) for item in artifacts})
            selected_smd_hashes = sorted({selected_smd_manifest_sha256(item) for item in artifacts})
            geometry_signatures = sorted({
                (
                    result.final_triangles,
                    result.final_vertices,
                    result.optimized_models,
                    result.preserved_models,
                )
                for result in results
            })
            decision_hashes = sorted({region_decision_sha256(report) for report in reports})
            representation_names = sorted({
                name
                for summary in region_summaries
                for name in summary["representations"]  # type: ignore[union-attr]
            })
            failed_gate_names = sorted({
                name
                for summary in region_summaries
                for name in summary["failed_gates"]  # type: ignore[union-attr]
            })
            stage_names = sorted({stage for result in results for stage, _seconds in result.stage_seconds})
            runtime_profiles = [report.get("runtime_profile") for report in reports]
            runtime_summary = None
            if lane == LANE_A and any(value is not None for value in runtime_profiles):
                failures.append(f"{family_id}/{lane}: unexpectedly consumed runtime evidence")
            if lane == LANE_B:
                if any(not isinstance(value, dict) for value in runtime_profiles):
                    failures.append(f"{family_id}/{lane}: runtime evidence was not consumed in every run")
                else:
                    runtime_summary = {
                        key: median_range([float(value[key]) for value in runtime_profiles if isinstance(value, dict)])
                        for key in ("matched_models", "matched_materials", "matched_regions", "scheduled_validations", "regional_recoveries")
                    }
                    if runtime_summary["matched_regions"]["minimum"] <= 0:
                        failures.append(f"{family_id}/{lane}: profile matched zero regions")
            if len(decision_hashes) != 1:
                failures.append(f"{family_id}/{lane}: regional choices are not deterministic across measured runs")
            if len(geometry_signatures) != 1:
                warnings.append(f"{family_id}/{lane}: compiled vertex signature varied across measured runs")
            if len(selected_smd_hashes) != 1:
                warnings.append(f"{family_id}/{lane}: selected SMD bytes varied across measured runs")
            if len(compiled_hashes) != 1:
                warnings.append(f"{family_id}/{lane}: StudioMDL binary bytes varied across measured runs")
            for result in results:
                if result.status != "ok" or result.exit_code != 0 or result.integrity_failures:
                    failures.append(f"{family_id}/{lane}: integrity/status failure")
                if result.final_dx80_bytes != 0 or result.dx90_count != result.source_models:
                    failures.append(f"{family_id}/{lane}: DX80/DX90 gate failed")
                if result.full_renders != 0:
                    failures.append(f"{family_id}/{lane}: full-family render count is nonzero")
                if result.source_models and result.optimized_models == 0:
                    failures.append(f"{family_id}/{lane}: family-wide preservation")
            if any(item.get("companion_dependency_hits") for item in artifacts):
                failures.append(f"{family_id}/{lane}: Companion dependency found in output paths")
            output[lane] = {
                "wall_seconds": wall,
                "cpu_seconds": median_range([result.cpu_seconds for result in results]),
                "peak_working_set_bytes": median_range([float(result.peak_working_set_bytes) for result in results]),
                "final_comparable_bytes": sizes,
                "reduction_percent": median_range([
                    (result.original_comparable - result.final_comparable) * 100.0 / result.original_comparable
                    for result in results
                ]),
                "final_triangles": median_range([float(result.final_triangles) for result in results]),
                "final_vertices": median_range([float(result.final_vertices) for result in results]),
                "targeted_renders": median_range([float(result.targeted_renders) for result in results]),
                "full_family_renders": median_range([float(result.full_renders) for result in results]),
                "simplifier_evaluations": median_range([float(result.simplifier_evaluations) for result in results]),
                "studiomdl_compiles": median_range([float(result.studiomdl_compiles) for result in results]),
                "optimized_models": median_range([float(result.optimized_models) for result in results]),
                "preserved_models": median_range([float(result.preserved_models) for result in results]),
                "stage_wall_seconds": {
                    name: median_range([
                        float(dict(result.stage_seconds).get(name, 0.0)) for result in results
                    ])
                    for name in stage_names
                },
                "region_outcomes": {
                    "representations": {
                        name: median_range([
                            float(summary["representations"].get(name, 0))  # type: ignore[union-attr]
                            for summary in region_summaries
                        ])
                        for name in representation_names
                    },
                    "failed_metric_gates": {
                        name: median_range([
                            float(summary["failed_gates"].get(name, 0))  # type: ignore[union-attr]
                            for summary in region_summaries
                        ])
                        for name in failed_gate_names
                    },
                    "targeted_regions": median_range([
                        float(summary["targeted_regions"]) for summary in region_summaries
                    ]),
                    "runtime_risk_uplift_average": median_range([
                        float(summary["runtime_risk_uplift_average"]) for summary in region_summaries
                    ]),
                    "runtime_risk_uplift_maximum": median_range([
                        float(summary["runtime_risk_uplift_maximum"]) for summary in region_summaries
                    ]),
                },
                "geometry_signatures": geometry_signatures,
                "region_decision_sha256": decision_hashes,
                "selected_smd_manifest_sha256": selected_smd_hashes,
                "compiled_binary": {
                    "deterministic": len(compiled_hashes) == 1,
                    "unique_tree_hashes": compiled_hashes,
                },
                "runtime_profile": runtime_summary,
            }
            lane_wall_totals[lane] += wall["median"]
            lane_size_totals[lane] += sizes["median"]
        if LANE_A in output and LANE_B in output:
            left = output[LANE_A]
            right = output[LANE_B]
            assert isinstance(left, dict) and isinstance(right, dict)
            comparison = {
                "wall_overhead_percent": percent_change(
                    left["wall_seconds"]["median"], right["wall_seconds"]["median"]  # type: ignore[index]
                ),
                "size_change_percent": percent_change(
                    left["final_comparable_bytes"]["median"], right["final_comparable_bytes"]["median"]  # type: ignore[index]
                ),
                "geometry_decisions_identical": left["region_decision_sha256"] == right["region_decision_sha256"],
                "selected_smd_variant_sets_identical": (
                    left["selected_smd_manifest_sha256"] == right["selected_smd_manifest_sha256"]
                ),
                "compiled_hash_sets_identical": (
                    left["compiled_binary"]["unique_tree_hashes"] == right["compiled_binary"]["unique_tree_hashes"]  # type: ignore[index]
                ),
            }
            output["comparison"] = comparison
            if comparison["size_change_percent"] > 5.0:
                failures.append(f"{family_id}: profile output-size increase exceeds 5%")

    aggregate_time_overhead = percent_change(lane_wall_totals[LANE_A], lane_wall_totals[LANE_B])
    aggregate_size_change = percent_change(lane_size_totals[LANE_A], lane_size_totals[LANE_B])
    if aggregate_time_overhead > 10.0:
        failures.append(f"aggregate profile time overhead exceeds 10%: {aggregate_time_overhead:.3f}%")
    if aggregate_size_change > 5.0:
        failures.append(f"aggregate profile output-size increase exceeds 5%: {aggregate_size_change:.3f}%")
    payload = {
        "schema": 1,
        "automated_gates_passed": not failures,
        "visual_review_required": True,
        "failures": failures,
        "warnings": warnings,
        "aggregate": {
            "maximum_no_profile_median_wall_sum": lane_wall_totals[LANE_A],
            "maximum_profile_median_wall_sum": lane_wall_totals[LANE_B],
            "wall_overhead_percent": aggregate_time_overhead,
            "maximum_no_profile_median_bytes_sum": lane_size_totals[LANE_A],
            "maximum_profile_median_bytes_sum": lane_size_totals[LANE_B],
            "size_change_percent": aggregate_size_change,
        },
        "families": family_payload,
    }
    destination = args.out.resolve() if args.out else root / "acceptance-audit.json"
    write_json(destination, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
