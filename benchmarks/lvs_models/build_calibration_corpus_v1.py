from __future__ import annotations

import argparse
import copy
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
    canonical_compiled_hash,
    canonical_lane_binding_hash,
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


def build_byte_comparison(
    candidate_bytes: int, shipped_original_bytes: int, roundtrip_control_bytes: int
) -> dict:
    values = (candidate_bytes, shipped_original_bytes, roundtrip_control_bytes)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("byte comparison values are invalid")
    if candidate_bytes > min(shipped_original_bytes, roundtrip_control_bytes):
        raise ValueError("candidate is larger than a byte denominator")

    def comparison(kind: str, denominator: int) -> dict:
        saved = denominator - candidate_bytes
        return {
            "denominator_kind": kind,
            "denominator_bytes": denominator,
            "saved_bytes": saved,
            "reduction_fraction": saved / denominator,
        }

    return {
        "candidate_bytes": candidate_bytes,
        "versus_shipped_original": comparison(
            "shipped-original-compiled-family-v1", shipped_original_bytes
        ),
        "versus_roundtrip_control": comparison(
            "strict-roundtrip-control-compiled-family-v1", roundtrip_control_bytes
        ),
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


def _shipped_original(repo_root: Path, family_id: str) -> dict:
    corpus = json.loads(
        (repo_root / "benchmarks/lvs_models/corpus.json").read_text(encoding="utf-8")
    )
    matches = [item for item in corpus["families"] if item["id"] == family_id]
    if len(matches) != 1:
        raise ValueError(f"shipped original family is missing or duplicated: {family_id}")
    artifacts = [dict(item) for item in matches[0]["baselines"]["original"]]
    artifacts.sort(key=lambda item: item["path"].casefold())
    return {
        "total_bytes": sum(item["size_bytes"] for item in artifacts),
        "artifacts": artifacts,
    }


def _archived_alternative(spec: dict, repo_root: Path) -> dict:
    artifact = Path(spec["artifact_path"])
    if not artifact.is_absolute():
        artifact = repo_root / artifact
    artifact = artifact.resolve(strict=True)
    archived_payload = None
    if spec["evidence_kind"] == "uncalibrated-raw-clay-rejection":
        archived_payload = json.loads(artifact.read_text(encoding="utf-8"))
        if (
            archived_payload["decision"]["winner"] is not False
            or archived_payload["quality"]["scope"] != spec["quality_scope"]
        ):
            raise ValueError("archived raw-clay decision or scope drift")
        compiled = archived_payload["candidate"]["compiled"]
        payload_sha256 = archived_payload["candidate"]["candidate_sha256"]
    else:
        compiled = _compiled(spec["compiled"]) if spec.get("compiled") else None
        payload_sha256 = spec.get("payload_sha256") or _digest(artifact)
    return {
        "candidate_id": spec["candidate_id"],
        "evidence_kind": spec["evidence_kind"],
        "status": spec["status"],
        "reason": spec["reason"],
        "evidence": {
            "artifact_path": spec["artifact_label"],
            "artifact_sha256": _digest(artifact),
            "payload_sha256": payload_sha256,
            "artifact_availability": spec["artifact_availability"],
            "quality_scope": spec["quality_scope"],
            "compiled": compiled,
            "compiled_sha256": (
                canonical_compiled_hash(compiled) if compiled is not None else None
            ),
            "winner": False,
        },
    }


def _canonical_payload_hash(payload: object) -> str:
    return hashlib.sha256((
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")).hexdigest()


def _validate_monaco_composite(path: Path, lane: dict) -> dict:
    path = path.resolve(strict=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    without_seal = copy.deepcopy(payload)
    declared_seal = without_seal.pop("seal_sha256", None)
    computed_seal = _canonical_payload_hash(without_seal)
    metrics = payload.get("candidate_metrics")
    computed_metrics = _canonical_payload_hash(metrics)
    if (
        payload.get("schema") != "maximum-accepted-composite-v1"
        or declared_seal != computed_seal
        or payload.get("candidate_metrics_sha256") != computed_metrics
    ):
        raise ValueError("Monaco composite canonical seal is invalid")
    composition = payload.get("composition", {})
    qc = payload.get("qc", {})
    files = metrics.get("files", ()) if type(metrics) is dict else ()
    if (
        len(files) != 44
        or metrics.get("triangles_before") != 206754
        or metrics.get("triangles_after") != 95128
        or any(item.get("fallback_reason") is not None for item in files)
        or len(composition.get("file_origins", ())) != 44
        or composition.get("stable_direct_visuals") != 8
        or composition.get("inherited_r040_visuals") != 36
        or composition.get("visual_fallback_count") != 0
        or qc.get("source_mesh_files") != 44
        or qc.get("optimized_mesh_files") != 44
        or qc.get("differing_fields") != ["mesh_files"]
    ):
        raise ValueError("Monaco composite counts or QC contract drift")

    external_compiled = payload.get("compiled", {})
    normalized_external = {
        "total_bytes": external_compiled.get("total_bytes"),
        "artifacts": sorted((
            {
                "path": Path(item["path"]).name.casefold(),
                "size_bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in external_compiled.get("artifacts", ())
        ), key=lambda item: item["path"]),
    }
    normalized_lane = {
        "total_bytes": lane["compiled"]["total_bytes"],
        "artifacts": sorted((
            {
                "path": Path(item["path"]).name.casefold(),
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in lane["compiled"]["artifacts"]
        ), key=lambda item: item["path"]),
    }
    if normalized_external != normalized_lane or normalized_lane["total_bytes"] != 11592080:
        raise ValueError("Monaco composite compiled artifacts do not match the lane")

    outputs = {
        item["source"].casefold(): item["output_sha256"]
        for item in composition["file_origins"]
    }
    for configuration in lane["configurations"]:
        for pair in configuration["source_pairs"]:
            if outputs.get(Path(pair["source_identity"]).name.casefold()) != pair["candidate_sha256"]:
                raise ValueError("Monaco composite does not bind rendered visual sources")
    return {
        "file_sha256": _digest(path),
        "canonical_payload_sha256": computed_seal,
        "candidate_metrics_sha256": computed_metrics,
    }


def _configuration(run_root: Path, index: int, record: dict, source_map: dict[str, str]) -> dict:
    state_root = run_root / f"{index:03d}-{record['name']}" / "renders"
    reference_path = state_root / "original" / "render_manifest.json"
    candidate_path = state_root / "optimized" / "render_manifest.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    source_pairs = record.get("source_pairs")
    if (
        type(source_pairs) is not list
        or reference.get("configuration", {}).get("source_pairs") != source_pairs
        or candidate.get("configuration", {}).get("source_pairs") != source_pairs
    ):
        raise ValueError(f"raw/render source-pair binding drift: {record['name']}")
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
        "source_pairs": source_pairs,
    }


def _control_provenance(repo_root: Path, family_id: str, compiled: dict) -> dict:
    path = repo_root / "benchmarks/lvs_models/control.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = [item for item in payload["records"] if item["family_id"] == family_id]
    if len(matches) != 1 or matches[0]["status"] != "compiled":
        raise ValueError(f"control provenance is missing: {family_id}")
    record = matches[0]
    expected = {
        "total_bytes": sum(item["size_bytes"] for item in record["artifacts"]),
        "artifacts": sorted(record["artifacts"], key=lambda item: item["path"].casefold()),
    }
    if compiled != expected:
        raise ValueError(f"control compiled artifacts drift: {family_id}")
    return {
        "kind": "roundtrip-control-record-v1",
        "artifacts": [{
            "kind": "control-record",
            "path": "benchmarks/lvs_models/control.json#" + family_id,
            "sha256": hashlib.sha256((
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")).hexdigest(),
        }],
    }


def _apply_regional_replacements(
    source_root: Path, regional: dict, outputs: dict[str, str],
) -> None:
    for replacement in regional.get("replacements", ()):
        relative = Path(replacement["target"].replace("\\", "/"))
        target = (source_root / relative).resolve(strict=True)
        target.relative_to(source_root)
        donor_sha256 = replacement["donor_sha256"]
        if _digest(target) != donor_sha256:
            raise ValueError("regional replacement does not match the declared donor")
        identity = relative.name.removesuffix("_opt.smd").casefold() + ".smd"
        outputs[identity] = donor_sha256


def _candidate_provenance(
    spec: dict, compiled: dict, configurations: list[dict], repo_root: Path,
) -> dict:
    source_root = Path(spec["source_root"]).resolve(strict=True)
    compiled_root = Path(spec["compiled_root"]).resolve(strict=True)
    optimized_qc = Path(spec["optimized_qc"]).resolve(strict=True)
    regional_path = (
        Path(spec["regional_provenance"]).resolve(strict=True)
        if spec.get("regional_provenance") else None
    )
    if regional_path is None:
        compiled_root.relative_to(source_root)
        optimized_qc.relative_to(source_root)
    else:
        compiled_root.relative_to(source_root)
        regional_path.relative_to(source_root)
        optimized_qc.relative_to(source_root)
    is_composite = spec["candidate_id"] == "hybrid-stable"
    paths = ({
        "accepted-composite": Path(spec["composite_path"]),
        "optimized-qc": optimized_qc,
        "compile-summary": compiled_root / "compile_summary.json",
    } if is_composite else {
        "candidate-json": source_root / "candidate.json",
        "candidate-metrics": source_root / "candidate_metrics.json",
        "optimized-qc": optimized_qc,
        "compile-summary": compiled_root / "compile_summary.json",
        **({"regional-provenance": regional_path} if regional_path else {}),
    })
    for path in paths.values():
        resolved = path.resolve(strict=True)
        if regional_path is None and (not is_composite or path != paths["accepted-composite"]):
            resolved.relative_to(source_root)

    composite = (
        json.loads(paths["accepted-composite"].read_text(encoding="utf-8"))
        if is_composite else None
    )
    metrics = (
        composite["candidate_metrics"]
        if is_composite
        else json.loads(paths["candidate-metrics"].read_text(encoding="utf-8"))
    )
    outputs = {}
    for item in metrics.get("files", ()):
        identity = Path(item["source"]).name.casefold()
        digest = item.get("restored_export_sha256")
        if identity in outputs or type(digest) is not str:
            raise ValueError("candidate metrics output identity is invalid")
        outputs[identity] = digest
    if regional_path is not None:
        regional = json.loads(regional_path.read_text(encoding="utf-8-sig"))
        if regional.get("status") != "compiled" or regional.get("compile_exit_code") != 0:
            raise ValueError("regional provenance is not a successful build")
        if regional.get("qc_sha256") != _digest(optimized_qc):
            raise ValueError("regional provenance does not bind the optimized QC")
        if regional.get("base_candidate_sha256") != _digest(paths["candidate-json"]):
            raise ValueError("regional provenance does not bind the base candidate")
        if regional.get("compile_summary_sha256") != _digest(paths["compile-summary"]):
            raise ValueError("regional provenance does not bind the compile summary")
        _apply_regional_replacements(source_root, regional, outputs)
    for configuration in configurations:
        for pair in configuration["source_pairs"]:
            identity = Path(pair["source_identity"]).name.casefold()
            if outputs.get(identity) != pair["candidate_sha256"]:
                raise ValueError(
                    f"rendered candidate is not an optimization output: {pair['source_identity']}"
                )

    summary = json.loads(paths["compile-summary"].read_text(encoding="utf-8"))
    if summary.get("total") != 1 or summary.get("ok") != 1 or summary.get("fail") != 0:
        raise ValueError("compile summary is not a single successful build")
    result = summary["results"][0]
    if (
        result.get("status") != "ok"
        or Path(result["qc_path"]).resolve() != optimized_qc
        or {Path(path).resolve() for path in result["compiled_files"]}
        != {
            (compiled_root / artifact["path"]).resolve()
            for artifact in compiled["artifacts"]
        }
    ):
        raise ValueError("compile summary does not bind the optimized QC and artifacts")
    return {
        "kind": (
            "accepted-composite-bundle-v1" if is_composite
            else "regional-recovery-compile-bundle-v1" if regional_path
            else "optimization-compile-bundle-v1"
        ),
        "artifacts": [
            {
                "kind": kind,
                "path": spec.get("provenance_label", "research://") + "#" + kind,
                "sha256": _digest(path),
            }
            for kind, path in paths.items()
        ],
    }


def _lane(spec: dict, lane: str, family_id: str, repo_root: Path) -> dict:
    run_root = Path(spec["run_root"]).resolve(strict=True)
    raw_summary_path = run_root / "raw-visual-states.json"
    summary = json.loads(raw_summary_path.read_text(encoding="utf-8"))
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
    compiled = _compiled(spec)
    provenance = (
        _control_provenance(repo_root, family_id, compiled)
        if spec["candidate_id"] == "roundtrip-control"
        else _candidate_provenance(spec, compiled, configurations, repo_root)
    )
    result = {
        "lane": lane,
        "candidate_id": spec["candidate_id"],
        "compiled": compiled,
        "configurations": configurations,
        "raw_visual_states_sha256": _digest(raw_summary_path),
        "provenance": provenance,
    }
    result["lane_binding_sha256"] = canonical_lane_binding_hash(result)
    return result


def build_payload(spec: dict, repo_root: Path) -> dict:
    families = []
    for expected_id, family_spec in zip(CALIBRATION_FAMILIES, spec["families"]):
        if family_spec["family_id"] != expected_id:
            raise ValueError("calibration spec family order is invalid")
        alternatives = [
            _archived_alternative(alternative_spec, repo_root)
            for alternative_spec in family_spec["alternatives"]
        ]
        baseline = _lane(
            family_spec["baseline"], "strict-region-paired", expected_id, repo_root
        )
        candidate = _lane(
            family_spec["candidate"], "strict-region-paired", expected_id, repo_root
        )
        shipped = _shipped_original(repo_root, expected_id)
        roundtrip = baseline["compiled"]
        families.append({
            "family_id": expected_id,
            "byte_denominators": {
                "shipped_original": {
                    "kind": "shipped-original-compiled-family-v1",
                    "compiled": shipped,
                },
                "roundtrip_control": {
                    "kind": "strict-roundtrip-control-compiled-family-v1",
                    "compiled": roundtrip,
                },
            },
            "byte_comparison": build_byte_comparison(
                candidate["compiled"]["total_bytes"],
                shipped["total_bytes"],
                roundtrip["total_bytes"],
            ),
            "baseline": baseline,
            "candidate": candidate,
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
    schema_version = spec.get("schema_version", 2)
    payload = {
        "schema_version": schema_version,
        "strategy": f"lvs-calibration-corpus-v{schema_version}",
        "status": "calibration-pending",
        "toolchain": {
            name: _digest(Path(path).resolve(strict=True))
            for name, path in spec["toolchain"].items()
        },
        "implementation": {
            relative: _digest(repo_root / relative) for relative in implementation_paths
        },
        "external_artifacts": {},
        "families": families,
        "baseline_distribution": baseline_distribution,
        "decision": {
            "winner": False,
            "status": "calibration-pending",
            "reason": "raw corpus distributions are not calibrated acceptance thresholds",
        },
    }
    if schema_version == 3:
        recovery = {}
        for name, artifact_spec in spec["regional_recovery"].items():
            path = Path(artifact_spec["path"]).resolve(strict=True)
            item = {"path": artifact_spec["label"], "sha256": _digest(path)}
            if artifact_spec.get("sealed_json"):
                sealed = json.loads(path.read_text(encoding="utf-8"))
                item["payload_sha256"] = sealed["evidence_sha256"]
            recovery[name] = item
        payload["regional_recovery"] = recovery
    monaco = next(item for item in families if item["family_id"] == "dodge_monaco_police")
    payload["external_artifacts"]["monaco_accepted_composite_v1"] = (
        _validate_monaco_composite(
            Path(spec["external_artifacts"]["monaco_accepted_composite_v1"]),
            monaco["candidate"],
        )
    )
    payload["evidence_sha256"] = canonical_calibration_evidence_hash(payload)
    parse_calibration_evidence(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if "base_spec" in spec:
        base = json.loads((_REPO_ROOT / spec["base_spec"]).read_text(encoding="utf-8"))
        base["schema_version"] = spec["schema_version"]
        base["regional_recovery"] = spec["regional_recovery"]
        overrides = spec.get("family_overrides", {})
        for family in base["families"]:
            if family["family_id"] in overrides:
                family["candidate"].update(overrides[family["family_id"]])
        spec = base
    payload = build_payload(spec, _REPO_ROOT)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
