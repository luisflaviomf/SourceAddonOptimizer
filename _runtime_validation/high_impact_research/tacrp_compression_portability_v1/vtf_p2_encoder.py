"""Deterministic reproduction encoder for the frozen CVTFP002 contract."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Iterable

from .vtf_p2_format import (
    FIELD_WIDTHS,
    HEADER_SIZE,
    MAGIC,
    MODE_DICTIONARY,
    MODE_RAW,
    STRATEGY_GLOBAL,
    STRATEGY_TILED,
    STRATEGY_WHOLE,
    TILE_SIZES,
    VERSION,
    VtfFallbackReason,
    VtfFormatError,
    VtfLayout,
    parse_vtf,
)


@dataclass(frozen=True)
class SequenceEncoding:
    mode: int
    bytes: bytes
    dictionary: tuple[bytes, ...]


@dataclass(frozen=True)
class VtfCodecCandidate:
    blob: bytes
    rank: int
    strategy: int
    tile: int
    layout: VtfLayout


@dataclass(frozen=True)
class VtfEncodeResult:
    blob: bytes | None
    rank: int | None
    strategy: int | None
    tile: int
    layout: VtfLayout | None
    fallback_reason: VtfFallbackReason | None

    @property
    def is_native(self) -> bool:
        return self.blob is None


def _pack_ids(ids: list[int], width: int) -> bytes:
    if width == 0:
        return b""
    packed = bytearray((len(ids) * width + 7) // 8)
    for index, value in enumerate(ids):
        start = index * width
        for bit in range(width):
            if value & (1 << bit):
                absolute = start + bit
                packed[absolute // 8] |= 1 << (absolute % 8)
    return bytes(packed)


def encode_sequence(values: Iterable[bytes], width: int) -> SequenceEncoding:
    if not isinstance(width, int) or width < 1:
        raise ValueError("width must be positive")
    materialized = tuple(values)
    if not materialized:
        raise ValueError("values must not be empty")
    if any(not isinstance(value, bytes) or len(value) != width for value in materialized):
        raise ValueError("every value must be bytes of the declared width")
    dictionary: list[bytes] = []
    ids: list[int] = []
    lookup: dict[bytes, int] = {}
    for value in materialized:
        identifier = lookup.get(value)
        if identifier is None:
            identifier = len(dictionary)
            lookup[value] = identifier
            dictionary.append(value)
        ids.append(identifier)
    bit_width = (len(dictionary) - 1).bit_length()
    raw = bytes((MODE_RAW,)) + b"".join(materialized)
    encoded_dictionary = (
        bytes((MODE_DICTIONARY,))
        + struct.pack("<I", len(dictionary))
        + b"".join(dictionary)
        + _pack_ids(ids, bit_width)
    )
    if len(encoded_dictionary) < len(raw):
        return SequenceEncoding(MODE_DICTIONARY, encoded_dictionary, tuple(dictionary))
    return SequenceEncoding(MODE_RAW, raw, ())


def _component_values(payload: bytes, block_size: int, widths: tuple[int, ...]) -> tuple[tuple[bytes, ...], ...]:
    offsets: list[int] = []
    cursor = 0
    for width in widths:
        offsets.append(cursor)
        cursor += width
    assert cursor == block_size
    return tuple(
        tuple(payload[offset + start : offset + start + width] for offset in range(0, len(payload), block_size))
        for start, width in zip(offsets, widths)
    )


def _encode_candidate(
    source: bytes,
    layout: VtfLayout,
    *,
    strategy: int,
    tile: int,
    rank: int,
) -> VtfCodecCandidate:
    payload = source[layout.high_offset : layout.high_offset + layout.high_length]
    block_size = layout.block_size
    if strategy == STRATEGY_WHOLE:
        widths = (block_size,)
    else:
        widths = FIELD_WIDTHS[layout.high_format]
    fields = _component_values(payload, block_size, widths)
    segment_size = tile if strategy == STRATEGY_TILED else layout.block_count
    segments: list[bytes] = []
    for start in range(0, layout.block_count, segment_size):
        count = min(segment_size, layout.block_count - start)
        for field, width in zip(fields, widths):
            segments.append(encode_sequence(field[start : start + count], width).bytes)
    segment_count = (layout.block_count + segment_size - 1) // segment_size
    header = bytearray(HEADER_SIZE)
    header[:8] = MAGIC
    struct.pack_into("<HH", header, 8, VERSION, HEADER_SIZE)
    tile_code = 0 if tile == 0 else TILE_SIZES.index(tile) + 1
    header[12:20] = bytes(
        (strategy, layout.high_format, tile_code, len(widths), layout.major, layout.minor, layout.mips, layout.faces)
    )
    struct.pack_into("<HHHH", header, 20, layout.width, layout.height, layout.frames, layout.depth)
    struct.pack_into(
        "<IIIIII",
        header,
        28,
        layout.high_offset,
        layout.high_length,
        layout.trailing,
        len(source),
        layout.block_count,
        segment_count,
    )
    header[52:84] = hashlib.sha256(source).digest()
    struct.pack_into("<HHII", header, 84, layout.header, len(layout.resources), layout.low_offset, layout.low_length)
    blob = bytes(header) + layout.prefix + b"".join(segments) + layout.suffix
    return VtfCodecCandidate(blob, rank, strategy, tile, layout)


def select_codec_candidate(candidates: Iterable[VtfCodecCandidate]) -> VtfCodecCandidate:
    materialized = tuple(candidates)
    if not materialized:
        raise ValueError("at least one codec candidate is required")
    return min(materialized, key=lambda candidate: (len(candidate.blob), candidate.rank))


def encode_vtf(source: bytes) -> VtfEncodeResult:
    if not isinstance(source, bytes):
        raise TypeError("source must be bytes")
    try:
        layout = parse_vtf(source)
    except VtfFormatError as exc:
        return VtfEncodeResult(None, None, None, 0, None, VtfFallbackReason(exc.reason))
    candidates = [
        _encode_candidate(source, layout, strategy=STRATEGY_WHOLE, tile=0, rank=1),
        _encode_candidate(source, layout, strategy=STRATEGY_GLOBAL, tile=0, rank=2),
    ]
    candidates.extend(
        _encode_candidate(source, layout, strategy=STRATEGY_TILED, tile=tile, rank=rank)
        for rank, tile in enumerate(TILE_SIZES, start=3)
    )
    selected = select_codec_candidate(candidates)
    if len(selected.blob) >= len(source):
        return VtfEncodeResult(None, 0, None, 0, layout, VtfFallbackReason.NOT_STRICTLY_SMALLER)
    return VtfEncodeResult(selected.blob, selected.rank, selected.strategy, selected.tile, layout, None)
