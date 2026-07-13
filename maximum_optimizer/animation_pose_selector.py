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
import stat
import struct
from typing import Mapping
import zlib


_NODE = re.compile(r'^\s*(-?\d+)\s+"([^"]+)"\s+(-?\d+)\s*$')
_TIME = re.compile(r"^\s*time\s+(-?\d+)\s*$", re.IGNORECASE)
_TRANSFORM = re.compile(
    r"^\s*(-?\d+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$"
)
_ZERO = (Decimal(0),) * 6
_MAX_EXACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_EXACT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ANIMATION_PAIRS = 64
_MAX_GEOMETRY_FILES = 256
_MAX_BONES = 4096
_MAX_FRAMES = 4096
_MAX_VERTICES = 20_000_000
_MAX_IMAGES = 64
_MAX_IMAGE_BYTES = 128 * 1024 * 1024
_MAX_IMAGE_DIMENSION = 8192
_MAX_TOTAL_PIXELS = 128 * 1024 * 1024


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
    parents: Mapping[int, int]
    frames: Mapping[int, Mapping[int, tuple[Decimal, ...]]]


@dataclass(frozen=True)
class _GeometryVertex:
    position: tuple[float, float, float]
    weights: tuple[tuple[int, float], ...]


def _parse_animation(path: Path, payload: bytes | None = None) -> _Animation:
    try:
        lines = (path.read_bytes() if payload is None else payload).decode(
            "utf-8", errors="strict",
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read exact animation source: {path}") from exc
    section: str | None = None
    bones: dict[int, str] = {}
    parents: dict[int, int] = {}
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
                parents[index] = int(match.group(3))
                if len(bones) > _MAX_BONES:
                    raise ValueError("exact animation bone cap exceeded")
        elif section == "skeleton":
            time_match = _TIME.fullmatch(line)
            if time_match is not None:
                current_frame = int(time_match.group(1))
                if current_frame in frames:
                    raise ValueError(f"duplicate exact animation frame in {path}")
                frames[current_frame] = {}
                if len(frames) > _MAX_FRAMES:
                    raise ValueError("exact animation frame cap exceeded")
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
    if any(parent != -1 and parent not in bones for parent in parents.values()):
        raise ValueError(f"exact animation bone lineage is invalid: {path}")
    return _Animation(bones, parents, frames)


def _safe_exact_file(root: Path, value: str | Path) -> tuple[Path, bytes, str]:
    raw = Path(value)
    selected = raw if raw.is_absolute() else root / raw
    try:
        selected = selected.resolve(strict=True)
        selected.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("exact allowlist path is not contained by its root") from exc
    relative = selected.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        info = cursor.lstat()
        reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        if cursor.is_symlink() or reparse:
            raise ValueError("exact allowlist path has reparse ancestry")
    before = selected.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_EXACT_FILE_BYTES:
        raise ValueError("exact allowlist file is invalid or exceeds byte cap")
    payload = selected.read_bytes()
    after = selected.lstat()
    if (
        len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("exact allowlist file changed while snapshotted")
    return selected, payload, hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class AnimationPoseSelection:
    selector: str
    animation_relative_path: str
    reference_relative_path: str
    animation_sha256: str
    reference_sha256: str
    frame: int
    source_time: int
    bone_index: int
    bone_name: str
    displacement: float
    rms_displacement: float
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
            "baseline": "geometry-rest",
            "bone_index": self.bone_index,
            "bone_name": self.bone_name,
            "candidate_inputs_consulted": False,
            "corrective_used_as_baseline": False,
            "displacement": self.displacement,
            "displacement_squared": self.displacement_squared,
            "frame": self.frame,
            "geometry_inventory_sha256": self.geometry_inventory_sha256,
            "pose_name": self.pose_name,
            "reference_relative_path": self.reference_relative_path,
            "reference_sha256": self.reference_sha256,
            "raw_render_max_displacement": self.displacement,
            "raw_render_rms_displacement": self.rms_displacement,
            "rms_displacement": self.rms_displacement,
            "selection_sha256": self.selection_sha256,
            "selector": self.selector,
            "selector_input_sha256": self.selector_input_sha256,
            "source_time": self.source_time,
        }


@dataclass(frozen=True)
class BindOnlyPoseSelection:
    selector: str
    reason: str
    geometry_inventory_sha256: str
    selector_input_sha256: str
    selection_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "candidate_inputs_consulted": False,
            "geometry_inventory_sha256": self.geometry_inventory_sha256,
            "pose_keys": ["bind"],
            "reason": self.reason,
            "selection_sha256": self.selection_sha256,
            "selector": self.selector,
            "selector_input_sha256": self.selector_input_sha256,
        }


@dataclass(frozen=True)
class PosePixelGateProof:
    image_count: int
    total_pixels: int
    foreground_pixels: int
    changed_pixels: int
    changed_fraction: float
    mean_absolute_error: float
    minimum_changed_fraction: float
    minimum_silhouette_pixels: int
    bind_pixel_bundle_sha256: str
    posed_pixel_bundle_sha256: str
    views: tuple[Mapping[str, object], ...]
    camera_directions: tuple[Mapping[str, object], ...]
    qualified_silhouette_views: tuple[str, ...]
    evidence_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "bind_pixel_bundle_sha256": self.bind_pixel_bundle_sha256,
            "changed_fraction": self.changed_fraction,
            "changed_pixels": self.changed_pixels,
            "evidence_sha256": self.evidence_sha256,
            "foreground_pixels": self.foreground_pixels,
            "image_count": self.image_count,
            "mean_absolute_error": self.mean_absolute_error,
            "minimum_changed_fraction": self.minimum_changed_fraction,
            "minimum_silhouette_pixels": self.minimum_silhouette_pixels,
            "posed_pixel_bundle_sha256": self.posed_pixel_bundle_sha256,
            "total_pixels": self.total_pixels,
            "views": [dict(view) for view in self.views],
            "camera_directions": [dict(item) for item in self.camera_directions],
            "qualified_silhouette_views": list(self.qualified_silhouette_views),
        }


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_distance:
        return left
    return above if above_distance <= upper_distance else upper_left


def _decode_png_rgba8(path: Path) -> tuple[tuple[int, int], bytes]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_IMAGE_BYTES:
        raise ValueError("pose pixel gate PNG byte cap is invalid")
    payload = path.read_bytes()
    after = path.lstat()
    if (
        len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("pose pixel gate PNG changed while decoded")
    signature = b"\x89PNG\r\n\x1a\n"
    if not payload.startswith(signature) or len(payload) > _MAX_IMAGE_BYTES:
        raise ValueError("pose pixel gate PNG signature/byte cap is invalid")
    offset = len(signature)
    width = height = None
    compressed = bytearray()
    saw_end = False
    saw_idat = False
    idat_closed = False
    chunk_index = 0
    while offset < len(payload):
        if len(payload) - offset < 12:
            raise ValueError("pose pixel gate PNG chunk is truncated")
        size = struct.unpack(">I", payload[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(payload) or size > _MAX_IMAGE_BYTES:
            raise ValueError("pose pixel gate PNG chunk cap is invalid")
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", payload[offset + 8 + size:end])[0]
        if (
            len(kind) != 4
            or any(not (65 <= value <= 90 or 97 <= value <= 122) for value in kind)
        ):
            raise ValueError("pose pixel gate PNG chunk type is invalid")
        if zlib.crc32(kind + data) != crc:
            raise ValueError("pose pixel gate PNG chunk CRC differs")
        if chunk_index == 0 and kind != b"IHDR":
            raise ValueError("pose pixel gate PNG header must be first")
        if kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"} and not (kind[0] & 0x20):
            raise ValueError("pose pixel gate PNG has unknown critical chunk")
        if kind == b"IHDR":
            if chunk_index != 0 or width is not None or len(data) != 13:
                raise ValueError("pose pixel gate PNG header is invalid")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data,
            )
            if (
                not 0 < width <= _MAX_IMAGE_DIMENSION
                or not 0 < height <= _MAX_IMAGE_DIMENSION
                or (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0)
            ):
                raise ValueError("pose pixel gate requires bounded non-interlaced RGBA8 PNG")
        elif kind == b"PLTE":
            if width is None or saw_idat or not data or len(data) > 768 or len(data) % 3:
                raise ValueError("pose pixel gate PNG palette order is invalid")
        elif kind == b"IDAT":
            if width is None or idat_closed:
                raise ValueError("pose pixel gate PNG data chunk order is invalid")
            saw_idat = True
            compressed.extend(data)
            if len(compressed) > _MAX_IMAGE_BYTES:
                raise ValueError("pose pixel gate compressed byte cap exceeded")
        elif kind == b"IEND":
            if size != 0 or width is None or not saw_idat:
                raise ValueError("pose pixel gate PNG end chunk is invalid")
            saw_end = True
            offset = end
            break
        elif saw_idat:
            idat_closed = True
        offset = end
        chunk_index += 1
    if width is None or height is None or not saw_end or offset != len(payload) or not compressed:
        raise ValueError("pose pixel gate PNG inventory is incomplete")
    row_bytes = width * 4
    if width * height > _MAX_TOTAL_PIXELS:
        raise ValueError("pose pixel gate PNG pixel cap exceeded")
    expected = height * (row_bytes + 1)
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(bytes(compressed), expected + 1)
    if len(raw) > expected:
        raise ValueError("pose pixel gate PNG decoded byte cap exceeded")
    remainder = decompressor.flush(max(1, expected + 1 - len(raw)))
    raw += remainder
    if (
        len(raw) != expected or not decompressor.eof
        or decompressor.unused_data or decompressor.unconsumed_tail
    ):
        raise ValueError("pose pixel gate PNG decoded byte inventory differs")
    output = bytearray(height * row_bytes)
    previous = bytearray(row_bytes)
    source_offset = 0
    for row in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        scanline = bytearray(raw[source_offset:source_offset + row_bytes])
        source_offset += row_bytes
        if filter_type not in range(5):
            raise ValueError("pose pixel gate PNG filter is invalid")
        for index in range(row_bytes):
            left = scanline[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            if filter_type == 1:
                scanline[index] = (scanline[index] + left) & 0xFF
            elif filter_type == 2:
                scanline[index] = (scanline[index] + above) & 0xFF
            elif filter_type == 3:
                scanline[index] = (scanline[index] + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                scanline[index] = (scanline[index] + _paeth(left, above, upper_left)) & 0xFF
        start = row * row_bytes
        output[start:start + row_bytes] = scanline
        previous = scanline
    return (width, height), bytes(output)


def _visible_rgba(payload: bytes) -> bytes:
    visible = bytearray(len(payload))
    for offset in range(0, len(payload), 4):
        red, green, blue, alpha = payload[offset:offset + 4]
        visible[offset:offset + 4] = bytes((
            (red * alpha + 127) // 255,
            (green * alpha + 127) // 255,
            (blue * alpha + 127) // 255,
            alpha,
        ))
    return bytes(visible)


def verify_pose_pixel_gate(
    bind_images: Mapping[str, str | Path],
    posed_images: Mapping[str, str | Path],
    *,
    camera_directions: Mapping[str, tuple[float, float, float]],
    minimum_changed_fraction: float = 0.0001,
    minimum_silhouette_pixels: int = 27,
    required_size: tuple[int, int] | None = None,
) -> PosePixelGateProof:
    """Require silhouette motion in at least two orthogonal decoded views."""

    if (
        not isinstance(minimum_changed_fraction, (int, float))
        or isinstance(minimum_changed_fraction, bool)
        or not math.isfinite(float(minimum_changed_fraction))
        or not 0.0 < float(minimum_changed_fraction) <= 1.0
    ):
        raise ValueError("pose pixel gate minimum is invalid")
    if (
        type(minimum_silhouette_pixels) is not int
        or not 1 <= minimum_silhouette_pixels <= _MAX_TOTAL_PIXELS
        or required_size is not None
        and (
            type(required_size) is not tuple or len(required_size) != 2
            or any(type(value) is not int or not 1 <= value <= _MAX_IMAGE_DIMENSION for value in required_size)
        )
    ):
        raise ValueError("pose pixel gate silhouette/size bound is invalid")
    raw_bind_keys = tuple(bind_images)
    raw_pose_keys = tuple(posed_images)
    raw_direction_keys = tuple(camera_directions)
    if any(type(key) is not str or not key for key in (*raw_bind_keys, *raw_pose_keys, *raw_direction_keys)):
        raise ValueError("pose pixel gate image/camera key is invalid")
    bind_keys = tuple(sorted(raw_bind_keys))
    if (
        not bind_keys or len(bind_keys) > _MAX_IMAGES
        or bind_keys != tuple(sorted(posed_images))
    ):
        raise ValueError("pose pixel gate image keys differ")
    if bind_keys != tuple(sorted(camera_directions)):
        raise ValueError("pose pixel gate camera direction keys differ")
    directions = {}
    for key in bind_keys:
        raw_direction = camera_directions[key]
        if (
            not isinstance(raw_direction, (tuple, list)) or len(raw_direction) != 3
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) for value in raw_direction)
        ):
            raise ValueError("pose pixel gate camera direction is invalid")
        length = math.sqrt(sum(float(value) ** 2 for value in raw_direction))
        if length <= 1e-12:
            raise ValueError("pose pixel gate camera direction is invalid")
        directions[key] = tuple(float(value) / length for value in raw_direction)
    bind_inventory: list[dict[str, object]] = []
    posed_inventory: list[dict[str, object]] = []
    total_pixels = 0
    foreground_pixels = 0
    changed_pixels = 0
    absolute_error = 0
    views: list[dict[str, object]] = []
    for key in bind_keys:
        if not isinstance(key, str) or not key:
            raise ValueError("pose pixel gate image key is invalid")
        bind_path = Path(bind_images[key]).resolve(strict=True)
        posed_path = Path(posed_images[key]).resolve(strict=True)
        try:
            bind_size, bind_raw = _decode_png_rgba8(bind_path)
            posed_size, posed_raw = _decode_png_rgba8(posed_path)
        except (OSError, ValueError, zlib.error) as exc:
            raise ValueError(f"pose pixel gate cannot decode {key}") from exc
        bind_rgba = _visible_rgba(bind_raw)
        posed_rgba = _visible_rgba(posed_raw)
        if bind_size != posed_size or len(bind_rgba) != len(posed_rgba):
            raise ValueError(f"pose pixel gate dimensions differ for {key}")
        if required_size is not None and bind_size != required_size:
            raise ValueError(f"pose pixel gate required dimensions differ for {key}")
        pixels = bind_size[0] * bind_size[1]
        total_pixels += pixels
        if total_pixels > _MAX_TOTAL_PIXELS:
            raise ValueError("pose pixel gate total pixel cap exceeded")
        view_foreground = 0
        view_changed = 0
        view_error = 0
        for offset in range(0, len(bind_rgba), 4):
            left = bind_rgba[offset:offset + 4]
            right = posed_rgba[offset:offset + 4]
            foreground = left[3] >= 128 or right[3] >= 128
            if foreground:
                foreground_pixels += 1
                view_foreground += 1
            silhouette_changed = (left[3] >= 128) != (right[3] >= 128)
            if silhouette_changed:
                changed_pixels += 1
                view_changed += 1
            pixel_error = sum(abs(a - b) for a, b in zip(left, right))
            absolute_error += pixel_error
            view_error += pixel_error
        view_fraction = view_changed / view_foreground if view_foreground else 0.0
        view_mae = view_error / (view_foreground * 4) if view_foreground else 0.0
        views.append({
            "changed_fraction": view_fraction,
            "changed_pixels": view_changed,
            "foreground_pixels": view_foreground,
            "height": bind_size[1],
            "key": key,
            "mean_absolute_error": view_mae,
            "width": bind_size[0],
        })
        bind_inventory.append({
            "height": bind_size[1], "key": key,
            "rgba_sha256": hashlib.sha256(bind_rgba).hexdigest(), "width": bind_size[0],
        })
        posed_inventory.append({
            "height": posed_size[1], "key": key,
            "rgba_sha256": hashlib.sha256(posed_rgba).hexdigest(), "width": posed_size[0],
        })
    changed_fraction = changed_pixels / foreground_pixels if foreground_pixels else 0.0
    mean_absolute_error = absolute_error / (foreground_pixels * 4) if foreground_pixels else 0.0
    if any(view["foreground_pixels"] <= 0 for view in views):
        raise ValueError(
            "selected animation decoded pixel matrix has an empty foreground view"
        )
    qualified = tuple(
        view["key"] for view in views
        if view["changed_pixels"] >= minimum_silhouette_pixels
        and view["changed_fraction"] >= float(minimum_changed_fraction)
    )
    orthogonal = any(
        abs(sum(a * b for a, b in zip(directions[left], directions[right]))) <= 0.25
        for index, left in enumerate(qualified)
        for right in qualified[index + 1:]
    )
    if not orthogonal:
        raise ValueError(
            "selected animation decoded pixels lack qualifying orthogonal silhouette views"
        )
    bind_bundle = _canonical_hash(bind_inventory)
    posed_bundle = _canonical_hash(posed_inventory)
    camera_payload = [
        {"key": key, "direction": list(directions[key])} for key in bind_keys
    ]
    unsigned = {
        "bind_pixel_bundle_sha256": bind_bundle,
        "changed_fraction": changed_fraction,
        "changed_pixels": changed_pixels,
        "foreground_pixels": foreground_pixels,
        "image_count": len(bind_keys),
        "mean_absolute_error": mean_absolute_error,
        "minimum_changed_fraction": float(minimum_changed_fraction),
        "minimum_silhouette_pixels": minimum_silhouette_pixels,
        "camera_directions": camera_payload,
        "qualified_silhouette_views": list(qualified),
        "posed_pixel_bundle_sha256": posed_bundle,
        "total_pixels": total_pixels,
        "views": views,
    }
    return PosePixelGateProof(
        image_count=len(bind_keys), total_pixels=total_pixels,
        foreground_pixels=foreground_pixels,
        changed_pixels=changed_pixels, changed_fraction=changed_fraction,
        mean_absolute_error=mean_absolute_error,
        minimum_changed_fraction=float(minimum_changed_fraction),
        minimum_silhouette_pixels=minimum_silhouette_pixels,
        bind_pixel_bundle_sha256=bind_bundle,
        posed_pixel_bundle_sha256=posed_bundle,
        views=tuple(views),
        camera_directions=tuple(camera_payload),
        qualified_silhouette_views=qualified,
        evidence_sha256=_canonical_hash(unsigned),
    )


def _geometry_extents(
    source_root: Path,
    geometry_paths: tuple[str | Path, ...],
    *,
    expected_bones: Mapping[int, str],
    expected_parents: Mapping[int, int],
) -> tuple[
    dict[int, tuple[tuple[float, float, float], tuple[float, float, float]]],
    str,
    dict[int, tuple[Decimal, ...]],
    tuple[_GeometryVertex, ...],
]:
    if not geometry_paths or len(geometry_paths) > _MAX_GEOMETRY_FILES:
        raise ValueError("exact geometry allowlist count is invalid")
    bounds: dict[int, list[list[float]]] = {}
    inventory: list[dict[str, object]] = []
    geometry_vertices: list[_GeometryVertex] = []
    canonical_bind: dict[int, tuple[Decimal, ...]] | None = None
    selected_files = []
    total_bytes = 0
    seen: set[Path] = set()
    for raw in geometry_paths:
        path, payload, sha256 = _safe_exact_file(source_root, raw)
        if path in seen:
            raise ValueError("duplicate exact geometry allowlist entry")
        seen.add(path)
        total_bytes += len(payload)
        if total_bytes > _MAX_EXACT_TOTAL_BYTES:
            raise ValueError("exact geometry total byte cap exceeded")
        selected_files.append((path, payload, sha256))
    selected_files.sort(key=lambda item: (_relative(item[0], source_root).casefold(), _relative(item[0], source_root)))
    vertices = 0
    if len(set(expected_bones.values())) != len(expected_bones):
        raise ValueError("exact animation bone names are duplicated")
    expected_by_name = {name: bone for bone, name in expected_bones.items()}
    expected_lineage = {
        name: None if expected_parents[bone] == -1 else expected_bones[expected_parents[bone]]
        for bone, name in expected_bones.items()
    }
    for path, payload, sha256 in selected_files:
        relative = _relative(path, source_root)
        try:
            lines = payload.decode("utf-8", errors="strict").splitlines()
        except UnicodeError as exc:
            raise ValueError(f"cannot read exact geometry source: {path}") from exc
        geometry_animation = _parse_animation(path, payload)
        if len(set(geometry_animation.bones.values())) != len(geometry_animation.bones):
            raise ValueError(f"exact geometry bone names are duplicated: {path}")
        geometry_lineage = {
            name: None if geometry_animation.parents[bone] == -1
            else geometry_animation.bones[geometry_animation.parents[bone]]
            for bone, name in geometry_animation.bones.items()
        }
        if geometry_lineage != expected_lineage:
            raise ValueError(f"exact animation/reference/geometry bone lineage differs: {path}")
        if 0 not in geometry_animation.frames or set(geometry_animation.frames[0]) != set(geometry_animation.bones):
            raise ValueError(f"exact geometry bind frame is incomplete: {path}")
        geometry_to_expected = {
            bone: expected_by_name[name] for bone, name in geometry_animation.bones.items()
        }
        bind = {
            geometry_to_expected[bone]: transform
            for bone, transform in geometry_animation.frames[0].items()
        }
        if canonical_bind is None:
            canonical_bind = bind
        elif bind != canonical_bind:
            raise ValueError("exact geometry bind frames differ across allowlist")
        try:
            start = next(index for index, line in enumerate(lines) if line.strip().casefold() == "triangles")
        except StopIteration:
            raise ValueError(f"exact geometry allowlist source has no triangles: {path}")
        inventory.append({"bytes": len(payload), "path": relative, "sha256": sha256})
        merged_vertex_keys: set[
            tuple[tuple[float, float, float], tuple[tuple[int, float], ...]]
        ] = set()
        expecting_material = True
        remaining_vertices = 0
        saw_triangle_end = False
        for line in lines[start + 1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if expecting_material:
                if stripped.casefold() == "end":
                    saw_triangle_end = True
                    break
                expecting_material = False
                remaining_vertices = 3
                continue
            if stripped.casefold() == "end":
                raise ValueError(f"exact geometry triangle is incomplete: {path}")
            tokens = line.split()
            if len(tokens) < 9:
                raise ValueError(f"invalid exact geometry vertex in {path}")
            try:
                parent = int(tokens[0])
                position = tuple(float(tokens[index]) for index in range(1, 4))
            except ValueError as exc:
                raise ValueError(f"invalid exact geometry vertex in {path}") from exc
            if parent not in geometry_to_expected or any(not math.isfinite(value) for value in position):
                raise ValueError(f"non-finite or unknown exact geometry vertex lineage in {path}")
            vertices += 1
            if vertices > _MAX_VERTICES:
                raise ValueError("exact geometry vertex cap exceeded")
            weighted: list[tuple[int, float]] = [(geometry_to_expected[parent], 1.0)]
            if len(tokens) >= 10:
                try:
                    link_count = int(tokens[9])
                    if (
                        not 0 <= link_count <= _MAX_BONES
                        or len(tokens) != 10 + link_count * 2
                    ):
                        raise ValueError
                    if link_count > 0:
                        weighted = []
                    for offset in range(link_count):
                        bone = int(tokens[10 + offset * 2])
                        weight = float(tokens[11 + offset * 2])
                        if (
                            not math.isfinite(weight)
                            or weight < 0.0
                            or bone not in geometry_to_expected
                        ):
                            raise ValueError
                        if weight > 0.0:
                            weighted.append((geometry_to_expected[bone], weight))
                except (ValueError, IndexError):
                    raise ValueError(f"invalid exact geometry vertex weights in {path}")
            weight_sum = sum(weight for _bone, weight in weighted)
            if not weighted or not math.isfinite(weight_sum) or weight_sum <= 0.0:
                raise ValueError(f"invalid exact geometry vertex weights in {path}")
            normalized_weights = tuple(
                (bone, weight / weight_sum) for bone, weight in sorted(weighted)
            )
            remaining_vertices -= 1
            if remaining_vertices == 0:
                expecting_material = True
            merge_key = (position, normalized_weights)
            if merge_key in merged_vertex_keys:
                continue
            merged_vertex_keys.add(merge_key)
            geometry_vertices.append(_GeometryVertex(position, normalized_weights))
            influenced = {bone for bone, _weight in normalized_weights}
            for bone in influenced:
                if bone not in bounds:
                    bounds[bone] = [list(position), list(position)]
                else:
                    for axis, value in enumerate(position):
                        bounds[bone][0][axis] = min(bounds[bone][0][axis], value)
                        bounds[bone][1][axis] = max(bounds[bone][1][axis], value)
        if not saw_triangle_end:
            if not expecting_material:
                raise ValueError(f"exact geometry triangle is incomplete: {path}")
            raise ValueError(f"exact geometry triangle end is missing: {path}")
    if not inventory or not bounds:
        raise ValueError("exact geometry inventory has no weighted mesh vertices")
    frozen_bounds = {
        bone: (tuple(minimum), tuple(maximum))
        for bone, (minimum, maximum) in bounds.items()
    }
    if any(
        not math.isfinite(value)
        for minimum, maximum in frozen_bounds.values()
        for value in (*minimum, *maximum)
    ):
        raise ValueError("non-finite exact geometry extent")
    assert canonical_bind is not None
    return frozen_bounds, _canonical_hash({
        "inventory": inventory,
        "kind": "exact-source-weighted-geometry-allowlist-v3",
    }), canonical_bind, tuple(geometry_vertices)


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


def _local_matrix(values: tuple[Decimal, ...]) -> tuple[tuple[float, ...], ...]:
    w, x, y, z = _quaternion(values)
    tx, ty, tz = (float(item) for item in values[:3])
    matrix = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), tx),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), ty),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), tz),
        (0.0, 0.0, 0.0, 1.0),
    )
    if any(not math.isfinite(item) for row in matrix for item in row):
        raise ValueError("non-finite exact animation matrix")
    return matrix


def _matrix_multiply(
    left: tuple[tuple[float, ...], ...],
    right: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(
        sum(left[row][index] * right[index][column] for index in range(4))
        for column in range(4)
    ) for row in range(4))


def _rigid_inverse(matrix: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
    rotation = tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))
    translation = tuple(matrix[row][3] for row in range(3))
    inverse_translation = tuple(
        -sum(rotation[row][axis] * translation[axis] for axis in range(3))
        for row in range(3)
    )
    return (
        (*rotation[0], inverse_translation[0]),
        (*rotation[1], inverse_translation[1]),
        (*rotation[2], inverse_translation[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _global_matrices(
    transforms: Mapping[int, tuple[Decimal, ...]],
    parents: Mapping[int, int],
) -> dict[int, tuple[tuple[float, ...], ...]]:
    result = {}

    def resolve(bone: int, active: set[int]):
        if bone in result:
            return result[bone]
        if bone in active or bone not in transforms:
            raise ValueError("exact animation global bone lineage differs")
        active.add(bone)
        local = _local_matrix(transforms[bone])
        parent = parents[bone]
        global_matrix = local if parent == -1 else _matrix_multiply(resolve(parent, active), local)
        active.remove(bone)
        result[bone] = global_matrix
        return global_matrix

    for bone in sorted(parents):
        resolve(bone, set())
    return result


def _source_pose_global_matrices(
    transforms: Mapping[int, tuple[Decimal, ...]],
    bind_global: Mapping[int, tuple[tuple[float, ...], ...]],
    parents: Mapping[int, int],
) -> dict[int, tuple[tuple[float, ...], ...]]:
    """Mirror Source Tools: both geometry bind and Action rows are parent-local."""

    result = {}

    def resolve(bone: int, active: set[int]):
        if bone in result:
            return result[bone]
        if bone in active:
            raise ValueError("exact animation parent lineage has a cycle")
        active.add(bone)
        parent = parents[bone]
        if bone in transforms:
            local = _local_matrix(transforms[bone])
        elif parent == -1:
            result[bone] = bind_global[bone]
            active.remove(bone)
            return result[bone]
        else:
            local = _matrix_multiply(_rigid_inverse(bind_global[parent]), bind_global[bone])
        result[bone] = local if parent == -1 else _matrix_multiply(resolve(parent, active), local)
        active.remove(bone)
        return result[bone]

    for bone in sorted(parents):
        resolve(bone, set())
    return result


def _transform_point(
    matrix: tuple[tuple[float, ...], ...],
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[axis][index] * (*point, 1.0)[index] for index in range(4))
        for axis in range(3)
    )


def _regional_skin_displacement(
    animation_values: Mapping[int, tuple[Decimal, ...]],
    bind_values: Mapping[int, tuple[Decimal, ...]],
    parents: Mapping[int, int],
    vertices: tuple[_GeometryVertex, ...],
) -> tuple[float, float]:
    # Source Tools imports REF skeleton rows as parent-local transforms.  The
    # renderer's bind image is the armature REST state, so the raw vertex is
    # the baseline; corrective animations remain provenance/QC only.
    bind_global = _global_matrices(bind_values, parents)
    animation_global = _source_pose_global_matrices(animation_values, bind_global, parents)
    animation_skin = {
        bone: _matrix_multiply(animation_global[bone], _rigid_inverse(bind_global[bone]))
        for bone in bind_values
    }
    squared = []
    for vertex in vertices:
        animated = [0.0, 0.0, 0.0]
        for bone, weight in vertex.weights:
            left = _transform_point(animation_skin[bone], vertex.position)
            for axis in range(3):
                animated[axis] += weight * left[axis]
        distance_squared = sum(
            (animated[axis] - vertex.position[axis]) ** 2 for axis in range(3)
        )
        if not math.isfinite(distance_squared):
            raise ValueError("non-finite exact regional skin displacement")
        squared.append(distance_squared)
    if not squared:
        raise ValueError("exact regional geometry has no skinned vertices")
    return math.sqrt(max(squared)), math.sqrt(sum(squared) / len(squared))


def _bone_displacement(
    values: tuple[Decimal, ...],
    baseline: tuple[Decimal, ...],
    extent: float,
) -> float:
    if not math.isfinite(extent) or extent < 0.0:
        raise ValueError("non-finite exact geometry extent")
    translation = math.dist(
        tuple(float(value) for value in values[:3]),
        tuple(float(value) for value in baseline[:3]),
    )
    left = _quaternion(values)
    right = _quaternion(baseline)
    dot = min(1.0, max(-1.0, abs(sum(a * b for a, b in zip(left, right)))))
    angle = 2.0 * math.acos(dot)
    displacement = translation + extent * 2.0 * math.sin(angle * 0.5)
    if not math.isfinite(displacement):
        raise ValueError("non-finite exact animation displacement")
    return displacement


def _descendant_bones(bone: int, parents: Mapping[int, int]) -> tuple[int, ...]:
    descendants = []
    for candidate in sorted(parents):
        cursor = candidate
        visited = set()
        while cursor != -1 and cursor not in visited:
            if cursor == bone:
                descendants.append(candidate)
                break
            visited.add(cursor)
            cursor = parents.get(cursor, -1)
    return tuple(descendants)


def _lineage_extent(
    bone: int,
    parents: Mapping[int, int],
    bounds: Mapping[int, tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> float | None:
    selected = [bounds[item] for item in _descendant_bones(bone, parents) if item in bounds]
    if not selected:
        return None
    minimum = tuple(min(item[0][axis] for item in selected) for axis in range(3))
    maximum = tuple(max(item[1][axis] for item in selected) for axis in range(3))
    extent = math.dist(minimum, maximum)
    if not math.isfinite(extent):
        raise ValueError("non-finite exact geometry extent")
    return extent


def select_animation_pose(
    animation_root: str | Path,
    *,
    source_root: str | Path,
    animation_pairs: tuple[tuple[str | Path, str | Path], ...],
    geometry_paths: tuple[str | Path, ...],
) -> AnimationPoseSelection | BindOnlyPoseSelection:
    """Choose the exact paired state with the largest bone transform delta."""

    root = Path(animation_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("exact animation root must be a directory")
    geometry_root = Path(source_root).resolve(strict=True)
    if not geometry_root.is_dir():
        raise ValueError("exact geometry source root must be a directory")
    if not animation_pairs or len(animation_pairs) > _MAX_ANIMATION_PAIRS:
        raise ValueError("paired exact animation allowlist count is invalid")
    pairs: list[tuple[Path, bytes, str, Path, bytes, str]] = []
    total_bytes = 0
    seen_pairs = set()
    for animation_raw, reference_raw in animation_pairs:
        try:
            animation_path, animation_bytes, animation_sha = _safe_exact_file(root, animation_raw)
            reference_path, reference_bytes, reference_sha = _safe_exact_file(root, reference_raw)
        except ValueError as exc:
            raise ValueError("paired exact animation allowlist is invalid") from exc
        identity = (animation_path, reference_path)
        if identity in seen_pairs:
            raise ValueError("duplicate paired exact animation allowlist entry")
        seen_pairs.add(identity)
        total_bytes += len(animation_bytes) + len(reference_bytes)
        if total_bytes > _MAX_EXACT_TOTAL_BYTES:
            raise ValueError("paired exact animation total byte cap exceeded")
        pairs.append((
            animation_path, animation_bytes, animation_sha,
            reference_path, reference_bytes, reference_sha,
        ))
    pairs.sort(key=lambda item: (_relative(item[0], root).casefold(), _relative(item[0], root)))

    inventory: list[dict[str, str]] = []
    ranked: list[tuple[float, float, str, str, int, int, int, str, Path, Path]] = []
    pair_hashes: dict[tuple[Path, Path], tuple[str, str]] = {}
    parsed_pairs = []
    canonical_bones = None
    canonical_parents = None
    for animation_path, animation_bytes, animation_sha, reference_path, reference_bytes, reference_sha in pairs:
        pair_hashes[(animation_path, reference_path)] = (animation_sha, reference_sha)
        animation_relative = _relative(animation_path, root)
        reference_relative = _relative(reference_path, root)
        inventory.append({
            "animation_relative_path": animation_relative,
            "animation_sha256": animation_sha,
            "reference_relative_path": reference_relative,
            "reference_sha256": reference_sha,
        })
        animation = _parse_animation(animation_path, animation_bytes)
        reference = _parse_animation(reference_path, reference_bytes)
        if animation.bones != reference.bones or animation.parents != reference.parents:
            raise ValueError("exact animation/reference bone lineage differs")
        if canonical_bones is None:
            canonical_bones = dict(animation.bones)
            canonical_parents = dict(animation.parents)
        elif animation.bones != canonical_bones or animation.parents != canonical_parents:
            raise ValueError("paired exact animation bone lineage differs")
        parsed_pairs.append((
            animation_path, reference_path, animation_relative,
            animation, reference,
        ))
    assert canonical_bones is not None and canonical_parents is not None
    geometry_bounds, geometry_inventory_sha256, geometry_bind, geometry_vertices = _geometry_extents(
        geometry_root, geometry_paths,
        expected_bones=canonical_bones, expected_parents=canonical_parents,
    )
    region_lineage = set()
    for vertex in geometry_vertices:
        for bone, _weight in vertex.weights:
            cursor = bone
            while cursor != -1 and cursor not in region_lineage:
                region_lineage.add(cursor)
                cursor = canonical_parents[cursor]
    for animation_path, reference_path, animation_relative, animation, reference in parsed_pairs:
        if len(reference.frames) != 1:
            raise ValueError("exact corrective animation must contain exactly one reference frame")
        reference_frame = next(iter(reference.frames.values()))
        if not reference_frame:
            raise ValueError("exact corrective animation frame must not be empty")
        for frame_ordinal, (source_time, transforms) in enumerate(animation.frames.items()):
            displacement, rms_displacement = _regional_skin_displacement(
                transforms, geometry_bind,
                animation.parents, geometry_vertices,
            )
            bone_rank = []
            for bone_index in sorted(region_lineage):
                extent = _lineage_extent(bone_index, animation.parents, geometry_bounds)
                if extent is None:
                    continue
                values = transforms.get(bone_index, geometry_bind[bone_index])
                baseline = geometry_bind[bone_index]
                bone_displacement = _bone_displacement(
                    values, baseline, extent,
                )
                bone_rank.append((bone_displacement, bone_index, animation.bones[bone_index]))
            if not bone_rank:
                continue
            bone_rank.sort(key=lambda item: (-item[0], item[1], item[2]))
            _bone_delta, bone_index, bone_name = bone_rank[0]
            ranked.append((
                displacement, rms_displacement,
                animation_relative.casefold(), animation_relative,
                frame_ordinal, source_time, bone_index, bone_name,
                animation_path, reference_path,
            ))
    selector_input_sha256 = _canonical_hash({
        "candidate_inputs_consulted": False,
        "pairs": inventory,
        "geometry_inventory_sha256": geometry_inventory_sha256,
        "selector": "exact-raw-render-region-displacement-v3",
    })
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3], item[4], item[5], item[6]))
    if not ranked or ranked[0][0] <= 1e-12:
        reason = "no-exact-region-influencing-animation-displacement"
        unsigned_bind = {
            "candidate_inputs_consulted": False,
            "geometry_inventory_sha256": geometry_inventory_sha256,
            "pose_keys": ["bind"],
            "reason": reason,
            "selector": "exact-raw-render-region-displacement-v3",
            "selector_input_sha256": selector_input_sha256,
        }
        return BindOnlyPoseSelection(
            selector="exact-raw-render-region-displacement-v3",
            reason=reason,
            geometry_inventory_sha256=geometry_inventory_sha256,
            selector_input_sha256=selector_input_sha256,
            selection_sha256=_canonical_hash(unsigned_bind),
        )
    winner = ranked[0]
    (
        displacement, rms_displacement, _folded, animation_relative, frame, source_time,
        bone_index, bone_name, animation_path, reference_path,
    ) = winner
    animation_sha, reference_sha = pair_hashes[(animation_path, reference_path)]
    displacement_squared = format(displacement * displacement, ".17g")
    unsigned = {
        "animation_relative_path": animation_relative,
        "animation_sha256": animation_sha,
        "baseline": "geometry-rest",
        "bone_index": bone_index,
        "bone_name": bone_name,
        "candidate_inputs_consulted": False,
        "corrective_used_as_baseline": False,
        "displacement": displacement,
        "displacement_squared": displacement_squared,
        "frame": frame,
        "geometry_inventory_sha256": geometry_inventory_sha256,
        "pose_name": Path(animation_relative).stem.casefold(),
        "reference_relative_path": _relative(reference_path, root),
        "reference_sha256": reference_sha,
        "raw_render_max_displacement": displacement,
        "raw_render_rms_displacement": rms_displacement,
        "rms_displacement": rms_displacement,
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": selector_input_sha256,
        "source_time": source_time,
    }
    return AnimationPoseSelection(
        selector="exact-raw-render-region-displacement-v3",
        animation_relative_path=animation_relative,
        reference_relative_path=_relative(reference_path, root),
        animation_sha256=animation_sha,
        reference_sha256=reference_sha,
        frame=frame,
        source_time=source_time,
        bone_index=bone_index,
        bone_name=bone_name,
        displacement=displacement,
        rms_displacement=rms_displacement,
        displacement_squared=displacement_squared,
        geometry_inventory_sha256=geometry_inventory_sha256,
        selector_input_sha256=selector_input_sha256,
        selection_sha256=_canonical_hash(unsigned),
    )
