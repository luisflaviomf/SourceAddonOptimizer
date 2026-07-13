"""Candidate-independent representative animation selection for calibration.

Only exact source animation/corrective pairs participate.  Candidate outputs
are never inspected, which prevents the candidate from choosing its own pose.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

from PIL import Image


_NODE = re.compile(r'^\s*(-?\d+)\s+"([^"]+)"\s+-?\d+\s*$')
_TIME = re.compile(r"^\s*time\s+(-?\d+)\s*$", re.IGNORECASE)
_TRANSFORM = re.compile(
    r"^\s*(-?\d+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$"
)
_ZERO = (Decimal(0),) * 6


def _canonical_hash(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return PurePosixPath(*path.relative_to(root).parts).as_posix()


@dataclass(frozen=True)
class _Animation:
    bones: Mapping[int, str]
    frames: Mapping[int, Mapping[int, tuple[Decimal, ...]]]


def _parse_animation(path: Path) -> _Animation:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read exact animation source: {path}") from exc
    section: str | None = None
    bones: dict[int, str] = {}
    frames: dict[int, dict[int, tuple[Decimal, ...]]] = {}
    current_frame: int | None = None
    for line in lines:
        stripped = line.strip().casefold()
        if stripped in {"nodes", "skeleton"}:
            section = stripped
            current_frame = None
            continue
        if stripped == "end":
            section = None
            current_frame = None
            continue
        if section == "nodes":
            match = _NODE.fullmatch(line)
            if match is not None:
                index = int(match.group(1))
                if index in bones:
                    raise ValueError(f"duplicate exact animation bone in {path}")
                bones[index] = match.group(2)
        elif section == "skeleton":
            time_match = _TIME.fullmatch(line)
            if time_match is not None:
                current_frame = int(time_match.group(1))
                if current_frame in frames:
                    raise ValueError(f"duplicate exact animation frame in {path}")
                frames[current_frame] = {}
                continue
            transform_match = _TRANSFORM.fullmatch(line)
            if transform_match is not None:
                if current_frame is None:
                    raise ValueError(f"animation transform precedes time in {path}")
                bone = int(transform_match.group(1))
                if bone in frames[current_frame]:
                    raise ValueError(f"duplicate exact animation transform in {path}")
                try:
                    values = tuple(Decimal(transform_match.group(index)) for index in range(2, 8))
                except ArithmeticError as exc:
                    raise ValueError(f"invalid exact animation transform in {path}") from exc
                if any(not value.is_finite() for value in values):
                    raise ValueError(f"non-finite exact animation transform in {path}")
                frames[current_frame][bone] = values
    if not bones or not frames or any(bone not in bones for frame in frames.values() for bone in frame):
        raise ValueError(f"exact animation source is structurally invalid: {path}")
    return _Animation(bones, frames)


@dataclass(frozen=True)
class AnimationPoseSelection:
    selector: str
    animation_relative_path: str
    reference_relative_path: str
    animation_sha256: str
    reference_sha256: str
    frame: int
    bone_index: int
    bone_name: str
    displacement: float
    displacement_squared: str
    geometry_inventory_sha256: str
    selector_input_sha256: str
    selection_sha256: str

    @property
    def pose_name(self) -> str:
        return Path(self.animation_relative_path).stem.casefold()

    def to_payload(self) -> dict[str, object]:
        return {
            "animation_relative_path": self.animation_relative_path,
            "animation_sha256": self.animation_sha256,
            "bone_index": self.bone_index,
            "bone_name": self.bone_name,
            "candidate_inputs_consulted": False,
            "displacement": self.displacement,
            "displacement_squared": self.displacement_squared,
            "frame": self.frame,
            "geometry_inventory_sha256": self.geometry_inventory_sha256,
            "pose_name": self.pose_name,
            "reference_relative_path": self.reference_relative_path,
            "reference_sha256": self.reference_sha256,
            "selection_sha256": self.selection_sha256,
            "selector": self.selector,
            "selector_input_sha256": self.selector_input_sha256,
        }


@dataclass(frozen=True)
class PosePixelGateProof:
    image_count: int
    total_pixels: int
    changed_pixels: int
    changed_fraction: float
    mean_absolute_error: float
    minimum_changed_fraction: float
    bind_pixel_bundle_sha256: str
    posed_pixel_bundle_sha256: str
    evidence_sha256: str


def verify_pose_pixel_gate(
    bind_images: Mapping[str, str | Path],
    posed_images: Mapping[str, str | Path],
    *,
    minimum_changed_fraction: float = 0.0001,
) -> PosePixelGateProof:
    """Require a real decoded-RGBA change between bind and selected pose."""

    if (
        not isinstance(minimum_changed_fraction, (int, float))
        or isinstance(minimum_changed_fraction, bool)
        or not math.isfinite(float(minimum_changed_fraction))
        or not 0.0 < float(minimum_changed_fraction) <= 1.0
    ):
        raise ValueError("pose pixel gate minimum is invalid")
    bind_keys = tuple(sorted(bind_images))
    if not bind_keys or bind_keys != tuple(sorted(posed_images)):
        raise ValueError("pose pixel gate image keys differ")
    bind_inventory: list[dict[str, object]] = []
    posed_inventory: list[dict[str, object]] = []
    total_pixels = 0
    changed_pixels = 0
    absolute_error = 0
    for key in bind_keys:
        if not isinstance(key, str) or not key:
            raise ValueError("pose pixel gate image key is invalid")
        bind_path = Path(bind_images[key]).resolve(strict=True)
        posed_path = Path(posed_images[key]).resolve(strict=True)
        try:
            with Image.open(bind_path) as source:
                bind_size = source.size
                bind_rgba = source.convert("RGBA").tobytes()
            with Image.open(posed_path) as source:
                posed_size = source.size
                posed_rgba = source.convert("RGBA").tobytes()
        except (OSError, ValueError) as exc:
            raise ValueError(f"pose pixel gate cannot decode {key}") from exc
        if bind_size != posed_size or len(bind_rgba) != len(posed_rgba):
            raise ValueError(f"pose pixel gate dimensions differ for {key}")
        pixels = bind_size[0] * bind_size[1]
        total_pixels += pixels
        for offset in range(0, len(bind_rgba), 4):
            left = bind_rgba[offset:offset + 4]
            right = posed_rgba[offset:offset + 4]
            if left != right:
                changed_pixels += 1
            absolute_error += sum(abs(a - b) for a, b in zip(left, right))
        bind_inventory.append({
            "height": bind_size[1], "key": key,
            "rgba_sha256": hashlib.sha256(bind_rgba).hexdigest(), "width": bind_size[0],
        })
        posed_inventory.append({
            "height": posed_size[1], "key": key,
            "rgba_sha256": hashlib.sha256(posed_rgba).hexdigest(), "width": posed_size[0],
        })
    changed_fraction = changed_pixels / total_pixels
    mean_absolute_error = absolute_error / (total_pixels * 4)
    if changed_fraction < float(minimum_changed_fraction) or mean_absolute_error <= 0.0:
        raise ValueError(
            "selected animation has no meaningful decoded pixel displacement"
        )
    bind_bundle = _canonical_hash(bind_inventory)
    posed_bundle = _canonical_hash(posed_inventory)
    unsigned = {
        "bind_pixel_bundle_sha256": bind_bundle,
        "changed_fraction": changed_fraction,
        "changed_pixels": changed_pixels,
        "image_count": len(bind_keys),
        "mean_absolute_error": mean_absolute_error,
        "minimum_changed_fraction": float(minimum_changed_fraction),
        "posed_pixel_bundle_sha256": posed_bundle,
        "total_pixels": total_pixels,
    }
    return PosePixelGateProof(
        image_count=len(bind_keys), total_pixels=total_pixels,
        changed_pixels=changed_pixels, changed_fraction=changed_fraction,
        mean_absolute_error=mean_absolute_error,
        minimum_changed_fraction=float(minimum_changed_fraction),
        bind_pixel_bundle_sha256=bind_bundle,
        posed_pixel_bundle_sha256=posed_bundle,
        evidence_sha256=_canonical_hash(unsigned),
    )


def _geometry_extents(source_root: Path) -> tuple[dict[int, float], str]:
    bounds: dict[int, list[list[float]]] = {}
    inventory: list[dict[str, object]] = []
    forbidden = {"output", "compiled", "compiled-hybrid", "maximum_direct_raw"}
    for path in sorted(source_root.rglob("*.smd"), key=lambda item: _relative(item, source_root).casefold()):
        relative = _relative(path, source_root)
        if any(part.casefold() in forbidden for part in PurePosixPath(relative).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read exact geometry source: {path}") from exc
        try:
            start = next(index for index, line in enumerate(lines) if line.strip().casefold() == "triangles")
        except StopIteration:
            continue
        inventory.append({"bytes": path.stat().st_size, "path": relative, "sha256": _file_hash(path)})
        for line in lines[start + 1:]:
            if line.strip().casefold() == "end":
                break
            tokens = line.split()
            if len(tokens) < 9:
                continue
            try:
                parent = int(tokens[0])
                position = tuple(float(tokens[index]) for index in range(1, 4))
            except ValueError:
                continue
            influenced = {parent}
            if len(tokens) >= 10:
                try:
                    link_count = int(tokens[9])
                    for offset in range(link_count):
                        bone = int(tokens[10 + offset * 2])
                        weight = float(tokens[11 + offset * 2])
                        if weight > 0.0:
                            influenced.add(bone)
                except (ValueError, IndexError):
                    raise ValueError(f"invalid exact geometry vertex weights in {path}")
            for bone in influenced:
                if bone not in bounds:
                    bounds[bone] = [list(position), list(position)]
                else:
                    for axis, value in enumerate(position):
                        bounds[bone][0][axis] = min(bounds[bone][0][axis], value)
                        bounds[bone][1][axis] = max(bounds[bone][1][axis], value)
    if not inventory or not bounds:
        raise ValueError("exact geometry inventory has no weighted mesh vertices")
    extents = {
        bone: math.dist(minimum, maximum)
        for bone, (minimum, maximum) in bounds.items()
    }
    return extents, _canonical_hash({
        "inventory": inventory,
        "kind": "exact-source-weighted-geometry-v1",
    })


def _quaternion(values: tuple[Decimal, ...]) -> tuple[float, float, float, float]:
    x, y, z = (float(value) * 0.5 for value in values[3:6])
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return (
        cz * cy * cx + sz * sy * sx,
        cz * cy * sx - sz * sy * cx,
        cz * sy * cx + sz * cy * sx,
        sz * cy * cx - cz * sy * sx,
    )


def _bone_displacement(
    values: tuple[Decimal, ...],
    baseline: tuple[Decimal, ...],
    extent: float,
) -> float:
    translation = math.dist(
        tuple(float(value) for value in values[:3]),
        tuple(float(value) for value in baseline[:3]),
    )
    left = _quaternion(values)
    right = _quaternion(baseline)
    dot = min(1.0, max(-1.0, abs(sum(a * b for a, b in zip(left, right)))))
    angle = 2.0 * math.acos(dot)
    return translation + extent * 2.0 * math.sin(angle * 0.5)


def select_animation_pose(
    animation_root: str | Path,
    *,
    source_root: str | Path,
) -> AnimationPoseSelection:
    """Choose the exact paired state with the largest bone transform delta."""

    root = Path(animation_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("exact animation root must be a directory")
    geometry_root = Path(source_root).resolve(strict=True)
    if not geometry_root.is_dir():
        raise ValueError("exact geometry source root must be a directory")
    geometry_extents, geometry_inventory_sha256 = _geometry_extents(geometry_root)
    pairs: list[tuple[Path, Path]] = []
    for reference in root.rglob("*_corrective_animation.smd"):
        stem = reference.name[: -len("_corrective_animation.smd")]
        animation = reference.with_name(stem + ".smd")
        if animation.is_file():
            pairs.append((animation.resolve(), reference.resolve()))
    pairs.sort(key=lambda item: (_relative(item[0], root).casefold(), _relative(item[0], root)))
    if not pairs:
        raise ValueError("no paired exact animation/corrective sources were found")

    inventory: list[dict[str, str]] = []
    ranked: list[tuple[float, str, str, int, int, str, Path, Path]] = []
    pair_hashes: dict[tuple[Path, Path], tuple[str, str]] = {}
    for animation_path, reference_path in pairs:
        animation_sha = _file_hash(animation_path)
        reference_sha = _file_hash(reference_path)
        pair_hashes[(animation_path, reference_path)] = (animation_sha, reference_sha)
        animation_relative = _relative(animation_path, root)
        reference_relative = _relative(reference_path, root)
        inventory.append({
            "animation_relative_path": animation_relative,
            "animation_sha256": animation_sha,
            "reference_relative_path": reference_relative,
            "reference_sha256": reference_sha,
        })
        animation = _parse_animation(animation_path)
        reference = _parse_animation(reference_path)
        reference_frame = reference.frames[min(reference.frames)]
        for frame_index, transforms in sorted(animation.frames.items()):
            for bone_index in sorted(set(transforms) | set(reference_frame)):
                values = transforms.get(bone_index, _ZERO)
                baseline = reference_frame.get(bone_index, _ZERO)
                displacement = _bone_displacement(
                    values, baseline, geometry_extents.get(bone_index, 0.0),
                )
                bone_name = animation.bones.get(bone_index, reference.bones.get(bone_index, ""))
                if not bone_name:
                    raise ValueError("animation/reference bone lineage differs")
                ranked.append((
                    displacement,
                    animation_relative.casefold(), animation_relative,
                    frame_index, bone_index, bone_name,
                    animation_path, reference_path,
                ))
    selector_input_sha256 = _canonical_hash({
        "candidate_inputs_consulted": False,
        "pairs": inventory,
        "geometry_inventory_sha256": geometry_inventory_sha256,
        "selector": "exact-max-bone-displacement-v1",
    })
    ranked.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4], item[5]))
    winner = ranked[0]
    if winner[0] <= 0:
        raise ValueError("paired exact animations have no meaningful displacement")
    displacement, _folded, animation_relative, frame, bone_index, bone_name, animation_path, reference_path = winner
    animation_sha, reference_sha = pair_hashes[(animation_path, reference_path)]
    displacement_squared = format(displacement * displacement, ".17g")
    unsigned = {
        "animation_relative_path": animation_relative,
        "animation_sha256": animation_sha,
        "bone_index": bone_index,
        "bone_name": bone_name,
        "candidate_inputs_consulted": False,
        "displacement_squared": displacement_squared,
        "frame": frame,
        "geometry_inventory_sha256": geometry_inventory_sha256,
        "reference_relative_path": _relative(reference_path, root),
        "reference_sha256": reference_sha,
        "selector": "exact-max-bone-displacement-v1",
        "selector_input_sha256": selector_input_sha256,
    }
    return AnimationPoseSelection(
        selector="exact-max-bone-displacement-v1",
        animation_relative_path=animation_relative,
        reference_relative_path=_relative(reference_path, root),
        animation_sha256=animation_sha,
        reference_sha256=reference_sha,
        frame=frame,
        bone_index=bone_index,
        bone_name=bone_name,
        displacement=displacement,
        displacement_squared=displacement_squared,
        geometry_inventory_sha256=geometry_inventory_sha256,
        selector_input_sha256=selector_input_sha256,
        selection_sha256=_canonical_hash(unsigned),
    )
