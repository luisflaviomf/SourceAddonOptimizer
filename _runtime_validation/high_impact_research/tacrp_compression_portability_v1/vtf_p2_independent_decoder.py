"""Strict independent decoder for CVTFP002; this module never imports the encoder."""

from __future__ import annotations

import hashlib
import struct

from .vtf_p2_format import (
    BLOCK_WIDTHS,
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
    VtfFormatError,
    parse_vtf,
)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, length: int, reason: str = "truncated_sequence") -> bytes:
        if length < 0 or self.pos + length > len(self.data):
            raise VtfFormatError(reason)
        result = self.data[self.pos : self.pos + length]
        self.pos += length
        return result

    def u8(self, reason: str = "truncated_sequence") -> int:
        return self.take(1, reason)[0]

    def u16(self, reason: str = "container_length") -> int:
        return struct.unpack("<H", self.take(2, reason))[0]

    def u32(self, reason: str = "truncated_sequence") -> int:
        return struct.unpack("<I", self.take(4, reason))[0]


def _bit_width(count: int) -> int:
    return (count - 1).bit_length()


def _packed_length(count: int, width: int) -> int:
    return (count * width + 7) // 8


def _read_id(packed: bytes, index: int, width: int) -> int:
    identifier = 0
    start = index * width
    for bit in range(width):
        absolute = start + bit
        if packed[absolute // 8] & (1 << (absolute % 8)):
            identifier |= 1 << bit
    return identifier


def _decode_sequence(reader: _Reader, *, width: int, count: int) -> bytes:
    if width < 1 or count < 1:
        raise VtfFormatError("sequence_shape")
    mode = reader.u8()
    raw_cost = 1 + count * width
    if mode == MODE_RAW:
        values = reader.take(count * width)
        unique_count = len({values[offset : offset + width] for offset in range(0, len(values), width)})
        dictionary_cost = 5 + unique_count * width + _packed_length(count, _bit_width(unique_count))
        if dictionary_cost < raw_cost:
            raise VtfFormatError("nonminimal_raw")
        return values
    if mode != MODE_DICTIONARY:
        raise VtfFormatError("sequence_mode")
    dictionary_count = reader.u32()
    if dictionary_count < 1 or dictionary_count > count:
        raise VtfFormatError("dictionary_count")
    dictionary_bytes = reader.take(dictionary_count * width)
    dictionary = tuple(
        dictionary_bytes[offset : offset + width]
        for offset in range(0, len(dictionary_bytes), width)
    )
    if len(set(dictionary)) != len(dictionary):
        raise VtfFormatError("duplicate_dictionary")
    bit_width = _bit_width(dictionary_count)
    packed = reader.take(_packed_length(count, bit_width))
    remainder = (count * bit_width) % 8
    if remainder and packed and packed[-1] >> remainder:
        raise VtfFormatError("sequence_padding")
    if 5 + dictionary_count * width + len(packed) >= raw_cost:
        raise VtfFormatError("nonminimal_dictionary")
    ids: list[int] = []
    next_first_seen = 0
    for index in range(count):
        identifier = _read_id(packed, index, bit_width)
        if identifier >= dictionary_count:
            raise VtfFormatError("dictionary_id")
        if identifier == next_first_seen:
            next_first_seen += 1
        elif identifier > next_first_seen:
            raise VtfFormatError("first_seen_order")
        ids.append(identifier)
    if next_first_seen != dictionary_count:
        raise VtfFormatError("unused_dictionary")
    return b"".join(dictionary[identifier] for identifier in ids)


def decode_sequence(encoded: bytes, *, width: int, count: int) -> bytes:
    if not isinstance(encoded, bytes):
        raise TypeError("encoded must be bytes")
    reader = _Reader(encoded)
    result = _decode_sequence(reader, width=width, count=count)
    if reader.pos != len(encoded):
        raise VtfFormatError("trailing_sequence")
    return result


def decode_vtf(blob: bytes, maximum_output: int = 32 * 1024 * 1024) -> bytes:
    if not isinstance(blob, bytes):
        raise TypeError("blob must be bytes")
    if not isinstance(maximum_output, int) or maximum_output < 1:
        raise ValueError("maximum_output must be positive")
    if len(blob) < HEADER_SIZE or len(blob) > 64 * 1024 * 1024:
        raise VtfFormatError("container_length")
    reader = _Reader(blob)
    if reader.take(8, "container_length") != MAGIC or reader.u16() != VERSION or reader.u16() != HEADER_SIZE:
        raise VtfFormatError("container_identity")
    strategy = reader.u8("container_length")
    high_format = reader.u8("container_length")
    tile_code = reader.u8("container_length")
    field_count = reader.u8("container_length")
    major = reader.u8("container_length")
    minor = reader.u8("container_length")
    mips = reader.u8("container_length")
    faces = reader.u8("container_length")
    width = reader.u16()
    height = reader.u16()
    frames = reader.u16()
    depth = reader.u16()
    high_offset = reader.u32("container_length")
    high_length = reader.u32("container_length")
    suffix_length = reader.u32("container_length")
    source_length = reader.u32("container_length")
    block_count = reader.u32("container_length")
    segment_count = reader.u32("container_length")
    source_sha = reader.take(32, "container_length")
    source_header = reader.u16()
    resource_count = reader.u16()
    low_offset = reader.u32("container_length")
    low_length = reader.u32("container_length")
    if reader.pos != HEADER_SIZE:
        raise VtfFormatError("container_header")
    if source_length > maximum_output:
        raise VtfFormatError("output_limit")
    if high_format not in BLOCK_WIDTHS:
        raise VtfFormatError("block_layout")
    block_size = BLOCK_WIDTHS[high_format]
    if high_length % block_size or block_count != high_length // block_size or block_count < 1:
        raise VtfFormatError("block_layout")
    tile = 0 if tile_code == 0 else (TILE_SIZES[tile_code - 1] if 1 <= tile_code <= len(TILE_SIZES) else None)
    component_widths = FIELD_WIDTHS[high_format]
    if strategy == STRATEGY_WHOLE:
        field_widths = (block_size,)
        valid_tuple = tile_code == 0 and field_count == 1
    elif strategy == STRATEGY_GLOBAL:
        field_widths = component_widths
        valid_tuple = tile_code == 0 and field_count == len(field_widths)
    elif strategy == STRATEGY_TILED:
        field_widths = component_widths
        valid_tuple = tile is not None and tile != 0 and field_count == len(field_widths)
    else:
        raise VtfFormatError("strategy_tuple")
    if not valid_tuple:
        raise VtfFormatError("strategy_tuple")
    expected_segments = (block_count + tile - 1) // tile if strategy == STRATEGY_TILED else 1
    if segment_count != expected_segments or high_offset + high_length + suffix_length != source_length:
        raise VtfFormatError("segment_or_source_bounds")
    prefix = reader.take(high_offset, "truncated_sequence")
    chunks: list[bytes] = []
    consumed = 0
    for _ in range(segment_count):
        value_count = min(tile, block_count - consumed) if strategy == STRATEGY_TILED else block_count
        decoded_fields = [
            _decode_sequence(reader, width=field_width, count=value_count)
            for field_width in field_widths
        ]
        for index in range(value_count):
            chunks.extend(
                field[index * field_width : (index + 1) * field_width]
                for field, field_width in zip(decoded_fields, field_widths)
            )
        consumed += value_count
    suffix = reader.take(suffix_length, "truncated_sequence")
    if reader.pos != len(blob) or consumed != block_count:
        raise VtfFormatError("container_eof")
    source = prefix + b"".join(chunks) + suffix
    if len(source) != source_length or hashlib.sha256(source).digest() != source_sha:
        raise VtfFormatError("source_sha")
    layout = parse_vtf(source)
    expected = (
        layout.major,
        layout.minor,
        layout.mips,
        layout.faces,
        layout.width,
        layout.height,
        layout.frames,
        layout.depth,
        layout.high_format,
        layout.high_offset,
        layout.high_length,
        layout.trailing,
        layout.header,
        len(layout.resources),
        layout.low_offset,
        layout.low_length,
    )
    observed = (
        major,
        minor,
        mips,
        faces,
        width,
        height,
        frames,
        depth,
        high_format,
        high_offset,
        high_length,
        suffix_length,
        source_header,
        resource_count,
        low_offset,
        low_length,
    )
    if observed != expected:
        raise VtfFormatError("container_binding")
    return source
