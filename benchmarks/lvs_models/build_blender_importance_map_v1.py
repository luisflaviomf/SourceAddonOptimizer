from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

from PIL import Image


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from maximum_optimizer.importance_evidence import (
    canonical_importance_evidence_hash,
    parse_importance_evidence,
)
from maximum_optimizer.visual_validation import _image_metrics


ANGLES = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
OUTPUT = Path(__file__).with_name("blender_importance_map_v1.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_set_sha256(root: Path) -> str:
    entries = []
    for angle in ANGLES:
        path = Path(root) / "clay" / "bind" / f"{angle}.png"
        if not path.is_file():
            raise ValueError(f"missing controlled clay image: {path}")
        with Image.open(path) as source:
            source.load()
            pixels = source.convert("RGBA")
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "width": pixels.width,
            "height": pixels.height,
            "mode": pixels.mode,
            "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
        })
    encoded = (json.dumps(entries, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def raw_metrics(reference: Path, candidate: Path) -> dict[str, float]:
    values = []
    for angle in ANGLES:
        with Image.open(reference / "clay" / "bind" / f"{angle}.png") as before:
            with Image.open(candidate / "clay" / "bind" / f"{angle}.png") as after:
                values.append(_image_metrics(before.convert("RGBA"), after.convert("RGBA")))
    return {
        "average_silhouette_error": sum(item["silhouette_iou"] for item in values) / len(values),
        "max_silhouette_error": max(item["silhouette_iou"] for item in values),
        "average_edge_error": sum(item["edge_error"] for item in values) / len(values),
        "max_edge_error": max(item["edge_error"] for item in values),
        "average_rgb_mae": sum(item["rgb_mae"] for item in values) / len(values),
        "max_rgb_mae": max(item["rgb_mae"] for item in values),
    }


def compiled_artifacts(run: Path, compiled_name: str) -> dict:
    root = run / compiled_name / "models" / "diggercars" / "pontiac_transam3"
    paths = sorted(root.glob("wheel.*"), key=lambda item: item.name)
    paths = [item for item in paths if item.suffix.casefold() in {".mdl", ".vvd", ".vtx", ".phy"}]
    if len(paths) != 5:
        raise ValueError(f"expected five compiled sidecars under {root}")
    artifacts = [
        {"path": path.name, "size_bytes": path.stat().st_size, "sha256": digest(path)}
        for path in paths
    ]
    return {"total_bytes": sum(item["size_bytes"] for item in artifacts), "artifacts": artifacts}


def record(
    run: Path,
    *,
    strategy: str,
    compiled_name: str,
    optimize_log: str,
    compile_log: str,
    candidate_render: Path,
    shared_reference: Path,
) -> dict:
    candidate = json.loads((run / "candidate.json").read_text(encoding="utf-8"))
    metrics = json.loads((run / "candidate_metrics.json").read_text(encoding="utf-8"))
    if candidate.get("strategy") != strategy or candidate.get("ratio") != 0.35:
        raise ValueError("candidate is not the declared controlled r0.35 strategy")
    if metrics.get("strategy") != strategy:
        raise ValueError("candidate metrics strategy does not match")
    return {
        "strategy": strategy,
        "ratio": 0.35,
        "compiled": compiled_artifacts(run, compiled_name),
        "candidate_sha256": digest(run / "candidate.json"),
        "metrics_sha256": digest(run / "candidate_metrics.json"),
        "optimize_log_sha256": digest(run / optimize_log),
        "compile_log_sha256": digest(run / compile_log),
        "render": {
            "reference_manifest_sha256": digest(shared_reference / "render_manifest.json"),
            "reference_image_set_sha256": image_set_sha256(shared_reference),
            "candidate_manifest_sha256": digest(candidate_render / "render_manifest.json"),
            "candidate_image_set_sha256": image_set_sha256(candidate_render),
        },
        "raw_clay": raw_metrics(shared_reference, candidate_render),
    }


def build_payload(root: Path, blender: Path, studiomdl: Path) -> dict:
    adaptive = root / "wheel-adaptive-r035-ablation"
    importance = root / "wheel-hard50-r035"
    for relative in (
        Path("diggercars/pontiac_transam3/wheel/wh.smd"),
        Path("diggercars/pontiac_transam3/wheel/wh1.smd"),
    ):
        if digest(adaptive / relative) != digest(importance / relative):
            raise ValueError("controlled ablation source meshes differ")
    adaptive_render = adaptive / "renders-rim1-final-bcc9af2"
    importance_render = importance / "renders-rim1-final-bcc9af2"
    repeat_render = importance / "renders-rim1-final-bcc9af2-repeat"
    shared_reference = importance_render / "original"
    reference_pixels = image_set_sha256(shared_reference)
    candidate_pixels = image_set_sha256(importance_render / "optimized")
    repeat_reference_pixels = image_set_sha256(repeat_render / "original")
    repeat_candidate_pixels = image_set_sha256(repeat_render / "optimized")
    if (reference_pixels != repeat_reference_pixels
            or candidate_pixels != repeat_candidate_pixels):
        raise ValueError("final renderer is not deterministic at decoded RGBA pixel level")
    payload = {
        "schema_version": 2,
        "strategy": "blender-importance-map-v1",
        "family_id": "pontiac_transam_wheel",
        "toolchain": {"blender": digest(blender), "studiomdl": digest(studiomdl)},
        "implementation": {
            path: digest(REPO / path)
            for path in (
                "batch_optimize_maximum.py",
                "maximum_optimizer/importance_map.py",
                "maximum_optimizer/search.py",
                "render_previews.py",
                "maximum_optimizer/visual_validation.py",
                "benchmarks/lvs_models/build_blender_importance_map_v1.py",
            )
        },
        "baseline": record(
            adaptive, strategy="blender-adaptive-v1", compiled_name="compiled",
            optimize_log="optimize.log", compile_log="compile.log",
            candidate_render=adaptive_render / "optimized",
            shared_reference=shared_reference,
        ),
        "candidate": record(
            importance, strategy="blender-importance-map-v1", compiled_name="compiled-final",
            optimize_log="optimize-final.log", compile_log="compile-final.log",
            candidate_render=importance_render / "optimized",
            shared_reference=shared_reference,
        ),
        "quality": {
            "status": "uncalibrated-raw-ranking-only",
            "scope": "rim1-bind-8-views",
            "texture_status": "clay-authoritative",
            "container_status": "png-bytes-vary-but-decoded-pixels-are-identical",
            "render_determinism": {
                "status": "decoded-rgba-identical-across-repeat",
                "repeat_reference_image_set_sha256": repeat_reference_pixels,
                "repeat_candidate_image_set_sha256": repeat_candidate_pixels,
            },
        },
        "decision": {
            "winner": False,
            "status": "rejected-visible-boundary-regression",
            "reason": "same-ratio r0.35 ablation is only 410 bytes smaller and has worse worst-view silhouette and edge error",
        },
    }
    payload["evidence_sha256"] = canonical_importance_evidence_hash(payload)
    parse_importance_evidence(payload)
    return payload


def main() -> int:
    payload = build_payload(
        Path(os.environ["LVS_TASK8_IMPORTANCE_ROOT"]).resolve(),
        Path(os.environ["BLENDER_EXE"]).resolve(),
        Path(os.environ["STUDIOMDL_EXE"]).resolve(),
    )
    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
