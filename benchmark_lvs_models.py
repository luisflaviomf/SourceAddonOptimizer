from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable

from maximum_optimizer.benchmarking import (
    ArtifactDeclaration,
    CORPUS_ID,
    FULL_FAMILY_IDS,
    PRESSURE_FAMILY_IDS,
    StrictControlRoundtripAdapter,
    canonical_json_bytes,
    import_compiled_baseline,
    load_corpus,
    parse_control_results,
    portable_compiler_error,
    sha256_file,
    safe_existing_file,
    safe_existing_root,
    summarize_records,
    verify_declared_sidecars,
)


FAMILIES = (
    ("dodge_charger", "Dodge Charger", "diggercars/dodge_charger/charger/charger.qc", "diggercars/dodge_charger/charger"),
    ("dodge_monaco_police", "Dodge Monaco Police", "diggercars/dodge_monaco/monaco_police/monaco_police.qc", "diggercars/dodge_monaco/monaco_police"),
    ("toyota_supra", "Toyota Supra", "diggercars/toyota_supra/supra/supra.qc", "diggercars/toyota_supra/supra"),
    ("ford_fairlane", "Ford Fairlane", "diggercars/ford_fairlane/ford_fairlane/ford_fairlane.qc", "diggercars/ford_fairlane/ford_fairlane"),
    ("nissan_skyline_gtr32", "Nissan Skyline GTR32", "diggercars/nissan_skyline_gtr32/bnr32/bnr32.qc", "diggercars/nissan_skyline_gtr32/bnr32"),
    ("vw_beetle", "VW Beetle", "diggercars/vw_beetle/beetle/beetle.qc", "diggercars/vw_beetle/beetle"),
    ("vw_touareg", "VW Touareg", "diggercars/vw_touareg/touareg/touareg.qc", "diggercars/vw_touareg/touareg"),
    ("ferrari_365_fullrig", "Ferrari 365 full rig", "diggercars/ferrari_365/ferrari_365fullrig/ferrari_365fullrig.qc", "diggercars/ferrari_365/ferrari_365fullrig"),
    ("caterham_620r", "Caterham 620R", "diggercars/caterham_620r/caterham/caterham.qc", "diggercars/caterham_620r/caterham"),
    ("pontiac_transam_wheel", "Pontiac TransAm wheel", "diggercars/pontiac_transam3/wheel/wheel.qc", "diggercars/pontiac_transam3/wheel"),
)
ROOT_ENV = {
    "source": "LVS_SOURCE_ROOT",
    "original": "LVS_ORIGINAL_MODELS_ROOT",
    "blender": "LVS_BLENDER_MODELS_ROOT",
    "fidelity": "LVS_FIDELITY_MODELS_ROOT",
}


def _declaration(path: Path, root: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _source_files(root: Path, qc_relative: str) -> list[dict[str, object]]:
    directory = (root / qc_relative).parent
    paths = sorted(path for path in directory.rglob("*") if path.is_file() and "output" not in path.relative_to(directory).parts)
    return [_declaration(path, root) for path in paths]


def _sidecars(root: Path, stem: str) -> list[dict[str, object]]:
    base = root / stem
    paths = []
    for suffix in (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".ani", ".phy"):
        candidate = Path(str(base) + suffix)
        if candidate.is_file():
            paths.append(candidate)
    if not all(Path(str(base) + suffix).is_file() for suffix in (".mdl", ".vvd", ".dx90.vtx")):
        raise ValueError(f"required sidecars missing for {stem} under {root}")
    return [_declaration(path, root) for path in paths]


def freeze_corpus(args: argparse.Namespace) -> int:
    roots = {name: Path(getattr(args, name)).resolve(strict=True) for name in ROOT_ENV}
    families = []
    for family_id, display_name, source_qc, compiled_stem in FAMILIES:
        families.append({
            "id": family_id,
            "display_name": display_name,
            "source_qc": source_qc,
            "source_files": _source_files(roots["source"], source_qc),
            "compiled_stem": compiled_stem,
            "baselines": {
                lane: _sidecars(roots[lane], compiled_stem)
                for lane in ("original", "blender", "fidelity")
            },
        })
    payload = {
        "schema_version": 1,
        "corpus_id": CORPUS_ID,
        "roots": {name: {"env": env} for name, env in ROOT_ENV.items()},
        "partitions": {"pressure": list(PRESSURE_FAMILY_IDS), "full": list(FULL_FAMILY_IDS)},
        "families": families,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(payload))
    return 0


def _source_digest(family: object) -> str:
    manifest = [{"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256} for item in family.source_files]
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


def import_baselines(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    runner_hash = sha256_file(Path(__file__))
    records = []
    for family in corpus.families:
        source_hash = _source_digest(family)
        for lane in ("original", "blender", "fidelity"):
            provenance = {
                "tool": {},
                "scripts": {"benchmark_lvs_models.py": runner_hash},
                "settings": {
                    "imported_existing": True,
                    "root_env": ROOT_ENV[lane],
                    "baseline": "unmodified-original" if lane == "original" else f"mixedfinal50h8-{lane}-050",
                },
            }
            records.append(import_compiled_baseline(
                corpus_id=corpus.corpus_id, family_id=family.id, lane=lane,
                root=corpus.roots[lane], compiled_stem=family.compiled_stem,
                declared_artifacts=family.baselines[lane], provenance=provenance, source_hash=source_hash,
            ))
    payload = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "records": [record.to_dict() for record in records],
        "summary": summarize_records(records),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(payload))
    return 0


def generate_control(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    if corpus.full_ids != FULL_FAMILY_IDS:
        raise ValueError("control generation is restricted to the fixed ten-family LVS corpus")
    run_root = args.run_root.resolve()
    if ".superpowers" not in run_root.parts:
        raise ValueError("control run root must be an ignored .superpowers directory")
    run_root.mkdir(parents=True, exist_ok=True)
    results = []
    for family in corpus.families:
        family_root = run_root / family.id
        game = family_root / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "gameinfo.txt").write_text('"GameInfo" { game "LVS control" FileSystem { SearchPaths { Game |gameinfo_path|. } } }\n', encoding="utf-8")
        adapter = StrictControlRoundtripAdapter(args.studiomdl.resolve(strict=True), game)
        try:
            result = adapter.compile(source_root=corpus.roots["source"], source_files=family.source_files,
                                     source_qc=family.source_qc, run_root=family_root / "workspace", family_id=family.id)
        except Exception as exc:
            failure_log = family_root / "workspace" / family.id / "studiomdl.log"
            failure_log.parent.mkdir(parents=True, exist_ok=True)
            failure_log.write_text(f"ERROR: {type(exc).__name__}: {exc}\n", encoding="utf-8")
            result = {"status": "failed", "returncode": -1, "elapsed_seconds": 0.0,
                      "log": f"{family.id}/studiomdl.log", "autofixes": False, "source_mutated": False}
        results.append({"family_id": family.id, **result})
    (run_root / "control_results.json").write_bytes(canonical_json_bytes({"schema_version": 1, "corpus_id": corpus.corpus_id, "results": results}))
    return 0 if all(result["status"] == "compiled" for result in results) else 1


def record_control(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    if not isinstance(args.tool_version, str) or not args.tool_version.strip() or len(args.tool_version) > 128:
        raise ValueError("tool_version must be a non-empty bounded string")
    run_root = safe_existing_root(args.run_root.resolve(strict=True), "control run root")
    studiomdl = safe_existing_file(args.studiomdl.resolve(strict=True), "StudioMDL")
    output = args.output.resolve()
    safe_existing_root(output.parent.resolve(strict=True), "control metadata output root")
    if os.path.lexists(output):
        safe_existing_file(output, "control metadata output")
    evidence_path = run_root / "control_results.json"
    safe_existing_file(evidence_path, "control_results.json")
    raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    results = parse_control_results(raw, expected_family_ids=corpus.full_ids)
    evidence = []
    for family, result in zip(corpus.families, results, strict=True):
        item = {
            "family_id": family.id,
            "status": result["status"],
            "returncode": result.get("returncode"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "autofixes": False,
            "source_mutated": False,
            "artifacts": [],
            "failure": None,
        }
        workspace = safe_existing_root(run_root / family.id / "workspace", f"{family.id} control workspace")
        expected_log = f"{family.id}/studiomdl.log"
        if result["log"] != expected_log:
            raise ValueError(f"stale/tampered control log path for {family.id}")
        log = workspace / expected_log
        safe_existing_file(log, f"{family.id} control log")
        log_text = log.read_text(encoding="utf-8", errors="replace")
        completed = any(line.startswith("Completed ") for line in log_text.splitlines())
        if (result["status"] == "compiled") != completed:
            raise ValueError(f"stale/tampered control status for {family.id}")
        if result["status"] == "compiled":
            models = safe_existing_root(run_root / family.id / "game" / "models", f"{family.id} compiled models")
            artifact_payloads = _sidecars(models, family.compiled_stem)
            declarations = tuple(ArtifactDeclaration.parse(value, f"{family.id} control artifact") for value in artifact_payloads)
            verify_declared_sidecars(models, family.compiled_stem, declarations, f"{family.id} control")
            item["artifacts"] = artifact_payloads
        else:
            models = run_root / family.id / "game" / "models"
            if models.exists():
                base = models / family.compiled_stem
                if any(Path(str(base) + suffix).exists() for suffix in (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".ani", ".phy")):
                    raise ValueError(f"failed control contains stale/partial sidecars for {family.id}")
            errors = [line.strip() for line in log_text.splitlines() if line.startswith("ERROR:")]
            item["failure"] = " | ".join(portable_compiler_error(line) for line in errors[-2:]) or f"StudioMDL return code {result.get('returncode')}"
        evidence.append(item)
    payload = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "lane": "control",
        "tool": {
            "name": "Garry's Mod StudioMDL",
            "version": args.tool_version,
            "size_bytes": studiomdl.stat().st_size,
            "sha256": sha256_file(studiomdl),
            "path_env": "STUDIOMDL_EXE",
        },
        "records": evidence,
        "quality_status": "unverified",
        "quality_claim": None,
    }
    output.write_bytes(canonical_json_bytes(payload))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Freeze and benchmark the fixed LVS model corpus")
    sub = result.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    for name in ROOT_ENV:
        freeze.add_argument(f"--{name}", required=True)
    freeze.add_argument("--output", type=Path, default=Path("benchmarks/lvs_models/corpus.json"))
    freeze.set_defaults(func=freeze_corpus)
    imported = sub.add_parser("import-baselines")
    imported.add_argument("--corpus", type=Path, default=Path("benchmarks/lvs_models/corpus.json"))
    imported.add_argument("--output", type=Path, default=Path("benchmarks/lvs_models/baseline.json"))
    imported.set_defaults(func=import_baselines)
    control = sub.add_parser("generate-control")
    control.add_argument("--corpus", type=Path, default=Path("benchmarks/lvs_models/corpus.json"))
    control.add_argument("--studiomdl", type=Path, required=True)
    control.add_argument("--run-root", type=Path, default=Path(".superpowers/lvs-task2-control"))
    control.set_defaults(func=generate_control)
    recorded = sub.add_parser("record-control")
    recorded.add_argument("--corpus", type=Path, default=Path("benchmarks/lvs_models/corpus.json"))
    recorded.add_argument("--run-root", type=Path, required=True)
    recorded.add_argument("--studiomdl", type=Path, required=True)
    recorded.add_argument("--tool-version", required=True)
    recorded.add_argument("--output", type=Path, default=Path("benchmarks/lvs_models/control.json"))
    recorded.set_defaults(func=record_control)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
