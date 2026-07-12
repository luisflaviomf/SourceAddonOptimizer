from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable

from maximum_optimizer.benchmarking import (
    CORPUS_ID,
    FULL_FAMILY_IDS,
    PRESSURE_FAMILY_IDS,
    StrictControlRoundtripAdapter,
    canonical_json_bytes,
    import_compiled_baseline,
    load_corpus,
    portable_compiler_error,
    sha256_file,
    summarize_records,
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
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "autofixes": False, "source_mutated": False}
        results.append({"family_id": family.id, **result})
    (run_root / "control_results.json").write_bytes(canonical_json_bytes({"schema_version": 1, "corpus_id": corpus.corpus_id, "results": results}))
    return 0 if all(result["status"] == "compiled" for result in results) else 1


def record_control(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    run_root = args.run_root.resolve(strict=True)
    raw = json.loads((run_root / "control_results.json").read_text(encoding="utf-8"))
    by_id = {item["family_id"]: item for item in raw["results"]}
    if tuple(by_id) != FULL_FAMILY_IDS:
        raise ValueError("control results must contain the fixed ten families in order")
    evidence = []
    for family in corpus.families:
        result = by_id[family.id]
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
        log = run_root / family.id / "workspace" / family.id / "studiomdl.log"
        if result["status"] == "compiled":
            models = run_root / family.id / "game" / "models"
            item["artifacts"] = _sidecars(models, family.compiled_stem)
        else:
            errors = [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("ERROR:")]
            item["failure"] = " | ".join(portable_compiler_error(line) for line in errors[-2:]) or f"StudioMDL return code {result.get('returncode')}"
        evidence.append(item)
    payload = {
        "schema_version": 1,
        "corpus_id": corpus.corpus_id,
        "lane": "control",
        "tool": {
            "name": "Garry's Mod StudioMDL",
            "version": args.tool_version,
            "size_bytes": args.studiomdl.stat().st_size,
            "sha256": sha256_file(args.studiomdl),
            "path_env": "STUDIOMDL_EXE",
        },
        "records": evidence,
        "quality_status": "unverified",
        "quality_claim": None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(payload))
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
