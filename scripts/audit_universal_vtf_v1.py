#!/usr/bin/env python3
"""Independent, standard-library auditor for the frozen Universal VTF V1 policy.

This program deliberately does not load the optimizer assembly and does not trust
metrics or physical facts recorded by the producer.  It reparses every VTF and
recomputes the frozen receipts from bytes on disk.
"""

from __future__ import annotations

import argparse
import cmath
import hashlib
import json
import math
import os
import stat
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "universal-vtf-manifest-v1"
FIXTURE_SCHEMA = "universal-vtf-v1-fixtures"
FIXTURE_SHA256 = "9adb529e565f9e1ba54f0833af21c59db8674b5bed286d9cac37c6f8ace13d7b"
BRIDGE_SCHEMA = "bc3-raw-to-approved-v1"
CAPS_SCHEMA = "universal-vtf-runtime-caps-v1"
FROZEN_RAW = {
    "file_count": 3827,
    "total_bytes": 1_441_918_651,
    "tree_sha256": "5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758",
}
FROZEN_SAFE = {
    "file_count": 3664,
    "total_bytes": 438_230_044,
    "tree_sha256": "8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af",
}
FROZEN_VTF_COUNT = 1152
FROZEN_BC_ROWS = 193
FROZEN_BC_PARTITION = (155, 32, 6, 0)
EXPECTED_PRIOR_MANIFEST_SHA256 = (
    "6eda827ee1fb1bddb85a1aabeea1a0181722947139bc4d58776a529c15d14b15"
)

_SHA_HEX = set("0123456789abcdefABCDEF")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_RESOURCE_NO_DATA = 0x02
_LOW_RES_TAG = 0x000001
_HIGH_RES_TAG = 0x000030
_ENVMAP_FLAG = 0x00004000
_EPSILON = 1e-12


class AuditError(RuntimeError):
    """A fail-closed audit finding."""


def _fail(message: str) -> None:
    raise AuditError(message)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        _fail(f"file_changed_during_hash:{path}")
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _canonical_relative(value: str, *, context: str = "path") -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"invalid_{context}:{value!r}")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        _fail(f"rooted_{context}:{value}")
    parts = value.split("/")
    if any(not part or part in (".", "..") for part in parts):
        _fail(f"traversal_{context}:{value}")
    return "/".join(parts)


def _contained(root: Path, relative: str) -> Path:
    relative = _canonical_relative(relative)
    root = root.resolve()
    result = (root / Path(*relative.split("/"))).resolve()
    try:
        result.relative_to(root)
    except ValueError:
        _fail(f"path_escape:{relative}")
    return result


def tree_inventory(root: os.PathLike[str] | str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        _fail(f"tree_root_missing:{root_path}")
    if _is_reparse(root_path):
        _fail(f"reparse_point:root:{root_path}")

    physical: list[tuple[str, Path]] = []
    folded: dict[str, str] = {}
    for directory, directories, files in os.walk(root_path, followlinks=False):
        directory_path = Path(directory)
        for name in list(directories):
            child = directory_path / name
            if _is_reparse(child):
                _fail(f"reparse_point:{child}")
        for name in files:
            path = directory_path / name
            if _is_reparse(path):
                _fail(f"reparse_point:{path}")
            relative = path.relative_to(root_path).as_posix()
            _canonical_relative(relative)
            prior = folded.setdefault(relative.casefold(), relative)
            if prior != relative:
                _fail(f"casefold_path_collision:{prior}:{relative}")
            physical.append((relative, path))

    physical.sort(key=lambda item: (item[0].lower(), item[0]))
    tree = hashlib.sha256()
    total = 0
    rows: list[dict[str, Any]] = []
    for relative, path in physical:
        before = path.stat()
        digest = _sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            _fail(f"file_changed_during_capture:{relative}")
        encoded = relative.encode("utf-8")
        tree.update(struct.pack("<I", len(encoded)))
        tree.update(encoded)
        tree.update(struct.pack("<Q", before.st_size))
        tree.update(bytes.fromhex(digest))
        total += before.st_size
        rows.append({"relative_path": relative, "bytes": before.st_size, "sha256": digest})
    return {
        "root": str(root_path),
        "file_count": len(rows),
        "total_bytes": total,
        "tree_sha256": tree.hexdigest(),
        "files": rows,
    }


def _read_i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _image_bytes(fmt: int, width: int, height: int) -> int:
    if width <= 0 or height <= 0 or fmt < 0:
        return 0
    if fmt in (13, 20):
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8
    if fmt in (14, 15):
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16
    if fmt in (0, 1, 11, 12, 16, 23, 26):
        return width * height * 4
    if fmt in (2, 3, 9, 10):
        return width * height * 3
    if fmt in (4, 6, 17, 18, 19, 21, 22):
        return width * height * 2
    if fmt in (5, 7, 8):
        return width * height
    if fmt in (24, 25):
        return width * height * 8
    _fail(f"unsupported_vtf_format:{fmt}")
    raise AssertionError


def parse_vtf(path: os.PathLike[str] | str) -> dict[str, Any]:
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as error:
        _fail(f"vtf_missing_or_unreadable:{file_path}:{error}")
    if len(data) < 63 or data[:4] != b"VTF\0":
        _fail(f"invalid_vtf_signature_or_header:{file_path}")
    major, minor = _read_i32(data, 4), _read_i32(data, 8)
    if major != 7 or not 0 <= minor <= 5:
        _fail(f"unsupported_vtf_version:{major}.{minor}:{file_path}")
    header_size = _read_i32(data, 12)
    width, height = _read_u16(data, 16), _read_u16(data, 18)
    flags = _read_i32(data, 20)
    frames, first_frame = _read_u16(data, 24), _read_u16(data, 26)
    high_format, mip_count = _read_i32(data, 52), data[56]
    low_format = _read_i32(data, 57)
    low_width, low_height = data[61], data[62]
    position = 65 if minor >= 2 else 63
    if len(data) < position:
        _fail(f"truncated_vtf_depth:{file_path}")
    depth = _read_u16(data, 63) if minor >= 2 else 1
    if header_size < position or header_size > len(data):
        _fail(f"invalid_vtf_header_size:{file_path}")
    if min(width, height, frames, depth, mip_count) <= 0:
        _fail(f"invalid_vtf_dimensions:{file_path}")

    resources: list[dict[str, Any]] = []
    if minor >= 3:
        if header_size < 80 or len(data) < 80:
            _fail(f"truncated_vtf_resource_header:{file_path}")
        resource_count = _read_i32(data, 68)
        if resource_count < 0 or resource_count > 4096 or 80 + resource_count * 8 > header_size:
            _fail(f"invalid_vtf_resource_dictionary:{file_path}")
        for index in range(resource_count):
            offset = 80 + index * 8
            tag = data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16
            resource_flags = data[offset + 3]
            value = _read_u32(data, offset + 4)
            resources.append({"tag": tag, "flags": resource_flags, "data": value})

    low_length = _image_bytes(low_format, low_width, low_height)
    low_offset = header_size
    high_offset = header_size + low_length
    for resource in resources:
        if resource["tag"] == _LOW_RES_TAG:
            if resource["flags"] & _RESOURCE_NO_DATA:
                _fail(f"inline_low_resolution_resource:{file_path}")
            low_offset = resource["data"]
        if resource["tag"] == _HIGH_RES_TAG:
            if resource["flags"] & _RESOURCE_NO_DATA:
                _fail(f"inline_high_resolution_resource:{file_path}")
            high_offset = resource["data"]
    for tag in (_LOW_RES_TAG, _HIGH_RES_TAG):
        if sum(resource["tag"] == tag for resource in resources) > 1:
            _fail(f"duplicate_image_resource:{tag:06x}:{file_path}")
    if high_offset < header_size or high_offset > len(data):
        _fail(f"invalid_high_resolution_offset:{file_path}")
    if low_length and (low_offset < header_size or low_offset + low_length > len(data)):
        _fail(f"invalid_low_resolution_range:{file_path}")

    faces = 1
    if flags & _ENVMAP_FLAG:
        faces = 7 if minor < 5 and first_frame != 0xFFFF else 6
    levels: list[dict[str, Any]] = []
    for level in range(mip_count):
        w, h, d = max(1, width >> level), max(1, height >> level), max(1, depth >> level)
        plane_bytes = _image_bytes(high_format, w, h)
        byte_count = plane_bytes * frames * faces * d
        levels.append(
            {
                "level": level,
                "width": w,
                "height": h,
                "depth": d,
                "plane_bytes": plane_bytes,
                "byte_count": byte_count,
            }
        )
    cursor = high_offset
    for level in reversed(levels):
        level["offset"] = cursor
        cursor += level["byte_count"]
    if cursor > len(data):
        _fail(f"truncated_high_resolution_data:{file_path}:{cursor-len(data)}")

    subresources: list[dict[str, Any]] = []
    for level in levels:
        offset = level["offset"]
        for frame in range(frames):
            for face in range(faces):
                for depth_slice in range(level["depth"]):
                    end = offset + level["plane_bytes"]
                    if end > len(data):
                        _fail(f"subresource_out_of_range:{file_path}:{level['level']}")
                    subresources.append(
                        {
                            "level": level["level"],
                            "frame": frame,
                            "face": face,
                            "depth_slice": depth_slice,
                            "offset": offset,
                            "length": level["plane_bytes"],
                            "sha256": _sha256_bytes(data[offset:end]),
                        }
                    )
                    offset = end
        if offset != level["offset"] + level["byte_count"]:
            _fail(f"subresource_partition_mismatch:{file_path}:{level['level']}")

    hrs_length = sum(level["byte_count"] for level in levels)
    if high_offset + hrs_length != cursor:
        _fail(f"high_resolution_partition_mismatch:{file_path}")
    occupied: list[tuple[int, int, str]] = [(0, header_size, "header")]
    if low_length:
        occupied.append((low_offset, low_offset + low_length, "low_resolution"))
    occupied.append((high_offset, high_offset + hrs_length, "high_resolution"))
    payload_offsets = sorted(
        {resource["data"] for resource in resources if not resource["flags"] & _RESOURCE_NO_DATA}
        | {len(data)}
    )
    for resource in resources:
        if resource["flags"] & _RESOURCE_NO_DATA or resource["tag"] in (_LOW_RES_TAG, _HIGH_RES_TAG):
            continue
        start = resource["data"]
        if start < header_size or start >= len(data):
            _fail(f"invalid_resource_payload_offset:{resource['tag']:06x}:{file_path}")
        end = next((boundary for boundary in payload_offsets if boundary > start), 0)
        if end <= start:
            _fail(f"unbounded_resource_payload:{resource['tag']:06x}:{file_path}")
        occupied.append((start, end, f"resource_{resource['tag']:06x}"))
    cursor_check = 0
    for start, end, name in sorted(occupied):
        if start < cursor_check or end <= start or end > len(data):
            _fail(f"overlapping_or_invalid_vtf_range:{name}:{file_path}")
        cursor_check = end
    return {
        "path": str(file_path),
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
        "major": major,
        "minor": minor,
        "header_size": header_size,
        "width": width,
        "height": height,
        "depth": depth,
        "frames": frames,
        "faces": faces,
        "flags": flags,
        "first_frame": first_frame,
        "format": high_format,
        "mip_count": mip_count,
        "low_format": low_format,
        "low_width": low_width,
        "low_height": low_height,
        "low_offset": low_offset,
        "low_length": low_length,
        "high_offset": high_offset,
        "high_length": hrs_length,
        "high_payload": data[high_offset : high_offset + hrs_length],
        "resources": resources,
        "levels": levels,
        "subresources": subresources,
    }


def _rgb565(value: int) -> tuple[int, int, int]:
    red = (value >> 11) & 31
    green = (value >> 5) & 63
    blue = value & 31
    return ((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2))


def _bc_four_colour_palette(c0: int, c1: int) -> tuple[tuple[int, int, int], ...]:
    p0, p1 = _rgb565(c0), _rgb565(c1)
    return (
        p0,
        p1,
        tuple((2 * p0[channel] + p1[channel]) // 3 for channel in range(3)),
        tuple((p0[channel] + 2 * p1[channel]) // 3 for channel in range(3)),
    )


def decode_bc3_rgb(block: bytes) -> tuple[tuple[int, int, int], ...]:
    if len(block) != 16:
        _fail("bc3_block_length")
    c0, c1, indices = struct.unpack_from("<HHI", block, 8)
    palette = _bc_four_colour_palette(c0, c1)
    return tuple(palette[(indices >> (2 * pixel)) & 3] for pixel in range(16))


def decode_bc1_rgb(block: bytes) -> tuple[tuple[int, int, int], ...]:
    if len(block) != 8:
        _fail("bc1_block_length")
    c0, c1, indices = struct.unpack("<HHI", block)
    if c0 <= c1:
        _fail("bc1_not_opaque_four_colour")
    palette = _bc_four_colour_palette(c0, c1)
    return tuple(palette[(indices >> (2 * pixel)) & 3] for pixel in range(16))


def transcode_bc3_block(block: bytes) -> bytes:
    if len(block) != 16:
        _fail("bc3_block_length")
    c0, c1, indices = struct.unpack_from("<HHI", block, 8)
    if c0 > c1:
        result = block[8:]
    elif c0 < c1:
        result = struct.pack("<HHI", c1, c0, indices ^ 0x55555555)
    elif c0 == 0:
        result = struct.pack("<HHI", 1, 0, 0x55555555)
    else:
        result = struct.pack("<HHI", c0, c0 - 1, 0)
    if decode_bc3_rgb(block) != decode_bc1_rgb(result):
        _fail("bc3_to_bc1_rgb_mismatch")
    return result


def transcode_bc3_payload(payload: bytes) -> bytes:
    if not payload or len(payload) % 16:
        _fail("bc3_payload_not_whole_blocks")
    return b"".join(transcode_bc3_block(payload[offset : offset + 16]) for offset in range(0, len(payload), 16))


def _alpha_receipt(payload: bytes, fmt: int) -> dict[str, Any]:
    counts = [0] * 256
    if fmt == 13:
        if not payload or len(payload) % 8:
            _fail("invalid_dxt1_alpha_receipt_payload")
        counts[255] = len(payload) // 8 * 16
    elif fmt == 15:
        if not payload or len(payload) % 16:
            _fail("invalid_dxt5_alpha_receipt_payload")
        for offset in range(0, len(payload), 16):
            block = payload[offset : offset + 16]
            palette = [block[0], block[1], 0, 0, 0, 0, 0, 0]
            if palette[0] > palette[1]:
                for index in range(1, 7):
                    palette[index + 1] = ((7 - index) * palette[0] + index * palette[1]) // 7
            else:
                for index in range(1, 5):
                    palette[index + 1] = ((5 - index) * palette[0] + index * palette[1]) // 5
                palette[6], palette[7] = 0, 255
            indices = int.from_bytes(block[2:8], "little")
            for pixel in range(16):
                counts[palette[(indices >> (pixel * 3)) & 7]] += 1
    else:
        _fail(f"unsupported_alpha_receipt_format:{fmt}")
    populated = [index for index, count in enumerate(counts) if count]
    return {
        "decodedSamples": sum(counts),
        "minimum": populated[0],
        "maximum": populated[-1],
        "uniqueValues": len(populated),
        "fullyOpaque": populated[0] == 255 and populated[-1] == 255,
    }


def reflect101(index: int, length: int) -> int:
    if length <= 0:
        raise ValueError("length")
    if length == 1:
        return 0
    period = 2 * length - 2
    folded = index % period
    return folded if folded < length else period - folded


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values or not 0 <= percentile <= 1:
        raise ValueError("nearest_rank")
    if any(math.isnan(value) for value in values):
        return math.nan
    ordered = sorted(values)
    index = min(max(math.ceil(percentile * len(ordered)) - 1, 0), len(ordered) - 1)
    return ordered[index]


def retention(source: float, candidate: float) -> float:
    if any(math.isnan(value) or value < 0 for value in (source, candidate)):
        return math.nan
    if abs(source) <= _EPSILON:
        return 1.0 if abs(candidate) <= _EPSILON else math.nan
    return candidate / source


def psnr(reference: Sequence[float], candidate: Sequence[float], peak: float = 255.0) -> float:
    if len(reference) != len(candidate) or not reference:
        raise ValueError("psnr")
    mse = sum((a - b) ** 2 for a, b in zip(reference, candidate)) / len(reference)
    return math.inf if mse <= _EPSILON else 10 * math.log10(peak * peak / mse)


def ssim(reference: Sequence[float], candidate: Sequence[float], width: int, height: int) -> float:
    if len(reference) != width * height or len(candidate) != len(reference):
        raise ValueError("ssim")
    radius, sigma = 5, 1.5
    kernel = [math.exp(-(offset * offset) / (2 * sigma * sigma)) for offset in range(-radius, radius + 1)]
    kernel_sum = sum(kernel)
    kernel = [value / kernel_sum for value in kernel]
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    total = 0.0
    for y in range(height):
        for x in range(width):
            mean_a = mean_b = 0.0
            for ky in range(-radius, radius + 1):
                sy = reflect101(y + ky, height)
                wy = kernel[ky + radius]
                for kx in range(-radius, radius + 1):
                    sx = reflect101(x + kx, width)
                    weight = wy * kernel[kx + radius]
                    sample = sy * width + sx
                    mean_a += reference[sample] * weight
                    mean_b += candidate[sample] * weight
            var_a = var_b = covariance = 0.0
            for ky in range(-radius, radius + 1):
                sy = reflect101(y + ky, height)
                wy = kernel[ky + radius]
                for kx in range(-radius, radius + 1):
                    sx = reflect101(x + kx, width)
                    weight = wy * kernel[kx + radius]
                    sample = sy * width + sx
                    da, db = reference[sample] - mean_a, candidate[sample] - mean_b
                    var_a += da * da * weight
                    var_b += db * db * weight
                    covariance += da * db * weight
            numerator = (2 * mean_a * mean_b + c1) * (2 * covariance + c2)
            denominator = (mean_a * mean_a + mean_b * mean_b + c1) * (var_a + var_b + c2)
            total += 1.0 if denominator <= _EPSILON else numerator / denominator
    return max(-1.0, min(1.0, total / (width * height)))


def edge_energy(values: Sequence[float], width: int, height: int) -> float:
    horizontal = [abs(values[y * width + x + 1] - values[y * width + x]) for y in range(height) for x in range(width - 1)]
    vertical = [abs(values[(y + 1) * width + x] - values[y * width + x]) for y in range(height - 1) for x in range(width)]
    return (sum(horizontal) / len(horizontal) if horizontal else 0.0) + (sum(vertical) / len(vertical) if vertical else 0.0)


def is_cutout_like(alpha: Sequence[int]) -> bool:
    if not alpha:
        return False
    low = sum(value <= 0.1 * 255 for value in alpha) / len(alpha)
    high = sum(value >= 0.9 * 255 for value in alpha) / len(alpha)
    return low >= 0.01 and high >= 0.01 and low + high >= 0.95


def _to_byte(value: float) -> int:
    # Python round is IEEE round-to-nearest-even, matching MidpointRounding.ToEven.
    return max(0, min(255, round(value * 255.0)))


def coverage(alpha: Sequence[int], threshold: float, scale: float = 1.0) -> float:
    threshold_byte = _to_byte(threshold)
    return sum(_to_byte(min(1.0, value / 255.0 * scale)) >= threshold_byte for value in alpha) / len(alpha)


def find_coverage_scale(alpha: Sequence[int], threshold: float, target: float) -> tuple[float, float, list[float]]:
    candidates = [0.0, 1.0, 8.0]
    low, high = 0.0, 8.0
    for _ in range(32):
        scale = (low + high) * 0.5
        candidates.append(scale)
        if coverage(alpha, threshold, scale) < target:
            low = scale
        else:
            high = scale

    def key(scale: float) -> tuple[float, float, float]:
        achieved = coverage(alpha, threshold, scale)
        distance = math.inf if scale <= 0 else abs(math.log2(scale))
        return abs(achieved - target), distance, scale

    best = min(candidates, key=key)
    return best, coverage(alpha, threshold, best), candidates


def iou(first: Sequence[bool], second: Sequence[bool]) -> float:
    intersection = sum(a and b for a, b in zip(first, second))
    union = sum(a or b for a, b in zip(first, second))
    return 1.0 if union == 0 else intersection / union


def hausdorff(first: Sequence[bool], second: Sequence[bool], width: int) -> float:
    a = [(index % width, index // width) for index, value in enumerate(first) if value]
    b = [(index % width, index // width) for index, value in enumerate(second) if value]
    if not a and not b:
        return 0.0
    if not a or not b:
        return math.inf

    def directed(source: Sequence[tuple[int, int]], target: Sequence[tuple[int, int]]) -> float:
        return max(min(math.hypot(x - tx, y - ty) for tx, ty in target) for x, y in source)

    return max(directed(a, b), directed(b, a))


def angular_error(first: Sequence[float], second: Sequence[float]) -> float:
    length_a = math.sqrt(sum(value * value for value in first))
    length_b = math.sqrt(sum(value * value for value in second))
    if length_a <= _EPSILON or length_b <= _EPSILON:
        return math.nan
    cosine = sum(a * b for a, b in zip(first, second)) / (length_a * length_b)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def margin(value: float, threshold: float, minimum: bool) -> float:
    if math.isnan(value) or math.isnan(threshold):
        return -math.inf
    denominator = max(abs(threshold), _EPSILON)
    return (value - threshold) / denominator if minimum else (threshold - value) / denominator


def ciede2000(first: Sequence[float], second: Sequence[float]) -> float:
    l1, a1, b1 = first
    l2, a2, b2 = second
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    mean_c = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(mean_c**7 / (mean_c**7 + 25**7)))
    ap1, ap2 = (1 + g) * a1, (1 + g) * a2
    cp1, cp2 = math.hypot(ap1, b1), math.hypot(ap2, b2)

    def hue(b: float, a: float) -> float:
        result = math.degrees(math.atan2(b, a))
        return result + 360 if result < 0 else result

    hp1, hp2 = hue(b1, ap1), hue(b2, ap2)
    dl, dc = l2 - l1, cp2 - cp1
    if cp1 * cp2 <= _EPSILON:
        dh_degrees = 0.0
    else:
        difference = hp2 - hp1
        dh_degrees = difference if abs(difference) <= 180 else difference - 360 if difference > 180 else difference + 360
    dh = 2 * math.sqrt(cp1 * cp2) * math.sin(math.radians(dh_degrees / 2))
    mean_l, mean_cp = (l1 + l2) / 2, (cp1 + cp2) / 2
    if cp1 * cp2 <= _EPSILON:
        mean_h = hp1 + hp2
    elif abs(hp1 - hp2) <= 180:
        mean_h = (hp1 + hp2) / 2
    elif hp1 + hp2 < 360:
        mean_h = (hp1 + hp2 + 360) / 2
    else:
        mean_h = (hp1 + hp2 - 360) / 2
    t = 1 - 0.17 * math.cos(math.radians(mean_h - 30)) + 0.24 * math.cos(math.radians(2 * mean_h)) + 0.32 * math.cos(math.radians(3 * mean_h + 6)) - 0.20 * math.cos(math.radians(4 * mean_h - 63))
    delta_theta = 30 * math.exp(-((mean_h - 275) / 25) ** 2)
    rc = 2 * math.sqrt(mean_cp**7 / (mean_cp**7 + 25**7))
    sl = 1 + 0.015 * (mean_l - 50) ** 2 / math.sqrt(20 + (mean_l - 50) ** 2)
    sc = 1 + 0.045 * mean_cp
    sh = 1 + 0.015 * mean_cp * t
    rt = -math.sin(math.radians(2 * delta_theta)) * rc
    lv, cv, hv = dl / sl, dc / sc, dh / sh
    return math.sqrt(lv * lv + cv * cv + hv * hv + rt * cv * hv)


def dft(values: Sequence[complex]) -> list[complex]:
    length = len(values)
    return [sum(values[n] * cmath.exp(-2j * math.pi * k * n / length) for n in range(length)) for k in range(length)]


def _expect(actual: Any, expected: Any, label: str, errors: list[float]) -> None:
    if isinstance(expected, str) and expected == "Infinity":
        if not isinstance(actual, (float, int)) or not math.isinf(actual) or actual < 0:
            _fail(f"fixture_mismatch:{label}:{actual}!={expected}")
        errors.append(0.0)
        return
    if isinstance(expected, bool) or isinstance(expected, str):
        if actual != expected:
            _fail(f"fixture_mismatch:{label}:{actual!r}!={expected!r}")
        errors.append(0.0)
        return
    error = abs(float(actual) - float(expected))
    if not math.isfinite(error) or error > 1e-12:
        _fail(f"fixture_mismatch:{label}:{actual!r}!={expected!r}:error={error}")
    errors.append(error)


def audit_fixtures(path: os.PathLike[str] | str) -> dict[str, Any]:
    fixture_path = Path(path)
    raw = fixture_path.read_bytes()
    if _sha256_bytes(raw) != FIXTURE_SHA256:
        _fail(f"fixture_sha256:{_sha256_bytes(raw)}!={FIXTURE_SHA256}")
    try:
        document = json.loads(raw, parse_constant=lambda value: _fail(f"fixture_nonfinite:{value}"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        _fail(f"fixture_invalid_json:{error}")
    if document.get("schema") != FIXTURE_SCHEMA or document.get("version") != 1:
        _fail("fixture_schema_or_version")
    cases = document["cases"]
    errors: list[float] = []

    identity = cases["identityRgba"]
    pixel = identity["source"]["rgbaPixel"]
    repeat = identity["source"]["repeat"]
    channels = [[pixel[channel]] * repeat for channel in range(4)]
    expected = identity["expected"]
    rgb_error = [value for channel in channels[:3] for value in channel]
    _expect(psnr(rgb_error, rgb_error), expected["rgbPsnr"], "identity.rgbPsnr", errors)
    for channel, expected_ssim in enumerate(expected["rgbSsim"]):
        _expect(ssim(channels[channel], channels[channel], 16, 16), expected_ssim, f"identity.ssim{channel}", errors)
    _expect(retention(edge_energy(channels[0], 16, 16), edge_energy(channels[0], 16, 16)), expected["edgeRetention"], "identity.edge", errors)
    _expect(ssim(channels[3], channels[3], 16, 16), expected["alphaSsim"], "identity.alphaSsim", errors)
    _expect(sum(abs(a - b) for a, b in zip(channels[3], channels[3])) / repeat / 255, expected["alphaMae"], "identity.alphaMae", errors)
    _expect(nearest_rank([abs(a - b) / 255 for a, b in zip(channels[3], channels[3])], 0.99), expected["alphaP99"], "identity.alphaP99", errors)

    half = cases["halfPlaneRgb"]
    source = half["sourceLumaRow"] * half["repeatRows"]
    candidate = half["candidateLumaRow"] * half["repeatRows"]
    _expect(psnr(source * 3, candidate * 3), half["expected"]["rgbPsnr"], "half.psnr", errors)
    _expect(retention(edge_energy(source, 16, 16), edge_energy(candidate, 16, 16)), half["expected"]["edgeRetention"], "half.edge", errors)

    alpha = cases["alphaClassification"]
    _expect(is_cutout_like(alpha["cutoutLike"]["alpha"]), alpha["cutoutLike"]["expected"], "alpha.cutout", errors)
    _expect(is_cutout_like(alpha["continuous"]["alpha"]), alpha["continuous"]["expected"], "alpha.continuous", errors)
    equal = alpha["equalHalfPlanes"]
    source_mask = [value >= _to_byte(equal["threshold"]) for value in equal["source"]]
    candidate_mask = [value >= _to_byte(equal["threshold"]) for value in equal["candidate"]]
    _expect(abs(sum(source_mask) / len(source_mask) - sum(candidate_mask) / len(candidate_mask)), equal["expectedCoverageDrift"], "alpha.coverage", errors)
    _expect(iou(source_mask, candidate_mask), equal["expectedIou"], "alpha.iou", errors)
    _expect(hausdorff(source_mask, candidate_mask, len(source_mask)), equal["expectedHausdorff"], "alpha.hausdorff", errors)

    for index, pair in enumerate(cases["normalAngles"]["pairs"]):
        _expect(angular_error(pair["source"], pair["candidate"]), pair["expectedDegrees"], f"normal.{index}", errors)
    for name, case in cases["normalLayouts"].items():
        route = "SsbumpScalars" if name == "ssbump" else "Dxt5NmAg" if name == "dxt5nmAg" else "RgbNormal"
        _expect(route, case["expectedRoute"], f"layout.{name}", errors)
        for key in ("vectorChannels", "auxiliaryChannels", "maskChannels", "scalarChannels"):
            if key in case:
                _expect(len(set(case[key])), len(case[key]), f"layout.{name}.{key}.unique", errors)
                _expect(all(0 <= channel <= 3 for channel in case[key]), True, f"layout.{name}.{key}.range", errors)

    rank = cases["nearestRank"]
    _expect(nearest_rank(rank["ascending"], 0.95), rank["expectedP95"], "rank.p95", errors)
    _expect(nearest_rank(rank["ascending"], 0.99), rank["expectedP99"], "rank.p99", errors)
    reflection = cases["reflect101"]
    actual_reflect = [reflect101(index, len(reflection["source"])) for index in reflection["indices"]]
    for index, (actual, expected_value) in enumerate(zip(actual_reflect, reflection["expected"])):
        _expect(actual, expected_value, f"reflect.{index}", errors)

    zero = cases["retentionZero"]
    _expect(retention(zero["bothZero"]["source"], zero["bothZero"]["candidate"]), zero["bothZero"]["expected"], "retention.zero", errors)
    _expect(math.isnan(retention(zero["sourceZeroCandidateNonzero"]["source"], zero["sourceZeroCandidateNonzero"]["candidate"])), True, "retention.fail", errors)
    tie = cases["routeTie"]
    _expect(margin(tie["minimumGate"]["value"], tie["minimumGate"]["threshold"], True), tie["minimumGate"]["expectedMargin"], "margin.min", errors)
    _expect(margin(tie["maximumGate"]["value"], tie["maximumGate"]["threshold"], False), tie["maximumGate"]["expectedMargin"], "margin.max", errors)
    _expect("SrgbColor", tie["srgbFlagWinner"], "route.srgb", errors)
    _expect("NumericLinear", tie["noSrgbFlagWinner"], "route.linear", errors)

    scale_case = cases["coverageScale32"]
    best, achieved, candidates = find_coverage_scale(scale_case["filteredAlpha"], scale_case["threshold"], scale_case["targetCoverage"])
    for index, expected_value in enumerate(scale_case["seedCandidates"] + scale_case["bisectionCandidates"]):
        _expect(candidates[index], expected_value, f"coverage.candidate{index}", errors)
    _expect(best, scale_case["expectedQuantizedWinningScale"], "coverage.best", errors)
    _expect(achieved, scale_case["expectedWinningCoverage"], "coverage.achieved", errors)

    temporal = cases["temporalFrames"]
    source_diff = [b - a for a, b in zip(*temporal["sourceFrames"])]
    candidate_diff = [b - a for a, b in zip(*temporal["candidateFrames"])]
    _expect(psnr(source_diff, candidate_diff, temporal["signedDifferencePeak"]), temporal["expectedTemporalPsnr"], "temporal", errors)
    cube = cases["cubemapSeam"]
    def seam(a: Sequence[Sequence[int]], b: Sequence[Sequence[int]]) -> float:
        flat = [(x, y) for left, right in zip(a, b) for x, y in zip(left, right)]
        return sum(abs(x - y) / 255 for x, y in flat) / len(flat)
    source_seam = seam(cube["sourceFaceABorders"], cube["sourceFaceBBorders"])
    candidate_seam = seam(cube["candidateFaceABorders"], cube["candidateFaceBBorders"])
    _expect(source_seam, cube["expectedSourceSeam"], "cube.source", errors)
    _expect(candidate_seam, cube["expectedCandidateSeam"], "cube.candidate", errors)
    _expect(candidate_seam - source_seam, cube["expectedIncrease"], "cube.increase", errors)

    # Independent mathematical sentinels not sourced from C# output.
    _expect(ciede2000((50, 2.6772, -79.7751), (50, 0, -82.7485)), 2.0424596801565738, "ciede2000.published", errors)
    spectrum = dft([complex(value) for value in (0, 1, 0, -1)])
    _expect(sum(abs(value) ** 2 for value in spectrum) / 4, 2.0, "dft.parseval", errors)
    return {
        "schema": "universal-vtf-v1-fixture-audit-v1",
        "status": "CLEAN",
        "fixture_sha256": FIXTURE_SHA256,
        "checks": len(errors),
        "maximum_error": max(errors, default=0.0),
    }


def _load_json(source: os.PathLike[str] | str | dict[str, Any], context: str) -> tuple[dict[str, Any], Path | None]:
    if isinstance(source, dict):
        document = source
        path = None
    else:
        path = Path(source).resolve()
        try:
            document = json.loads(path.read_bytes(), parse_constant=lambda value: _fail(f"{context}_nonfinite:{value}"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            _fail(f"{context}_invalid:{error}")
    if not isinstance(document, dict):
        _fail(f"{context}_not_object")
    _reject_nan(document, context)
    return document, path


def _reject_nan(value: Any, context: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{context}_nonfinite_number")
    if isinstance(value, str) and value == "NaN":
        _fail(f"{context}_nan_string")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nan(child, context)
    elif isinstance(value, list):
        for child in value:
            _reject_nan(child, context)


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _SHA_HEX for character in value):
        _fail(f"invalid_sha256:{label}")
    return value.lower()


def _tree_matches(actual: dict[str, Any], expected: dict[str, Any], label: str) -> None:
    mapping = (("file_count", "fileCount"), ("total_bytes", "bytes"), ("tree_sha256", "treeSha256"))
    for actual_key, expected_key in mapping:
        expected_value = expected.get(expected_key, expected.get(actual_key))
        if actual[actual_key] != expected_value:
            _fail(f"{label}_tree_{actual_key}:{actual[actual_key]}!={expected_value}")


def _files_by_path(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["relative_path"]: row for row in inventory["files"]}


def _find_frozen_authorities(candidate: Path) -> tuple[Path | None, Path | None]:
    for ancestor in (candidate, *candidate.parents):
        raw = ancestor / "lvs_cars_pack"
        safe = ancestor / "_runtime_validation" / "source" / "addons" / "lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1"
        if raw.is_dir() and safe.is_dir():
            return raw, safe
    return None, None


def _find_receipt(manifest_path: Path | None, expected_sha: str, names: Sequence[str]) -> Path | None:
    if manifest_path is None:
        return None
    for directory in (manifest_path.parent, *manifest_path.parents):
        for name in names:
            candidate = directory / name
            if candidate.is_file() and _sha256_file(candidate) == expected_sha:
                return candidate
    return None


def _validate_bridge(document: dict[str, Any], raw: dict[str, Any], safe: dict[str, Any], frozen: bool) -> dict[str, dict[str, Any]]:
    if document.get("schema") != BRIDGE_SCHEMA:
        _fail(f"bc3_bridge_schema:{document.get('schema')}")
    if document.get("rawTreeSha256") != raw["tree_sha256"] or document.get("rawTreeBytes") != raw["total_bytes"] or document.get("rawTreeFileCount") != raw["file_count"]:
        _fail("bc3_bridge_raw_authority_header_mismatch")
    if document.get("approvedSafeTreeSha256") != safe["tree_sha256"] or document.get("approvedSafeTreeBytes") != safe["total_bytes"] or document.get("approvedSafeTreeFileCount") != safe["file_count"]:
        _fail("bc3_bridge_safe_authority_header_mismatch")
    rows = document.get("rows")
    if not isinstance(rows, list):
        _fail("bc3_bridge_missing_rows")
    if frozen:
        actual = (
            document.get("physicalOpaqueBc3Count"), document.get("rawDxt1Count"),
            document.get("semanticExceptionBc3Count"), document.get("equivalentPriorSafeColorChainCount"),
        )
        if len(rows) != FROZEN_BC_ROWS or actual != FROZEN_BC_PARTITION:
            _fail(f"bc3_bridge_frozen_partition:{len(rows)}:{actual}")
        if document.get("priorManifestSha256") != EXPECTED_PRIOR_MANIFEST_SHA256:
            _fail("bc3_bridge_frozen_prior_manifest")
    result: dict[str, dict[str, Any]] = {}
    previous = None
    counts = {"physicalOpaque": 0, "rawDxt1": 0, "semanticDetailRgbOnly": 0}
    for row in rows:
        relative = _canonical_relative(row.get("relativePath", ""), context="bridge_path")
        if relative != relative.lower() or row.get("approvedOutputPath") != relative:
            _fail(f"bc3_bridge_noncanonical_path:{relative}")
        if previous is not None and previous >= relative:
            _fail("bc3_bridge_rows_not_sorted")
        previous = relative
        if relative.casefold() in (key.casefold() for key in result):
            _fail(f"bc3_bridge_duplicate_path:{relative}")
        for field in ("rawSha256", "priorSafeSourceSha256", "approvedSafeSha256", "consumerReceiptSha256"):
            _require_sha(row.get(field), f"bridge.{field}")
        mode = row.get("eligibilityMode")
        if mode not in counts:
            _fail(f"bc3_bridge_mode:{relative}:{mode}")
        counts[mode] += 1
        if (mode == "rawDxt1") != (row.get("rawFormat") == 13):
            _fail(f"bc3_bridge_mode_format:{relative}")
        mips = row.get("rawMips")
        if not isinstance(mips, list) or not mips:
            _fail(f"bc3_bridge_missing_mips:{relative}")
        for index, mip in enumerate(mips, 1):
            if mip.get("level") != index or min(mip.get("width", 0), mip.get("height", 0), mip.get("depth", 0), mip.get("planeCount", 0), mip.get("payloadBytes", 0)) <= 0:
                _fail(f"bc3_bridge_invalid_mip:{relative}:{index}")
            _require_sha(mip.get("payloadSha256"), "bridge.mip.payloadSha256")
        result[relative] = row
    if counts["physicalOpaque"] != document.get("physicalOpaqueBc3Count") or counts["rawDxt1"] != document.get("rawDxt1Count") or counts["semanticDetailRgbOnly"] != document.get("semanticExceptionBc3Count"):
        _fail("bc3_bridge_partition_accounting")
    return result


def _aggregate_expected(manifest: dict[str, Any]) -> dict[str, Any]:
    rows = manifest["rows"]
    raw_vtf = sum(row["rawBytes"] for row in rows)
    safe_vtf = sum(row["safeBytes"] for row in rows)
    spatial_vtf = sum(row["spatialBytes"] for row in rows)
    final_vtf = sum(row["finalBytes"] for row in rows)
    def savings(source: int, final: int) -> dict[str, Any]:
        saved = source - final
        return {"savedBytes": saved, "savedMb": saved / 1_000_000, "savedMib": saved / 1_048_576, "percent": 0 if source == 0 else saved * 100 / source}
    levels: dict[str, int] = {}
    classes: dict[str, int] = {}
    families: dict[str, int] = {}
    for row in rows:
        levels[str(row["selectedLevel"])] = levels.get(str(row["selectedLevel"]), 0) + 1
        families[row["family"]] = families.get(row["family"], 0) + 1
        for semantic in row["semanticClasses"]:
            classes[semantic] = classes.get(semantic, 0) + 1
    raw_tree, safe_tree, final_tree = manifest["rawTree"], manifest["safeTree"], manifest["finalTree"]
    return {
        "corpus": {"rawBytes": raw_tree["bytes"], "safeBytes": safe_tree["bytes"], "finalBytes": final_tree["bytes"], "rawToFinal": savings(raw_tree["bytes"], final_tree["bytes"]), "safeToFinal": savings(safe_tree["bytes"], final_tree["bytes"])},
        "vtf": {"rawBytes": raw_vtf, "safeBytes": safe_vtf, "spatialBytes": spatial_vtf, "finalBytes": final_vtf, "resolutionSavedBytes": raw_vtf - spatial_vtf, "formatSavedBytes": spatial_vtf - final_vtf, "rawToFinal": savings(raw_vtf, final_vtf), "safeToFinal": savings(safe_vtf, final_vtf)},
        "levels": dict(sorted(levels.items(), key=lambda item: int(item[0]))),
        "classes": dict(sorted(classes.items())), "families": dict(sorted(families.items())),
        "authored": sum(bool(row["authored"]) for row in rows), "generated": sum(bool(row["generated"]) for row in rows),
        "byteFloor": sum(bool(row["byteFloor"]) for row in rows), "blocked": sum(row.get("blocker") is not None for row in rows),
        "bcReceiptRows": sum(row.get("bcEligibilityMode") is not None for row in rows),
    }


def _json_equivalent(actual: Any, expected: Any, label: str) -> None:
    if isinstance(expected, float):
        if not isinstance(actual, (float, int)) or not math.isclose(float(actual), expected, rel_tol=0, abs_tol=1e-12):
            _fail(f"{label}:{actual}!={expected}")
    elif isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            _fail(f"{label}_keys")
        for key in expected:
            _json_equivalent(actual[key], expected[key], f"{label}.{key}")
    elif actual != expected:
        _fail(f"{label}:{actual}!={expected}")


def _canonical_policy() -> dict[str, Any]:
    names = ("commonRgbPsnrMin", "commonRgbSsimMin", "edgeRetentionMin", "normalMeanMax", "normalP95Max", "normalMax", "alphaSsimMin", "alphaMaeMax", "alphaP99Max", "cutoutCoverageTopMax", "cutoutCoverageMipsMax", "cutoutIouMin", "emissiveEnergyDriftMax", "emissiveHotspotIouMin", "glassDeltaEMax", "glassEdgeRetentionMin", "textPsnrMin", "textSsimMin", "textEdgeRetentionMin", "detailFrequencyRetentionMin", "detailSeamIncreaseMax", "cubemapSeamIncreaseMax", "temporalPsnrMin", "componentRetentionMin", "hausdorffMax")
    values = {
        "conservative": (26,.950,.50,3,8,25,.980,.020,.080,.01,.02,.980,.05,.950,2,.65,30,.970,.70,.70,1/255,2/255,26,1,2),
        "balanced": (24,.925,.40,5,14,40,.960,.040,.140,.02,.04,.950,.10,.900,4,.55,27,.950,.55,.55,2/255,4/255,24,.99,4),
        "aggressive": (22,.900,.30,8,22,60,.930,.070,.240,.04,.07,.900,.20,.800,6,.45,24,.920,.40,.40,4/255,8/255,22,.97,8),
    }
    return {
        "id": "universal-vtf-v1", "fixtureSha256": FIXTURE_SHA256, "mandatoryFloorLevel": 1,
        "colorFilter": "Magick.NET-14.10.4-LanczosSharp", "comparisonFilter": "bilinear-domain-aware-v1",
        "ssim": "rgb-min-channel;11x11;sigma=1.5;k1=.01;k2=.03;population;reflect101",
        "percentile": "nearest-rank-ceil-pn-minus-one", "deltaE": "CIEDE2000-D65-2degree",
        "cutoutClassifier": "poles=.1,.9;combined>=.95;each>=.01", "coverageBisectionSteps": 32,
        "coverageSeeds": [0,1,8], "coverageQuantization": "round-to-nearest-even-uint8",
        "retentionZeroRule": "both-zero=1;source-zero-candidate-nonzero=fail", "iouEmptyRule": "both-empty=1;one-empty=0",
        "routeScore": "minimum-normalized-margin;nan-fail;srgb-flag-tie;numeric-final",
        "thresholds": {profile: dict(zip(names, row)) for profile, row in values.items()},
    }


def _frozen_toolchain() -> tuple[dict[str, Any], str]:
    binaries = [
        {"name":"VTFEdit.zip","version":"embedded-v1","bytes":1431232,"sha256":"87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c"},
        {"name":"VTFEdit/VTFCmd.exe","version":"1.0.10","bytes":102400,"sha256":"30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e"},
        {"name":"magick/Magick.NET-Q16-AnyCPU.dll","version":"14.10.4","bytes":1126696,"sha256":"5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6"},
        {"name":"magick/Magick.NET.Core.dll","version":"14.10.4","bytes":1535784,"sha256":"c1d25c3c6cbef662a4f57631732fe9430b29b77662910552962059fb06034523"},
        {"name":"magick/native/win-x64/Magick.Native-Q16-x64.dll","version":"7.1.2","bytes":23001896,"sha256":"82865a3fd86d84b5eff7761b9c51cc750a7f90ecd446e52046f2ed13b3e5ae82"},
    ]
    arguments = ["-file","<input.png>","-output","<isolated-dir>","-format","<DXT1|DXT5>","-alphaformat","<same>","-version","<source-version-clamped-to-7.4>"]
    canonical = {"schema":"universal-vtf-toolchain-fingerprint-v1","arguments":arguments,"binaries":sorted(binaries,key=lambda row:row["name"])}
    fingerprint = _sha256_bytes(json.dumps(canonical,separators=(",",":"),ensure_ascii=False).encode())
    policy_toolchain = {
        "magickVersion":"14.10.4","magickManagedBytes":1126696,"magickManagedSha256":binaries[2]["sha256"],
        "magickNativeSha256":{"magick/native/win-x64/Magick.Native-Q16-x64.dll":binaries[4]["sha256"]},
        "magickFilter":"LanczosSharp","comparisonFilter":"bilinear-domain-aware-v1","vtfCmdVersion":"1.0.10",
        "vtfCmdBytes":102400,"vtfCmdSha256":binaries[1]["sha256"],"vtfEditArchiveSha256":binaries[0]["sha256"],
        "vtfCmdArgumentTemplate":arguments,
    }
    return policy_toolchain, fingerprint


def _policy_fingerprint(bridge_sha: str) -> tuple[str, str]:
    toolchain, toolchain_sha = _frozen_toolchain()
    document = {"schema":"universal-vtf-policy-fingerprint-v1","policy":_canonical_policy(),"toolchain":toolchain,"bc3RawToApprovedSha256":bridge_sha}
    # Utf8JsonWriter's default JavaScriptEncoder escapes the HTML-sensitive
    # characters present in the frozen classifier string and VTFCmd templates.
    canonical = json.dumps(document,separators=(",",":"),ensure_ascii=True)
    canonical = canonical.replace("<", "\\u003C").replace(">", "\\u003E").replace("&", "\\u0026")
    return _sha256_bytes(canonical.encode()), toolchain_sha


def audit_corpus(
    manifest_source: os.PathLike[str] | str | dict[str, Any],
    candidate: os.PathLike[str] | str,
    *, raw: os.PathLike[str] | str | None = None, safe: os.PathLike[str] | str | None = None,
    bridge: os.PathLike[str] | str | None = None, runtime_caps: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    manifest, manifest_path = _load_json(manifest_source, "manifest")
    candidate_root = Path(candidate).resolve()
    if not candidate_root.is_dir():
        _fail(f"candidate_missing:{candidate_root}")
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        _fail("manifest_rows_missing")
    # Resolve and check every declared file first. This intentionally gives a
    # useful missing-file finding even for a deliberately minimal test manifest.
    for row in rows:
        relative = _canonical_relative(row.get("relativePath", ""), context="manifest_path")
        path = _contained(candidate_root, relative)
        if not path.is_file():
            _fail(f"manifest_file_missing:{relative}")
    if manifest.get("schema") != SCHEMA:
        _fail(f"manifest_schema:{manifest.get('schema')}")

    for field in ("policyFingerprint", "fixtureSha256", "toolchainSha256", "bcBridgeSha256"):
        _require_sha(manifest.get(field), f"manifest.{field}")
    if manifest.get("runtimeCapsSha256") is not None:
        _require_sha(manifest["runtimeCapsSha256"], "manifest.runtimeCapsSha256")
    if manifest["fixtureSha256"] != FIXTURE_SHA256:
        _fail("manifest_fixture_sha256")
    profile = manifest.get("profile")
    if profile not in ("conservative", "balanced", "aggressive"):
        _fail(f"manifest_profile:{profile}")
    for name in ("raw", "safe", "final"):
        if manifest.get(f"{name}Tree", {}).get("authority") != name:
            _fail(f"manifest_tree_authority:{name}")

    candidate_inventory = tree_inventory(candidate_root)
    _tree_matches(candidate_inventory, manifest.get("finalTree", {}), "final")
    candidate_files = _files_by_path(candidate_inventory)
    manifest_paths = [row["relativePath"] for row in rows]
    if len({path.casefold() for path in manifest_paths}) != len(manifest_paths) or manifest_paths != sorted(manifest_paths):
        _fail("manifest_paths_duplicate_or_unsorted")
    actual_vtfs = sorted(path for path in candidate_files if path.lower().endswith(".vtf"))
    if actual_vtfs != manifest_paths:
        _fail("manifest_vtf_path_coverage")

    frozen = manifest.get("rawTree", {}).get("treeSha256") == FROZEN_RAW["tree_sha256"]
    if frozen and len(rows) != FROZEN_VTF_COUNT:
        _fail(f"manifest_frozen_row_count:{len(rows)}!={FROZEN_VTF_COUNT}")
    if raw is None or safe is None:
        found_raw, found_safe = _find_frozen_authorities(candidate_root)
        raw = raw or found_raw
        safe = safe or found_safe
    if raw is None or safe is None:
        _fail("source_authority_missing:pass --raw and --safe")
    raw_root, safe_root = Path(raw).resolve(), Path(safe).resolve()
    raw_inventory, safe_inventory = tree_inventory(raw_root), tree_inventory(safe_root)
    _tree_matches(raw_inventory, manifest.get("rawTree", {}), "raw")
    _tree_matches(safe_inventory, manifest.get("safeTree", {}), "safe")
    if frozen:
        _tree_matches(raw_inventory, FROZEN_RAW, "frozen_raw")
        _tree_matches(safe_inventory, FROZEN_SAFE, "frozen_safe")
    raw_files, safe_files = _files_by_path(raw_inventory), _files_by_path(safe_inventory)

    bridge_sha = manifest["bcBridgeSha256"].lower()
    if bridge is None:
        bridge = _find_receipt(manifest_path, bridge_sha, ("bc3-raw-to-approved-v1.json",))
    if bridge is None:
        _fail("bc3_bridge_missing")
    bridge_path = Path(bridge).resolve()
    if _sha256_file(bridge_path) != bridge_sha:
        _fail("bc3_bridge_sha256_mismatch")
    bridge_document, _ = _load_json(bridge_path, "bc3_bridge")
    bridge_rows = _validate_bridge(bridge_document, raw_inventory, safe_inventory, frozen)

    expected_policy, expected_toolchain = _policy_fingerprint(bridge_sha)
    if manifest["toolchainSha256"] != expected_toolchain:
        _fail(f"manifest_toolchain_fingerprint:{manifest['toolchainSha256']}!={expected_toolchain}")
    if manifest["policyFingerprint"] != expected_policy:
        _fail(f"manifest_policy_fingerprint:{manifest['policyFingerprint']}!={expected_policy}")
    caps_sha = manifest.get("runtimeCapsSha256")
    if caps_sha is not None:
        if runtime_caps is None:
            runtime_caps = _find_receipt(manifest_path, caps_sha, ("runtime-caps-v1.json",))
        if runtime_caps is None or _sha256_file(Path(runtime_caps)) != caps_sha:
            _fail("runtime_caps_missing_or_hash_mismatch")
        caps_document, _ = _load_json(runtime_caps, "runtime_caps")
        if caps_document.get("schema") != CAPS_SCHEMA or not isinstance(caps_document.get("caps"), list):
            _fail("runtime_caps_schema")

    # Every non-VTF byte comes from the safe baseline, byte-for-byte.
    safe_non_vtf = {path: row for path, row in safe_files.items() if not path.lower().endswith(".vtf")}
    candidate_non_vtf = {path: row for path, row in candidate_files.items() if not path.lower().endswith(".vtf")}
    if set(safe_non_vtf) != set(candidate_non_vtf):
        _fail("non_vtf_path_set_mismatch")
    for relative, safe_file in safe_non_vtf.items():
        candidate_file = candidate_non_vtf[relative]
        if (safe_file["bytes"], safe_file["sha256"]) != (candidate_file["bytes"], candidate_file["sha256"]):
            _fail(f"non_vtf_identity:{relative}")

    bc_receipts = 0
    for row in rows:
        relative = row["relativePath"]
        for field in ("consumerProofSha256","rawSha256","safeSha256","spatialSha256","finalSha256"):
            _require_sha(row.get(field), f"row.{field}:{relative}")
        raw_file, safe_file, final_file = raw_files.get(relative), safe_files.get(relative), candidate_files.get(relative)
        if raw_file is None or safe_file is None or final_file is None:
            _fail(f"row_authority_file_missing:{relative}")
        if (row.get("rawBytes"), row.get("rawSha256")) != (raw_file["bytes"], raw_file["sha256"]):
            _fail(f"row_raw_identity:{relative}")
        if (row.get("safeBytes"), row.get("safeSha256")) != (safe_file["bytes"], safe_file["sha256"]):
            _fail(f"row_safe_identity:{relative}")
        if (row.get("finalBytes"), row.get("finalSha256")) != (final_file["bytes"], final_file["sha256"]):
            _fail(f"row_final_identity:{relative}")
        authored, generated = row.get("authored"), row.get("generated")
        if not isinstance(authored, bool) or not isinstance(generated, bool) or authored == generated:
            _fail(f"row_authorship:{relative}")
        levels = (row.get("conservativeLevel"), row.get("balancedLevel"), row.get("aggressiveLevel"))
        if not all(isinstance(level, int) and level >= 1 for level in levels) or not levels[0] <= levels[1] <= levels[2]:
            _fail(f"row_level_nesting:{relative}:{levels}")
        expected_selected = levels[("conservative","balanced","aggressive").index(profile)]
        if row.get("selectedLevel") != expected_selected or not row.get("mandatoryFloorApplied"):
            _fail(f"row_selected_level_or_floor:{relative}")
        if min(row.get("rawWidth",0),row.get("rawHeight",0),row.get("rawDepth",0),row.get("rawFrames",0),row.get("rawFaces",0),row.get("rawMipCount",0)) <= 0:
            _fail(f"row_raw_shape:{relative}")
        selected = row["selectedLevel"]
        expected_width, expected_height = max(1,row["rawWidth"] >> selected), max(1,row["rawHeight"] >> selected)
        expected_depth = max(1,row["rawDepth"] >> selected) if authored else row["rawDepth"]
        expected_mips = row["rawMipCount"] - selected if authored else 1
        if (row.get("finalWidth"),row.get("finalHeight"),row.get("finalDepth"),row.get("finalMipCount")) != (expected_width,expected_height,expected_depth,expected_mips):
            _fail(f"row_final_shape_policy:{relative}")
        if row.get("rawFormat") not in (13,15) or row.get("finalFormat") not in (13,15,):
            _fail(f"row_format_policy:{relative}")
        raw_layout = parse_vtf(_contained(raw_root, relative))
        raw_physical = (
            raw_layout["width"], raw_layout["height"], raw_layout["depth"],
            raw_layout["frames"], raw_layout["faces"], raw_layout["mip_count"],
            raw_layout["format"],
        )
        raw_declared = (
            row["rawWidth"], row["rawHeight"], row["rawDepth"], row["rawFrames"],
            row["rawFaces"], row["rawMipCount"], row["rawFormat"],
        )
        if raw_physical != raw_declared:
            _fail(f"row_raw_reparse_mismatch:{relative}:{raw_physical}!={raw_declared}")
        layout = parse_vtf(_contained(candidate_root, relative))
        if layout["format"] == 20:
            _fail(f"candidate_bc1_one_bit_alpha_forbidden:{relative}")
        physical = (layout["width"],layout["height"],layout["depth"],layout["mip_count"],layout["format"],layout["frames"],layout["faces"],len(layout["resources"]))
        declared = (row["finalWidth"],row["finalHeight"],row["finalDepth"],row["finalMipCount"],row["finalFormat"],row["rawFrames"],row["rawFaces"],row["resourceCount"])
        if physical != declared:
            _fail(f"row_reparse_mismatch:{relative}:{physical}!={declared}")

        bridge_row = bridge_rows.get(relative)
        if bridge_row is None:
            if row.get("bcEligibilityMode") is not None or row.get("bcReceiptSha256") is not None:
                _fail(f"unexpected_bc_receipt:{relative}")
            continue
        bc_receipts += 1
        if row.get("bcEligibilityMode") != bridge_row.get("eligibilityMode") or row.get("bcReceiptSha256") != bridge_sha:
            _fail(f"bc_receipt_mismatch:{relative}")
        if (bridge_row.get("rawBytes"), bridge_row.get("rawSha256"), bridge_row.get("rawFormat")) != (raw_file["bytes"], raw_file["sha256"], raw_layout["format"]):
            _fail(f"bc3_bridge_raw_identity:{relative}")
        if (bridge_row.get("approvedSafeBytes"), bridge_row.get("approvedSafeSha256")) != (safe_file["bytes"], safe_file["sha256"]):
            _fail(f"bc3_bridge_safe_identity:{relative}")
        for receipt in bridge_row["rawMips"]:
            level_number = receipt["level"]
            level_info = raw_layout["levels"][level_number]
            level_ranges = sorted(
                (item for item in raw_layout["subresources"] if item["level"] == level_number),
                key=lambda item: (item["frame"], item["face"], item["depth_slice"]),
            )
            mip_payload = b"".join(
                raw_layout["high_payload"][
                    item["offset"] - raw_layout["high_offset"] :
                    item["offset"] - raw_layout["high_offset"] + item["length"]
                ]
                for item in level_ranges
            )
            if (
                receipt.get("width"), receipt.get("height"), receipt.get("depth"),
                receipt.get("planeCount"), receipt.get("payloadBytes"), receipt.get("payloadSha256")
            ) != (
                level_info["width"], level_info["height"], level_info["depth"],
                len(level_ranges), len(mip_payload), _sha256_bytes(mip_payload)
            ):
                _fail(f"bc3_bridge_mip_receipt:{relative}:{level_number}")
            if receipt.get("alpha") != _alpha_receipt(mip_payload, raw_layout["format"]):
                _fail(f"bc3_bridge_alpha_receipt:{relative}:{level_number}")
        selected_payload = b"".join(
            raw_layout["high_payload"][level["offset"]-raw_layout["high_offset"]:level["offset"]-raw_layout["high_offset"]+level["byte_count"]]
            for level in sorted((level for level in raw_layout["levels"] if level["level"] >= selected), key=lambda value:value["offset"])
        )
        if raw_layout["format"] == 15:
            expected_payload = transcode_bc3_payload(selected_payload)
            if layout["format"] != 13:
                _fail(f"bc3_bridge_final_not_dxt1:{relative}")
        else:
            expected_payload = selected_payload
            if layout["format"] != 13:
                _fail(f"raw_dxt1_final_not_dxt1:{relative}")
        if layout["high_payload"] != expected_payload:
            _fail(f"bc_authored_chain_mismatch:{relative}")
    if frozen and bc_receipts != FROZEN_BC_ROWS:
        _fail(f"bc_receipt_count:{bc_receipts}!={FROZEN_BC_ROWS}")
    _json_equivalent(manifest.get("aggregate"), _aggregate_expected(manifest), "manifest.aggregate")

    # Re-capture source authorities so concurrent mutation cannot hide behind a
    # valid first snapshot.
    raw_after, safe_after = tree_inventory(raw_root), tree_inventory(safe_root)
    if (raw_after["file_count"],raw_after["total_bytes"],raw_after["tree_sha256"]) != (raw_inventory["file_count"],raw_inventory["total_bytes"],raw_inventory["tree_sha256"]):
        _fail("raw_source_authority_changed")
    if (safe_after["file_count"],safe_after["total_bytes"],safe_after["tree_sha256"]) != (safe_inventory["file_count"],safe_inventory["total_bytes"],safe_inventory["tree_sha256"]):
        _fail("safe_source_authority_changed")
    return {"schema":"universal-vtf-independent-audit-v1","status":"CLEAN","profile":profile,"rows":len(rows),"bc_receipts":bc_receipts,"tree_sha256":candidate_inventory["tree_sha256"],"bytes":candidate_inventory["total_bytes"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fixtures = commands.add_parser("fixtures", help="audit the frozen mathematical fixture")
    fixtures.add_argument("json")
    corpus = commands.add_parser("corpus", help="audit one physical universal VTF corpus")
    corpus.add_argument("--manifest", required=True)
    corpus.add_argument("--candidate", required=True)
    corpus.add_argument("--raw")
    corpus.add_argument("--safe")
    corpus.add_argument("--bc-bridge")
    corpus.add_argument("--runtime-caps")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "fixtures":
            result = audit_fixtures(arguments.json)
        else:
            result = audit_corpus(arguments.manifest, arguments.candidate, raw=arguments.raw, safe=arguments.safe, bridge=arguments.bc_bridge, runtime_caps=arguments.runtime_caps)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except AuditError as error:
        print(json.dumps({"status":"DIRTY","error":str(error)},sort_keys=True,separators=(",",":")), file=sys.stderr)
        return 5
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
