"""Frozen CVTFP002 constants, VTF layout parser, and typed failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import struct


MAGIC = b"CVTFP002"
VERSION = 1
HEADER_SIZE = 96
MODE_RAW = 0
MODE_DICTIONARY = 1
STRATEGY_WHOLE = 1
STRATEGY_GLOBAL = 2
STRATEGY_TILED = 3
TILE_SIZES = (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 65536)
FIELD_WIDTHS = {13: (4, 4), 15: (2, 6, 4, 4)}
BLOCK_WIDTHS = {13: 8, 15: 16}
MAXIMUM_SOURCE = 64 * 1024 * 1024


class VtfFallbackReason(str, Enum):
    NOT_STRICTLY_SMALLER = "not_strictly_smaller"
    TRUNCATED_HEADER = "truncated_header"
    INVALID_SIGNATURE = "invalid_signature"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_HEADER = "unsupported_header"
    INVALID_DIMENSIONS = "invalid_dimensions"
    INVALID_FIRST_FRAME = "invalid_first_frame"
    UNSUPPORTED_HIGH_FORMAT = "unsupported_high_format"
    UNSUPPORTED_LOW_FORMAT = "unsupported_low_format"
    INVALID_THUMBNAIL = "invalid_thumbnail"
    UNSUPPORTED_RESOURCES = "unsupported_resources"
    INVALID_OFFSETS = "invalid_offsets"
    TRUNCATED_PAYLOAD = "truncated_payload"
    TRAILING_SOURCE = "trailing_source"
    OUTPUT_LIMIT = "output_limit"


class VtfFormatError(ValueError):
    """A stable machine-readable format or compatibility failure."""

    def __init__(self, reason: VtfFallbackReason | str):
        self.reason = reason.value if isinstance(reason, VtfFallbackReason) else reason
        super().__init__(self.reason)


@dataclass(frozen=True)
class VtfResource:
    tag: int
    flags: int
    data: int

    @property
    def is_inline(self) -> bool:
        return self.flags == 2


@dataclass(frozen=True)
class VtfLayout:
    major: int
    minor: int
    header: int
    width: int
    height: int
    flags: int
    frames: int
    first_frame: int
    depth: int
    faces: int
    high_format: int
    mips: int
    low_format: int
    low_width: int
    low_height: int
    low_offset: int
    low_length: int
    high_offset: int
    high_length: int
    mip_bytes: tuple[int, ...]
    resources: tuple[VtfResource, ...]
    prefix: bytes
    suffix: bytes
    source_length: int

    @property
    def block_size(self) -> int:
        return BLOCK_WIDTHS[self.high_format]

    @property
    def block_count(self) -> int:
        return self.high_length // self.block_size

    @property
    def trailing(self) -> int:
        return len(self.suffix)


def _fail(reason: VtfFallbackReason) -> None:
    raise VtfFormatError(reason)


def _image_length(format_id: int, width: int, height: int) -> int:
    block_size = BLOCK_WIDTHS.get(format_id)
    if block_size is None:
        _fail(VtfFallbackReason.UNSUPPORTED_LOW_FORMAT)
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_size


def parse_vtf(source: bytes) -> VtfLayout:
    """Parse only the frozen 7.1, 7.2, and constrained 7.4 BC layouts."""
    if not isinstance(source, bytes):
        raise TypeError("source must be bytes")
    if len(source) > MAXIMUM_SOURCE:
        _fail(VtfFallbackReason.OUTPUT_LIMIT)
    if len(source) < 16:
        _fail(VtfFallbackReason.TRUNCATED_HEADER)
    if source[:4] != b"VTF\0":
        _fail(VtfFallbackReason.INVALID_SIGNATURE)
    major, minor, header = struct.unpack_from("<III", source, 4)
    if major != 7 or minor not in (1, 2, 4):
        _fail(VtfFallbackReason.UNSUPPORTED_VERSION)
    expected_header = {1: 64, 2: 80, 4: 96}[minor]
    if header != expected_header:
        _fail(VtfFallbackReason.UNSUPPORTED_HEADER)
    if len(source) < header:
        _fail(VtfFallbackReason.TRUNCATED_HEADER)

    width, height, flags, frames, first_frame = struct.unpack_from("<HHIHH", source, 16)
    high_format = struct.unpack_from("<I", source, 52)[0]
    mips = source[56]
    low_format = struct.unpack_from("<i", source, 57)[0]
    low_width, low_height = source[61], source[62]
    depth = struct.unpack_from("<H", source, 63)[0] if minor >= 2 else 1
    if (
        not 1 <= width <= 16_384
        or not 1 <= height <= 16_384
        or not 1 <= frames <= 65_535
        or not 1 <= depth <= 16_384
        or not 1 <= mips <= 16
    ):
        _fail(VtfFallbackReason.INVALID_DIMENSIONS)
    if first_frame != 0xFFFF and first_frame >= frames:
        _fail(VtfFallbackReason.INVALID_FIRST_FRAME)
    if high_format not in BLOCK_WIDTHS:
        _fail(VtfFallbackReason.UNSUPPORTED_HIGH_FORMAT)
    if low_format == -1:
        if low_width or low_height:
            _fail(VtfFallbackReason.INVALID_THUMBNAIL)
        low_length = 0
    elif low_format in BLOCK_WIDTHS:
        if not low_width or not low_height:
            _fail(VtfFallbackReason.INVALID_THUMBNAIL)
        low_length = _image_length(low_format, low_width, low_height)
    else:
        _fail(VtfFallbackReason.UNSUPPORTED_LOW_FORMAT)

    faces = 1 if not flags & 0x4000 else (6 if minor >= 5 or first_frame == 0xFFFF else 7)
    block_size = BLOCK_WIDTHS[high_format]
    mip_bytes: list[int] = []
    high_length = 0
    for level in range(mips):
        level_length = (
            max(1, (max(1, width >> level) + 3) // 4)
            * max(1, (max(1, height >> level) + 3) // 4)
            * block_size
            * max(1, depth >> level)
            * frames
            * faces
        )
        high_length += level_length
        if high_length > MAXIMUM_SOURCE:
            _fail(VtfFallbackReason.OUTPUT_LIMIT)
        mip_bytes.append(level_length)

    resources: tuple[VtfResource, ...] = ()
    low_offset = header
    high_offset = header + low_length
    if minor == 4:
        resource_count = struct.unpack_from("<I", source, 68)[0]
        if resource_count != 2:
            _fail(VtfFallbackReason.UNSUPPORTED_RESOURCES)
        parsed: list[VtfResource] = []
        for index in range(2):
            offset = 80 + index * 8
            tag = int.from_bytes(source[offset : offset + 3], "little")
            parsed.append(VtfResource(tag, source[offset + 3], struct.unpack_from("<I", source, offset + 4)[0]))
        resources = tuple(parsed)
        disk = {(item.tag, item.flags): item.data for item in resources if item.flags == 0}
        inline = {(item.tag, item.flags): item.data for item in resources if item.flags == 2}
        if low_format == -1:
            if set(disk) != {(0x30, 0)} or set(inline) != {(0x435243, 2)}:
                _fail(VtfFallbackReason.UNSUPPORTED_RESOURCES)
            high_offset = disk[(0x30, 0)]
        else:
            if set(disk) != {(1, 0), (0x30, 0)} or inline:
                _fail(VtfFallbackReason.UNSUPPORTED_RESOURCES)
            low_offset = disk[(1, 0)]
            high_offset = disk[(0x30, 0)]
        if low_offset != header or high_offset != header + low_length:
            _fail(VtfFallbackReason.INVALID_OFFSETS)

    high_end = high_offset + high_length
    if low_offset < 1 or high_offset < 1 or low_length > MAXIMUM_SOURCE or high_length > MAXIMUM_SOURCE:
        _fail(VtfFallbackReason.INVALID_OFFSETS)
    if high_offset < header or high_end > len(source):
        _fail(VtfFallbackReason.TRUNCATED_PAYLOAD)
    if high_end != len(source):
        _fail(VtfFallbackReason.TRAILING_SOURCE)
    return VtfLayout(
        major=major,
        minor=minor,
        header=header,
        width=width,
        height=height,
        flags=flags,
        frames=frames,
        first_frame=first_frame,
        depth=depth,
        faces=faces,
        high_format=high_format,
        mips=mips,
        low_format=low_format,
        low_width=low_width,
        low_height=low_height,
        low_offset=low_offset,
        low_length=low_length,
        high_offset=high_offset,
        high_length=high_length,
        mip_bytes=tuple(mip_bytes),
        resources=resources,
        prefix=source[:high_offset],
        suffix=source[high_end:],
        source_length=len(source),
    )
