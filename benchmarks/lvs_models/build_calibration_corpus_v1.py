from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from maximum_optimizer.calibration_evidence import (
    CALIBRATION_FAMILIES,
    CALIBRATION_METRICS,
    canonical_calibration_evidence_hash,
    parse_calibration_evidence,
)
from maximum_optimizer.visual_validation import (
    FidelityProfile,
    REQUIRED_METRICS,
    compare_render_sets,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution(values: list[float]) -> dict:
    if not values or any(not math.isfinite(float(value)) or value < 0 for value in values):
        raise ValueError("distribution values are invalid")
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": _percentile(ordered, 0.5),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def _compiled(spec: dict) -> dict:
    root = Path(spec["compiled_root"]).resolve(strict=True)
    artifacts = []
    for relative in spec["compiled_files"]:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
        artifacts.append({
            "path": Path(relative).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _digest(path),
        })
    artifacts.sort(key=lambda item: item["path"].casefold())
    return {
        "total_bytes": sum(item["size_bytes"] for item in artifacts),
        "artifacts": artifacts,
    }


def _configuration(run_root: Path, index: int, record: dict, source_map: dict[str, str]) -> dict:
    state_root = run_root / f"{index:03d}-{record['name']}" / "renders"
    reference_path = state_root / "original" / "render_manifest.json"
    candidate_path = state_root / "optimized" / "render_manifest.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    profile = FidelityProfile(
        1, "calibration-raw-probe", True, "a" * 64,
        {metric: 1e9 for metric in REQUIRED_METRICS},
    )
    result = compare_render_sets(reference_path.parent, candidate_path.parent, profile)
    infrastructure_failures = tuple(
        failure.gate for failure in result.failures
        if failure.gate not in CALIBRATION_METRICS
    )
    if infrastructure_failures:
        raise ValueError(
            f"raw configuration is not infrastructure-clean: {record['name']} {infrastructure_failures}"
        )
    missing = {}
    for side, manifest in (("reference", reference), ("candidate", candidate)):
        counts = {len(entry.get("missing_materials", ())) for entry in manifest["entries"]}
        if len(counts) != 1:
            raise ValueError(f"inconsistent missing material evidence: {record['name']}/{side}")
        missing[side] = counts.pop()
    audits = {}
    for side, manifest in (("reference", reference), ("candidate", candidate)):
        values = tuple(manifest["geometry_audit"].values())
        input_triangles = sum(item["input_triangles"] for item in values)
        kept_triangles = sum(item["kept_triangles"] for item in values)
        filtered = sum(item["filtered_degenerate_triangles"] for item in values)
        audits[side] = {
            "input_triangles": input_triangles,
            "kept_triangles": kept_triangles,
            "filtered_degenerate_triangles": filtered,
            "max_filtered_fraction": max(
                item["filtered_degenerate_triangles"] / item["input_triangles"]
                for item in values
            ),
        }
    ranked = sorted(
        candidate["geometry"],
        key=lambda item: (
            item["surface_bidirectional_p95"], item["surface_max"], item["scope"]
        ),
        reverse=True,
    )[:3]
    return {
        "name": record["name"],
        "scope": record.get("scope") or "strict-region-paired",
        "bodygroups": record["bodygroups"],
        "reference_manifest_sha256": _digest(reference_path),
        "candidate_manifest_sha256": _digest(candidate_path),
        "metrics": {metric: float(result.metrics[metric]) for metric in CALIBRATION_METRICS},
        "missing_materials": missing,
        "geometry_audit": audits,
        "top_regions": [
            {
                "source": source_map.get(item["scope"], item["scope"]),
                "surface_bidirectional_p95": item["surface_bidirectional_p95"],
                "surface_max": item["surface_max"],
            }
            for item in ranked
        ],
    }


def _lane(spec: dict, lane: str) -> dict:
    run_root = Path(spec["run_root"]).resolve(strict=True)
    summary = json.loads((run_root / "raw-visual-states.json").read_text(encoding="utf-8"))
    expected_scope = (
        "aggregate-appearance-anchor-not-structural-baseline"
        if lane.startswith("aggregate") else "strict-region-paired"
    )
    if summary["validation_scope"] != expected_scope:
        raise ValueError("render run scope does not match evidence lane")
    source_map = {}
    if spec.get("region_manifest"):
        manifest = json.loads(Path(spec["region_manifest"]).read_text(encoding="utf-8"))
        source_map = {
            item["key"]: item["descriptor"]["source_identity"]
            for item in manifest["regions"]
        }
    configurations = [
        _configuration(run_root, index, record, source_map)
        for index, record in enumerate(summary["records"])
    ]
    if lane.startswith("aggregate"):
        configurations[0]["scope"] = expected_scope
    return {
        "lane": lane,
        "candidate_id": spec["candidate_id"],
        "compiled": _compiled(spec),
        "configurations": configurations,
    }


def build_payload(spec: dict, repo_root: Path) -> dict:
    families = []
    for expected_id, family_spec in zip(CALIBRATION_FAMILIES, spec["families"]):
        if family_spec["family_id"] != expected_id:
            raise ValueError("calibration spec family order is invalid")
        alternatives = []
        for alternative_spec in family_spec["alternatives"]:
            alternatives.append({
                "lane": _lane(
                    alternative_spec["lane"], "strict-region-paired"
                ),
                "status": alternative_spec["status"],
                "reason": alternative_spec["reason"],
            })
        families.append({
            "family_id": expected_id,
            "baseline": _lane(
                family_spec["baseline"],
                "aggregate-appearance-anchor-not-structural-baseline",
            ),
            "candidate": _lane(family_spec["candidate"], "strict-region-paired"),
            "alternatives": alternatives,
        })
    implementation_paths = (
        "render_previews.py",
        "maximum_optimizer/visual_validation.py",
        "benchmarks/lvs_models/run_visual_states.py",
        "benchmarks/lvs_models/build_calibration_corpus_v1.py",
    )
    baseline_distribution = {
        metric: distribution([
            family["baseline"]["configurations"][0]["metrics"][metric]
            for family in families
        ])
        for metric in CALIBRATION_METRICS
    }
    payload = {
        "schema_version": 1,
        "strategy": "lvs-calibration-corpus-v1",
        "status": "calibration-pending",
        "toolchain": {
            name: _digest(Path(path).resolve(strict=True))
            for name, path in spec["toolchain"].items()
        },
        "implementation": {
            relative: _digest(repo_root / relative) for relative in implementation_paths
        },
        "families": families,
        "baseline_distribution": baseline_distribution,
        "decision": {
            "winner": False,
            "status": "calibration-pending",
            "reason": "raw corpus distributions are not calibrated acceptance thresholds",
        },
    }
    payload["evidence_sha256"] = canonical_calibration_evidence_hash(payload)
    parse_calibration_evidence(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    payload = build_payload(spec, _REPO_ROOT)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
