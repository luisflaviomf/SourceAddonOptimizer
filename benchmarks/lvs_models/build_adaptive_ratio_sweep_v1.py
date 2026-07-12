from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from maximum_optimizer.qc_inventory import parse_qc_fingerprint
from maximum_optimizer.ratio_sweep_evidence import parse_ratio_sweep_evidence


OUTPUT = Path(__file__).with_name("blender_adaptive_ratio_sweep_v1.json")
RUN_BASE_COMMIT = "b9bf711c5706d057d895defb4d967cfdd91d53e2"
EXECUTION_SNAPSHOT_COMMIT = "47548fd255d43027ec8ddd73c9bc72ddc777b23f"
BASELINE_INPUT = Path(__file__).with_name("blender_adaptive_v1.json")
BASELINE_INPUT_SHA256 = "d246a382db92c6f19e49e170f6cf56eddfb72c93834bd73511eb6c365a3779ec"
RUNTIME_SCRIPTS = {
    "batch_compile_opt_qc.py": {
        "git_blob_sha1": "39bb95630a1fbb6754687ee05636bc77d3fe7626",
        "checkout_sha256": "9ae7a78878fd281de99cd3476ce8d1fc8e073b20e8f4897a9df36dc97c3215cc",
        "checkout_eol": "crlf",
    },
    "batch_optimize_maximum.py": {
        "git_blob_sha1": "7989d38ef77900f6742b3f01df70f98753712aff",
        "checkout_sha256": "76cd2f9ca769d92e49fd66b1f8e383adb4492208a50a231147cf9a5fbb5e8098",
        "checkout_eol": "lf",
    },
    "maximum_optimizer/compiler_aware.py": {
        "git_blob_sha1": "0a88926c804c8244b7722b3a5c8024e74e32577a",
        "checkout_sha256": "186c4d7cd49f8085dd3741c9d4221cc5daa7e8b80ca7c83d7d4d0ee0d6c45b13",
        "checkout_eol": "lf",
    },
    "maximum_optimizer/qc_graph.py": {
        "git_blob_sha1": "342df930c3fa42b710597998bc099b18b6f3df97",
        "checkout_sha256": "625d64ef8b78f49ce1ab42a979b9faa781b2feefcc9722c8665d074a094b8260",
        "checkout_eol": "lf",
    },
    "maximum_optimizer/qc_inventory.py": {
        "git_blob_sha1": "2022c336dfaf71e8f1ab7198f56f228c40d26158",
        "checkout_sha256": "ca4cb526add762bb7b6b56a308f5aee37b06ec045b9a2e5b1be028d32952b057",
        "checkout_eol": "lf",
    },
}
FAMILIES = (
    ("dodge_charger", "dodge-charger", "charger.qc", "diggercars/dodge_charger", "charger"),
    ("toyota_supra", "toyota-supra", "supra.qc", "diggercars/toyota_supra", "supra"),
    ("nissan_skyline_gtr32", "nissan-skyline-gtr32", "bnr32.qc", "diggercars/nissan_skyline_gtr32", "bnr32"),
)


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_frozen_execution_snapshot() -> None:
    for path, expected in RUNTIME_SCRIPTS.items():
        try:
            blob = subprocess.check_output(
                ["git", "rev-parse", f"{EXECUTION_SNAPSHOT_COMMIT}:{path}"],
                cwd=REPO,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit(f"execution snapshot is unavailable: {path}") from exc
        if blob != expected["git_blob_sha1"]:
            raise SystemExit(f"execution snapshot blob drift: {path}")
        blob_bytes = subprocess.check_output(
            ["git", "cat-file", "blob", blob], cwd=REPO
        )
        checkout_bytes = (
            blob_bytes.replace(b"\n", b"\r\n")
            if expected["checkout_eol"] == "crlf" else blob_bytes
        )
        if hashlib.sha256(checkout_bytes).hexdigest() != expected["checkout_sha256"]:
            raise SystemExit(f"execution snapshot checkout/EOL drift: {path}")
    if digest(BASELINE_INPUT) != BASELINE_INPUT_SHA256:
        raise SystemExit("frozen blender_adaptive_v1.json input drifted")


def artifacts(root: Path, stem: str) -> list[dict]:
    return sorted(
        (
            {"path": path.name, "size_bytes": path.stat().st_size, "sha256": digest(path)}
            for path in root.glob(stem + ".*")
            if path.is_file() and path.suffix.casefold() in {".mdl", ".vvd", ".vtx", ".phy"}
        ),
        key=lambda item: item["path"],
    )


def lane(items: list[dict]) -> dict:
    copied = sorted((dict(item) for item in items), key=lambda item: item["path"])
    return {"total_bytes": sum(item["size_bytes"] for item in copied), "artifacts": copied}


def fingerprint(value) -> tuple[dict, str]:
    payload = json.loads(json.dumps(asdict(value), ensure_ascii=False))
    return payload, hashlib.sha256(canonical(payload)).hexdigest()


def external_file(path: Path) -> dict:
    return {"sha256": digest(path), "availability": "external-not-committed"}


def attempt(
    run: Path, run_id: str, qc_name: str, compiled_dir: str, stem: str, ratio: float,
) -> dict:
    metrics_path = run / "candidate_metrics.json"
    candidate_path = run / "candidate.json"
    compile_log = run / "compile-current.log"
    compile_summary = run / "compiled" / "compile_summary.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate["ratio"] != ratio or candidate["strategy"] != "blender-adaptive-v1":
        raise SystemExit(f"{run.name}: candidate ratio/strategy mismatch")
    summary = json.loads(compile_summary.read_text(encoding="utf-8"))
    records = summary.get("results")
    if (
        not isinstance(records, list) or len(records) != 1
        or records[0].get("status") != "ok" or records[0].get("returncode") != 0
    ):
        raise SystemExit(f"{run.name}: StudioMDL summary did not pass")
    source_qc = run / qc_name
    optimized_qcs = tuple(run.glob("*_OPT.qc"))
    if not metrics_path.is_file() or len(optimized_qcs) != 1:
        raise SystemExit(f"{run.name}: valid-run contamination proof failed")
    optimized_qc = optimized_qcs[0]
    source_fp = parse_qc_fingerprint(source_qc)
    optimized_fp = parse_qc_fingerprint(optimized_qc)
    differing = [
        name for name in source_fp.__dataclass_fields__
        if getattr(source_fp, name) != getattr(optimized_fp, name)
    ]
    source_fp_payload, source_fp_hash = fingerprint(source_fp)
    optimized_fp_payload, optimized_fp_hash = fingerprint(optimized_fp)
    fallback_items = []
    for raw in metrics["files"]:
        if not raw.get("fallback_reason"):
            continue
        source = Path(raw["source"]).resolve()
        output = Path(raw["output"]).resolve()
        fallback_items.append({
            "source": source.relative_to(run.resolve()).as_posix(),
            "reason": raw["fallback_reason"],
            "size_bytes": source.stat().st_size,
            "source_sha256": digest(source),
            "output_sha256": digest(output),
        })
    fallback_items.sort(key=lambda item: item["source"])
    compiled = artifacts(run / "compiled" / "models" / compiled_dir, stem)
    return {
        "run_id": run_id,
        "ratio": ratio,
        "candidate": lane(compiled),
        "triangles": {"before": int(metrics["triangles_before"]), "after": int(metrics["triangles_after"])},
        "fallback_summary": {
            "count": len(fallback_items),
            "bytes": sum(item["size_bytes"] for item in fallback_items),
        },
        "fallbacks": fallback_items,
        "qc": {
            "status": "pass" if differing == ["mesh_files"] else "fail",
            "differing_fields": differing,
            "source": {
                "path": source_qc.name,
                "sha256": digest(source_qc),
                "fingerprint": source_fp_payload,
                "fingerprint_sha256": source_fp_hash,
            },
            "optimized": {
                "path": optimized_qc.name,
                "sha256": digest(optimized_qc),
                "fingerprint": optimized_fp_payload,
                "fingerprint_sha256": optimized_fp_hash,
            },
        },
        "provenance_files": {
            "candidate_metrics": external_file(metrics_path),
            "candidate_payload": external_file(candidate_path),
            "compile_log": external_file(compile_log),
            "compile_summary": external_file(compile_summary),
        },
    }


def pareto(attempts: list[dict]) -> list[float]:
    result = []
    for candidate in attempts:
        axes = (
            candidate["candidate"]["total_bytes"], candidate["fallback_summary"]["count"],
            candidate["fallback_summary"]["bytes"],
        )
        if not any(
            all(a <= b for a, b in zip(
                (other["candidate"]["total_bytes"], other["fallback_summary"]["count"], other["fallback_summary"]["bytes"]),
                axes,
            ))
            and any(a < b for a, b in zip(
                (other["candidate"]["total_bytes"], other["fallback_summary"]["count"], other["fallback_summary"]["bytes"]),
                axes,
            ))
            for other in attempts if other is not candidate
        ):
            result.append(candidate["ratio"])
    return result


def tool(path: Path, version: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing tool: {path}")
    return {"version": version, "sha256": digest(path)}


def main() -> int:
    _validate_frozen_execution_snapshot()
    baselines = json.loads(BASELINE_INPUT.read_text(encoding="utf-8"))
    baseline_by_family = {item["family_id"]: item for item in baselines["records"]}
    runs = Path(os.environ["LVS_TASK8_RATIO_SWEEP_ROOT"]).resolve()
    blender = Path(os.environ["BLENDER_EXE"]).resolve()
    studiomdl = Path(os.environ["STUDIOMDL_EXE"]).resolve()
    families = []
    for family_id, directory_id, qc_name, compiled_dir, stem in FAMILIES:
        baseline = baseline_by_family[family_id]
        attempts = [
            attempt(
                runs / f"{directory_id}-r{tag}", f"{directory_id}-r{tag}-valid",
                qc_name, compiled_dir, stem, ratio,
            )
            for ratio, tag in ((0.35, "035"), (0.30, "030"), (0.25, "025"))
        ]
        selected = min(attempts, key=lambda item: (item["candidate"]["total_bytes"], item["ratio"]))
        families.append({
            "family_id": family_id,
            "baselines": {
                "original": lane(baseline["original"]["artifacts"]),
                "b050": lane(baseline["baseline"]["artifacts"]),
                "r040": lane(baseline["candidate"]["artifacts"]),
            },
            "attempts": attempts,
            "decision": {
                "minimum_bytes_ratio": selected["ratio"],
                "minimum_bytes": selected["candidate"]["total_bytes"],
                "sampled_pareto_ratios": pareto(attempts),
                "reason": "minimum-bytes selects actual StudioMDL bytes; sampled Pareto also preserves non-dominated fallback count/bytes tradeoffs and is not a visual-quality decision",
            },
        })
    tooling_paths = (
        "benchmarks/lvs_models/build_adaptive_ratio_sweep_v1.py",
        "benchmarks/lvs_models/run_adaptive_ratio_sweep.py",
        "maximum_optimizer/ratio_sweep_evidence.py",
    )
    payload = {
        "schema_version": 2,
        "strategy": "blender-adaptive-ratio-sweep-v1",
        "provenance": {
            "run_base_commit": RUN_BASE_COMMIT,
            "execution_snapshot": {
                "snapshot_commit": EXECUTION_SNAPSHOT_COMMIT,
                "runtime_scripts": RUNTIME_SCRIPTS,
            },
            "evidence_tooling": {path: digest(REPO / path) for path in tooling_paths},
            "inputs": {
                "benchmarks/lvs_models/blender_adaptive_v1.json": BASELINE_INPUT_SHA256,
            },
        },
        "toolchain": {
            "blender": tool(blender, "5.0.1"),
            "studiomdl": tool(studiomdl, "Jun 29 2026 Garry's Mod Edition"),
        },
        "excluded_runs": [{
            "run_id": "failed-harness-qc-file-001",
            "path": ".superpowers/lvs-task8-ratio-sweep/dodge-charger-r035",
            "reason": "harness passed charger.qc instead of the workspace directory; Blender produced no candidate_metrics or optimized QC and the empty compile was discarded before the workspace was recursively deleted and recreated",
            "availability": "deleted-before-valid-rerun-no-artifacts-retained",
            "available_hashes": {},
            "path_reused_after_clean": True,
            "replacement_run_id": "dodge-charger-r035-valid",
            "contamination_proof": {
                "candidate_metrics_required": True,
                "exactly_one_optimized_qc_required": True,
                "one_successful_compile_record_required": True,
                "workspace_recreated_after_recursive_delete": True,
            },
        }],
        "families": families,
        "quality": {
            "status": "unverified",
            "visual_gate": "separate-pending",
            "reason": "this sweep ranks compiled-byte/fallback tradeoffs only; it does not promote a visual winner or production default",
        },
    }
    payload["evidence_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    parse_ratio_sweep_evidence(payload)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
