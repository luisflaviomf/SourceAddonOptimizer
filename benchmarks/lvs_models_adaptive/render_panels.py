#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageOps


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maximum_optimizer.benchmarking import LANES, load_corpus, load_family_results, write_json  # noqa: E402
from maximum_optimizer.qc_graph import scan_qc_occurrences  # noqa: E402
from maximum_optimizer.smd import parse_smd  # noqa: E402


VISUAL_MODELS = {
    "vw_beetle": "beetle.mdl",
    "caterham_620r": "light_fl_dam.mdl",
    "ferrari_365_fullrig": "ferrari_365fullrig.mdl",
    "ford_fairlane": "windscreen_dam.mdl",
    "vw_touareg": "wheel.mdl",
    "dodge_charger": "charger.mdl",
    "toyota_supra": "supra.mdl",
    "pontiac_transam_wheel": "wheel.mdl",
}
VIEW_MODES = ("preview", "clay", "uv", "normal", "silhouette")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render 1024px rounded-region comparison evidence and panels.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--partition", choices=("development", "holdout", "all"), default="all")
    parser.add_argument("--families", help="Comma-separated family IDs; defaults to the selected partition.")
    parser.add_argument("--lanes", help="Comma-separated lane IDs; defaults to all benchmark lanes.")
    parser.add_argument("--model-name", help="Override the visual MDL name; requires exactly one family.")
    return parser.parse_args()


def _find_model(models: Path, name: str) -> Path:
    matches = [path for path in models.rglob("*.mdl") if path.name.casefold() == name.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} under {models}, found {len(matches)}")
    return matches[0]


def _largest_mesh_smd(root: Path) -> Path:
    visual_sources = {
        root / Path(*occurrence.source_path.parts)
        for occurrence in scan_qc_occurrences(root)
        if occurrence.directive in {"$body", "$model", "$bodygroup/studio"}
    }
    candidates = []
    for path in visual_sources or set(root.rglob("*.smd")):
        try:
            triangles = len(parse_smd(path.read_text(encoding="utf-8", errors="replace")).triangles)
        except (OSError, ValueError):
            continue
        if triangles:
            candidates.append((triangles, path))
    if not candidates:
        raise RuntimeError(f"no mesh SMD generated under {root}")
    return max(candidates, key=lambda item: (item[0], item[1].as_posix()))[1]


def _corresponding_mesh_smd(reference_root: Path, reference: Path, candidate_root: Path) -> Path:
    reference_relative = reference.relative_to(reference_root).as_posix().casefold()
    reference_occurrences = tuple(
        occurrence
        for occurrence in scan_qc_occurrences(reference_root)
        if occurrence.source_path.as_posix().casefold() == reference_relative
    )
    candidate_by_identity = {
        (occurrence.qc_path.as_posix().casefold(), occurrence.directive, occurrence.line): occurrence
        for occurrence in scan_qc_occurrences(candidate_root)
    }
    for occurrence in reference_occurrences:
        identity = (occurrence.qc_path.as_posix().casefold(), occurrence.directive, occurrence.line)
        candidate_occurrence = candidate_by_identity.get(identity)
        if candidate_occurrence is None:
            continue
        candidate = candidate_root / Path(*candidate_occurrence.source_path.parts)
        try:
            triangles = len(parse_smd(candidate.read_text(encoding="utf-8", errors="replace")).triangles)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"matched candidate visual SMD is invalid: {candidate_occurrence.source_path}") from exc
        if not triangles:
            raise RuntimeError(f"matched candidate visual SMD is empty: {candidate_occurrence.source_path}")
        return candidate
    raise RuntimeError(f"candidate QC does not contain the original visual occurrence: {reference_relative}")


def _decompile(
    model: Path,
    destination: Path,
    reference: tuple[Path, Path] | None = None,
) -> Path:
    if destination.is_dir():
        existing = list(destination.rglob("*.smd"))
        if existing:
            return _corresponding_mesh_smd(*reference, destination) if reference else _largest_mesh_smd(destination)
    command = [
        sys.executable,
        str(REPO_ROOT / "batch_decompile_organize.py"),
        str(model),
        "--out", str(destination),
        "--crowbar", str(REPO_ROOT / "CrowbarCommandLineDecomp.exe"),
        "--force",
        "--jobs", "1",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False, capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"decompile failed for {model}: {detail[-1000:]}")
    return _corresponding_mesh_smd(*reference, destination) if reference else _largest_mesh_smd(destination)


def _render(
    blender: Path,
    before: Path,
    after: Path,
    destination: Path,
    mode: str,
    material_roots: tuple[Path, ...],
) -> None:
    summary = destination / "preview_summary.json"
    if summary.is_file():
        return
    command = [
        str(blender), "--background", "--python", str(REPO_ROOT / "render_previews.py"), "--",
        "--before", str(before), "--after", str(after), "--out", str(destination),
        "--size", "1024", "--angles", "iso1", "--pose", "reference", "--view-mode", mode,
    ]
    for root in material_roots:
        command.extend(("--material-root", str(root)))
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"render failed ({mode}): {detail[-1500:]}")


def _cell(path: Path, label: str, size: int = 480) -> Image.Image:
    with Image.open(path) as raw:
        image = ImageOps.contain(raw.convert("RGB"), (size, size)).copy()
    canvas = Image.new("RGB", (size, size + 32), (24, 26, 30))
    canvas.paste(image, ((size - image.width) // 2, 32 + (size - image.height) // 2))
    ImageDraw.Draw(canvas).text((8, 8), label, fill=(245, 245, 245))
    return canvas


def _difference(before: Path, after: Path, destination: Path) -> Path:
    with Image.open(before) as before_raw, Image.open(after) as after_raw:
        diff = ImageChops.difference(before_raw.convert("RGB"), after_raw.convert("RGB"))
        diff = ImageOps.autocontrast(diff, cutoff=0.1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        diff.save(destination)
    return destination


def _build_panel(family_id: str, raw_root: Path, destination: Path, lanes: tuple[str, ...]) -> None:
    if not lanes:
        raise ValueError("at least one lane is required")
    columns = ("original", *lanes)
    rows = ("preview", "clay", "uv", "normal", "normal-deviation", "silhouette-difference")
    cells = []
    reference_lane = lanes[0]
    for row in rows:
        row_cells = []
        for column in columns:
            if row in VIEW_MODES:
                lane = reference_lane if column == "original" else column
                side = "original" if column == "original" else "optimized"
                path = raw_root / lane / row / side / "iso1.png"
            elif column == "original":
                path = raw_root / reference_lane / "normal" / "original" / "iso1.png"
            else:
                mode = "normal" if row == "normal-deviation" else "silhouette"
                path = _difference(
                    raw_root / column / mode / "original" / "iso1.png",
                    raw_root / column / mode / "optimized" / "iso1.png",
                    raw_root / column / f"{row}.png",
                )
            row_cells.append(_cell(path, f"{row} | {column}"))
        cells.append(row_cells)
    width = sum(cell.width for cell in cells[0])
    height = sum(row[0].height for row in cells)
    panel = Image.new("RGB", (width, height), (18, 20, 24))
    y = 0
    for row in cells:
        x = 0
        for cell in row:
            panel.paste(cell, (x, y))
            x += cell.width
        y += row[0].height
    destination.parent.mkdir(parents=True, exist_ok=True)
    panel.save(destination, optimize=True)


def main() -> int:
    args = _arguments()
    results_root = args.results.resolve()
    output_root = args.out.resolve()
    blender = args.blender.resolve()
    if not blender.is_file():
        raise FileNotFoundError(blender)
    corpus = load_corpus(Path(__file__).with_name("corpus.json"))
    selected_family_ids = (
        {value.strip() for value in args.families.split(",") if value.strip()}
        if args.families else {family.id for family in corpus.partition(args.partition)}
    )
    known_family_ids = {family.id for family in corpus.families}
    unknown_families = selected_family_ids - known_family_ids
    if unknown_families:
        raise ValueError(f"unknown families: {sorted(unknown_families)}")
    selected_lanes = tuple(value.strip() for value in args.lanes.split(",") if value.strip()) if args.lanes else tuple(
        lane.id for lane in LANES
    )
    known_lanes = {lane.id for lane in LANES}
    unknown_lanes = set(selected_lanes) - known_lanes
    if unknown_lanes:
        raise ValueError(f"unknown lanes: {sorted(unknown_lanes)}")
    if not selected_lanes:
        raise ValueError("at least one lane is required")
    if args.model_name and len(selected_family_ids) != 1:
        raise ValueError("--model-name requires exactly one selected family")
    results = load_family_results(results_root)
    by_key = {(result.family_id, result.lane): result for result in results}
    manifest = {"schema": 1, "resolution": 1024, "panels": [], "missing_categories": ["weapon_sights"]}
    for family in corpus.families:
        if family.id not in selected_family_ids:
            continue
        lane_results = []
        for lane_id in selected_lanes:
            result = by_key.get((family.id, lane_id))
            if result is None:
                raise RuntimeError(f"missing result for {family.id}/{lane_id}")
            lane_results.append(result)
        model_name = args.model_name or VISUAL_MODELS[family.id]
        family_root = output_root / family.id
        original_model = _find_model(Path(lane_results[0].work_path).parent / "source" / family.id / "models", model_name)
        original_decompile_root = family_root / "decompiled" / "original"
        original_smd = _decompile(original_model, original_decompile_root)
        raw_root = family_root / "raw"
        candidate_smds = {}
        for result in lane_results:
            candidate_model = _find_model(Path(result.output_path) / "models", model_name)
            candidate_smd = _decompile(
                candidate_model,
                family_root / "decompiled" / result.lane,
                (original_decompile_root, original_smd),
            )
            candidate_smds[result.lane] = candidate_smd
            material_roots = (Path(result.output_path),)
            for mode in VIEW_MODES:
                _render(blender, original_smd, candidate_smd, raw_root / result.lane / mode, mode, material_roots)
        panel_path = family_root / f"{family.id}-comparison.png"
        _build_panel(family.id, raw_root, panel_path, selected_lanes)
        manifest["panels"].append(
            {
                "family_id": family.id,
                "focus_regions": list(family.focus_regions),
                "visual_model": model_name,
                "panel": str(panel_path),
                "original_smd": str(original_smd),
                "candidate_smds": {name: str(path) for name, path in candidate_smds.items()},
            }
        )
        print(f"[PANELS] {family.id}: {panel_path}")
    write_json(output_root / "visual-manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
