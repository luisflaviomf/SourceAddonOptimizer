from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from maximum_optimizer.qc_inventory import parse_qc_fingerprint
from maximum_optimizer.ratio_sweep_evidence import parse_ratio_sweep_evidence


OUTPUT = Path(__file__).with_name("blender_adaptive_ratio_sweep_v1.json")
SOURCE_COMMIT = "b9bf711c5706d057d895defb4d967cfdd91d53e2"
BASELINES = json.loads(Path(__file__).with_name("blender_adaptive_v1.json").read_text(encoding="utf-8"))
BASELINE_BY_FAMILY = {item["family_id"]: item for item in BASELINES["records"]}
FAMILIES = (
    ("dodge_charger", "dodge-charger", "charger.qc", "diggercars/dodge_charger", "charger"),
    ("toyota_supra", "toyota-supra", "supra.qc", "diggercars/toyota_supra", "supra"),
    ("nissan_skyline_gtr32", "nissan-skyline-gtr32", "bnr32.qc", "diggercars/nissan_skyline_gtr32", "bnr32"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def attempt(run: Path, qc_name: str, compiled_dir: str, stem: str, ratio: float) -> dict:
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
    if not isinstance(records, list) or len(records) != 1 or records[0].get("status") != "ok" \
            or records[0].get("returncode") != 0:
        raise SystemExit(f"{run.name}: StudioMDL summary did not pass")
    source_fp = parse_qc_fingerprint(run / qc_name)
    optimized_qc = next(run.glob("*_OPT.qc"))
    optimized_fp = parse_qc_fingerprint(optimized_qc)
    differing = [
        name for name in source_fp.__dataclass_fields__
        if getattr(source_fp, name) != getattr(optimized_fp, name)
    ]
    fallback_items = []
    for raw in metrics["files"]:
        if not raw.get("fallback_reason"):
            continue
        source = Path(raw["source"]).resolve()
        output = Path(raw["output"]).resolve()
        source_relative = source.relative_to(run.resolve()).as_posix()
        fallback_items.append({
            "source": source_relative,
            "reason": raw["fallback_reason"],
            "size_bytes": source.stat().st_size,
            "source_sha256": digest(source),
            "output_sha256": digest(output),
        })
    fallback_items.sort(key=lambda item: item["source"])
    compiled = artifacts(run / "compiled" / "models" / compiled_dir, stem)
    return {
        "ratio": ratio,
        "candidate": lane(compiled),
        "triangles": {"before": int(metrics["triangles_before"]), "after": int(metrics["triangles_after"])},
        "fallback_summary": {
            "count": len(fallback_items),
            "bytes": sum(item["size_bytes"] for item in fallback_items),
        },
        "fallbacks": fallback_items,
        "qc": {"status": "pass" if differing == ["mesh_files"] else "fail", "differing_fields": differing},
        "candidate_metrics_sha256": digest(metrics_path),
        "candidate_payload_sha256": digest(candidate_path),
        "compile_log_sha256": digest(compile_log),
        "compile_summary_sha256": digest(compile_summary),
    }


def tool(path: Path, version: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing tool: {path}")
    return {"version": version, "sha256": digest(path)}


def main() -> int:
    runs = Path(os.environ["LVS_TASK8_RATIO_SWEEP_ROOT"]).resolve()
    blender = Path(os.environ["BLENDER_EXE"]).resolve()
    studiomdl = Path(os.environ["STUDIOMDL_EXE"]).resolve()
    families = []
    for family_id, directory_id, qc_name, compiled_dir, stem in FAMILIES:
        baseline = BASELINE_BY_FAMILY[family_id]
        attempts = [
            attempt(runs / f"{directory_id}-r{tag}", qc_name, compiled_dir, stem, ratio)
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
                "ratio": selected["ratio"],
                "total_bytes": selected["candidate"]["total_bytes"],
                "reason": "minimum actual StudioMDL family bytes among structurally passing planned ratios; visual quality is a separate pending gate",
            },
        })
    payload = {
        "schema_version": 1,
        "strategy": "blender-adaptive-ratio-sweep-v1",
        "implementation": {
            "source_commit": SOURCE_COMMIT,
            "scripts": {
                name: digest(REPO / name)
                for name in (
                    "batch_compile_opt_qc.py",
                    "batch_optimize_maximum.py",
                    "maximum_optimizer/compiler_aware.py",
                    "maximum_optimizer/qc_graph.py",
                    "maximum_optimizer/qc_inventory.py",
                )
            },
        },
        "toolchain": {
            "blender": tool(blender, "5.0.1"),
            "studiomdl": tool(studiomdl, "Jun 29 2026 Garry's Mod Edition"),
        },
        "families": families,
        "quality": {
            "status": "unverified",
            "visual_gate": "separate-pending",
            "reason": "this sweep ranks actual compiled bytes only; it does not promote a visual winner or production default",
        },
    }
    canonical = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    payload["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
    parse_ratio_sweep_evidence(payload)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
