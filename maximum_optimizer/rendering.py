from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess

from PIL import Image, ImageChops, ImageFilter

from .materials import MaterialSemantics
from .smd import parse_smd


_RENDER_SCRIPT = Path(__file__).resolve().parents[1] / "render_previews.py"
_EVIDENCE_NAME = "maximum_render_evidence.json"


@dataclass(frozen=True)
class RenderRequest:
    original_region_smd: Path
    candidate_region_smd: Path
    camera_json: Path
    output_dir: Path
    blender: Path
    pose: str
    material_roots: tuple[Path, ...] = ()
    material_directories: tuple[PurePosixPath, ...] = ()
    size: int = 512
    rgb_mae_limit: float = 0.035
    edge_error_limit: float = 1.5
    blender_version: str | None = None
    render_script: Path = field(default_factory=lambda: _RENDER_SCRIPT)

    def __post_init__(self) -> None:
        for name in ("original_region_smd", "candidate_region_smd", "camera_json", "output_dir", "blender", "render_script"):
            object.__setattr__(self, name, Path(getattr(self, name)))
        object.__setattr__(self, "material_roots", tuple(Path(path) for path in self.material_roots))
        directories = tuple(PurePosixPath(str(path).replace("\\", "/").strip("/")) for path in self.material_directories)
        if any(path.is_absolute() or ".." in path.parts for path in directories):
            raise ValueError("material directories must be relative")
        object.__setattr__(self, "material_directories", directories)
        if type(self.pose) is not str or not self.pose.strip():
            raise ValueError("render pose must be non-empty")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or not 64 <= self.size <= 4096:
            raise ValueError("render size must be an integer in [64, 4096]")
        for name in ("rgb_mae_limit", "edge_error_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class RenderEvidence:
    passed: bool
    rgb_mae: float
    edge_error: float
    output_dir: Path
    cache_key: str
    cache_hit: bool


def requires_targeted_render(
    semantics: MaterialSemantics,
    margin: float,
    confidence: float,
    reduction: float,
) -> bool:
    for name, value in (("margin", margin), ("confidence", confidence), ("reduction", reduction)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1]")
    return margin <= 0.15 or reduction >= 0.02 and (semantics.requires_render or confidence < 0.75)


def build_render_command(request: RenderRequest) -> list[str]:
    command = [
        str(request.blender),
        "--background",
        "--python",
        str(request.render_script),
        "--",
        "--before",
        str(request.original_region_smd),
        "--after",
        str(request.candidate_region_smd),
        "--out",
        str(request.output_dir),
        "--size",
        str(request.size),
        "--camera-json",
        str(request.camera_json),
        "--pose",
        request.pose,
    ]
    for root in request.material_roots:
        command.extend(("--material-root", str(root)))
    for directory in request.material_directories:
        command.extend(("--material-directory", directory.as_posix()))
    return command


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _referenced_material_files(request: RenderRequest) -> Iterable[Path]:
    materials: set[str] = set()
    for smd_path in (request.original_region_smd, request.candidate_region_smd):
        document = parse_smd(smd_path.read_text(encoding="utf-8", errors="replace"))
        materials.update(triangle.material.replace("\\", "/").removesuffix(".vmt") for triangle in document.triangles)
    for material in sorted(materials, key=str.casefold):
        relative = Path(*material.split("/")).with_suffix(".vmt")
        for root in request.material_roots:
            for directory in (PurePosixPath(),) + request.material_directories:
                candidate = root / "materials" / Path(*directory.parts) / relative
                if candidate.is_file():
                    yield candidate
                    break
            else:
                continue
            break


def _detect_blender_version(blender: Path) -> str:
    completed = subprocess.run(
        [str(blender), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Blender version query failed with exit code {completed.returncode}")
    first_line = completed.stdout.splitlines()[0].strip() if completed.stdout else ""
    if not first_line:
        raise RuntimeError("Blender version query returned no version")
    return first_line


def _cache_key(request: RenderRequest) -> str:
    version = request.blender_version or _detect_blender_version(request.blender)
    material_hashes = [
        {"path": path.as_posix().casefold(), "sha256": _sha256_file(path)}
        for path in _referenced_material_files(request)
    ]
    payload = {
        "schema": 1,
        "original_sha256": _sha256_file(request.original_region_smd),
        "candidate_sha256": _sha256_file(request.candidate_region_smd),
        "camera_sha256": _sha256_file(request.camera_json),
        "pose": request.pose,
        "size": request.size,
        "materials": material_hashes,
        "material_directories": [path.as_posix() for path in request.material_directories],
        "blender_version": version,
        "render_script_sha256": _sha256_file(request.render_script),
        "rgb_mae_limit": request.rgb_mae_limit,
        "edge_error_limit": request.edge_error_limit,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _image_pair_metrics(before_path: Path, after_path: Path) -> tuple[float, float]:
    with Image.open(before_path) as before_raw, Image.open(after_path) as after_raw:
        before = before_raw.convert("RGB")
        after = after_raw.convert("RGB")
        if before.size != after.size:
            raise ValueError("targeted render dimensions differ")
        difference = ImageChops.difference(before, after)
        histogram = difference.histogram()
        pixels = before.width * before.height
        rgb_mae = sum((index % 256) * count for index, count in enumerate(histogram)) / (pixels * 3.0 * 255.0)
        before_edges = before.convert("L").filter(ImageFilter.FIND_EDGES)
        after_edges = after.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_histogram = ImageChops.difference(before_edges, after_edges).histogram()
        edge_mae = sum(index * count for index, count in enumerate(edge_histogram)) / (pixels * 255.0)
        edge_error = edge_mae * min(before.size)
        return rgb_mae, edge_error


def _measure_output(output_dir: Path) -> tuple[float, float]:
    summary = json.loads((output_dir / "preview_summary.json").read_text(encoding="utf-8"))
    angles = summary.get("angles")
    before_images = summary.get("before", {}).get("images", {})
    after_images = summary.get("after", {}).get("images", {})
    if type(angles) is not list or not angles or type(before_images) is not dict or type(after_images) is not dict:
        raise ValueError("targeted render summary is invalid")
    metrics = []
    for angle in angles:
        if type(angle) is not str or angle not in before_images or angle not in after_images:
            raise ValueError("targeted render image map is incomplete")
        metrics.append(
            _image_pair_metrics(
                output_dir / str(before_images[angle]),
                output_dir / str(after_images[angle]),
            )
        )
    return max(value[0] for value in metrics), max(value[1] for value in metrics)


def _default_run_blender(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:]
        suffix = f": {detail[0]}" if detail else ""
        raise RuntimeError(f"targeted Blender render failed with exit code {completed.returncode}{suffix}")


def _cached_evidence(path: Path, output_dir: Path, cache_key: str) -> RenderEvidence | None:
    if not path.is_file() or not (output_dir / "preview_summary.json").is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict or payload.get("schema") != 1 or payload.get("cache_key") != cache_key:
            return None
        return RenderEvidence(
            bool(payload["passed"]),
            float(payload["rgb_mae"]),
            float(payload["edge_error"]),
            output_dir,
            cache_key,
            True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def render_region_comparison(
    request: RenderRequest,
    *,
    run_blender: Callable[[list[str]], None] = _default_run_blender,
) -> RenderEvidence:
    for path in (
        request.original_region_smd,
        request.candidate_region_smd,
        request.camera_json,
        request.render_script,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    cache_key = _cache_key(request)
    evidence_path = request.output_dir / _EVIDENCE_NAME
    cached = _cached_evidence(evidence_path, request.output_dir, cache_key)
    if cached is not None:
        return cached

    request.output_dir.mkdir(parents=True, exist_ok=True)
    run_blender(build_render_command(request))
    rgb_mae, edge_error = _measure_output(request.output_dir)
    passed = rgb_mae <= request.rgb_mae_limit and edge_error <= request.edge_error_limit
    payload = {
        "schema": 1,
        "cache_key": cache_key,
        "passed": passed,
        "rgb_mae": rgb_mae,
        "edge_error": edge_error,
    }
    evidence_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return RenderEvidence(passed, rgb_mae, edge_error, request.output_dir, cache_key, False)
