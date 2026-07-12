from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from maximum_optimizer.orchestrator import (
    _graph_visual_configurations,
    _validate_visual_configuration_pairing,
)
from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.regions import filter_region_manifest, load_region_manifest_payload
from maximum_optimizer.reporting import canonical_json


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def short_texture_cache_root(run_root: Path) -> Path:
    identity = hashlib.sha256(
        str(Path(run_root).resolve()).casefold().encode("utf-8")
    ).hexdigest()[:16]
    return Path(tempfile.gettempdir()).resolve() / "maximum-vtf-cache" / identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--original-qc", type=Path, required=True)
    parser.add_argument("--candidate-qc", type=Path, required=True)
    parser.add_argument("--region-manifest", type=Path, required=True)
    parser.add_argument("--materials-root", type=Path, required=True, action="append")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--vtfcmd", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-alternatives", type=int, default=8)
    parser.add_argument("--aggregate-regions", action="store_true")
    args = parser.parse_args()
    if args.aggregate_regions and args.max_alternatives != 0:
        raise ValueError("aggregate appearance anchor is restricted to engine-default only")

    source_root = args.source_root.resolve(strict=True)
    original_graph = parse_qc_graph(args.original_qc.resolve(strict=True), source_root)
    candidate_graph = parse_qc_graph(args.candidate_qc.resolve(strict=True), source_root)
    original_states = _graph_visual_configurations(
        original_graph, max_alternatives=args.max_alternatives
    )
    candidate_states = _graph_visual_configurations(
        candidate_graph, max_alternatives=args.max_alternatives
    )
    _validate_visual_configuration_pairing(original_states, candidate_states)
    full_manifest = load_region_manifest_payload(
        json.loads(args.region_manifest.resolve(strict=True).read_text(encoding="utf-8"))
    )
    records = []
    for index, (original, candidate) in enumerate(zip(original_states, candidate_states)):
        state_root = args.out / f"{index:03d}-{original.name}"
        identities = tuple(
            path.resolve(strict=True).relative_to(source_root).as_posix()
            for path in original.sources
        )
        state_manifest = source_root / f"maximum_region_manifest.task8a-{index:03d}-{original.name}.json"
        _write_json(
            state_manifest,
            filter_region_manifest(full_manifest, identities).to_payload(),
        )
        configuration_name = (
            "aggregate-appearance-anchor-not-structural-baseline:engine-default"
            if args.aggregate_regions else original.name
        )
        configuration = {
            "schema": 1,
            "name": configuration_name,
            "bodygroups": dict(original.bodygroup_indices),
            "lod_index": original.lod_index,
            "source_pairs": [
                {
                    "source_identity": identity,
                    "reference_sha256": _digest(reference),
                    "candidate_sha256": _digest(optimized),
                }
                for identity, reference, optimized in zip(
                    identities, original.sources, candidate.sources
                )
            ],
        }
        configuration_path = state_root / "configuration.json"
        _write_json(configuration_path, configuration)
        render_root = state_root / "renders"
        command = [
            str(args.blender.resolve(strict=True)), "--background", "--python",
            str((args.repo_root / "render_previews.py").resolve(strict=True)), "--",
        ]
        for path in original.sources:
            command.extend(("--before", str(path)))
        for path in candidate.sources:
            command.extend(("--after", str(path)))
        command.extend((
            "--out", str(render_root), "--size", "512",
            "--passes", "textured,clay", "--poses", "bind:0",
            "--vtfcmd", str(args.vtfcmd.resolve(strict=True)),
            "--region-manifest", str(state_manifest),
            "--configuration-manifest", str(configuration_path),
            "--texture-cache", str(short_texture_cache_root(args.out)),
        ))
        for materials_root in args.materials_root:
            command.extend(("--materials-root", str(materials_root.resolve(strict=True))))
        if args.aggregate_regions:
            command.append("--aggregate-regions")
        process = subprocess.run(command, cwd=args.repo_root, capture_output=True, text=True)
        (state_root / "blender.log").write_text(
            process.stdout + "\n" + process.stderr, encoding="utf-8"
        )
        if process.returncode != 0:
            raise RuntimeError(f"render failed for {original.name}: {process.returncode}")
        manifests = []
        for side in ("original", "optimized"):
            path = render_root / side / "render_manifest.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("configuration") != configuration:
                raise ValueError(f"configuration identity mismatch for {original.name}/{side}")
            if len(payload.get("entries", ())) != 16 or not payload.get("geometry_audit"):
                raise ValueError(f"incomplete raw render manifest for {original.name}/{side}")
            manifests.append({
                "side": side,
                "sha256": _digest(path),
                "texture_missing_entries": sum(
                    entry.get("texture_missing") is True for entry in payload["entries"]
                ),
                "filtered_degenerate_triangles": sum(
                    audit["filtered_degenerate_triangles"]
                    for audit in payload["geometry_audit"].values()
                ),
            })
        records.append({
            "name": original.name,
            "bodygroups": dict(original.bodygroup_indices),
            "lod_index": original.lod_index,
            "source_pairs": configuration["source_pairs"],
            "manifests": manifests,
        })
    _write_json(args.out / "raw-visual-states.json", {
        "schema": 1,
        "quality_status": "raw-unverified-not-calibrated",
        "validation_scope": (
            "aggregate-appearance-anchor-not-structural-baseline"
            if args.aggregate_regions else "strict-region-paired"
        ),
        "records": records,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
