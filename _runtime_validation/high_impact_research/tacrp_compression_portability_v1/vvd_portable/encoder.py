"""Deterministic structural encoder for the portable VVD container."""

from __future__ import annotations

import hashlib
import struct

from _runtime_validation.high_impact_research.vvd_scalar_tiled_fixup_delta_exact_p1.encoder import (
    encode_lane as _encode_lane,
)

from .format import (
    CONTAINER_HEADER,
    FIXED_HEADER_SIZE,
    LAYOUT_CONTIGUOUS_NO_FIXUP,
    LAYOUT_DISJOINT_PADDED,
    MAGIC,
    VERSION,
    EncodeResult,
    LaneSelection,
)


_VVD_HEADER = struct.Struct("<4sIII8I4I")
_FIXUP = struct.Struct("<III")
_LEGAL_PADDING_LENGTHS = (4, 8, 12)


def _selection(value: object) -> LaneSelection:
    return LaneSelection(
        scope=int(getattr(value, "scope")),
        tile_count=int(getattr(value, "tile_count")),
        tile_modes=tuple(getattr(value, "tile_modes")),
    )


def _encode_uleb(value: int) -> bytes:
    if value < 0:
        raise ValueError("ULEB value is negative")
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)


def _zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def _validate_active_lods(active: tuple[int, ...], *, physical_count: int) -> None:
    if not active or active[0] != physical_count:
        raise ValueError("LOD0 differs from physical vertex count")
    if any(left < right for left, right in zip(active, active[1:])):
        raise ValueError("active LOD counts are not non-increasing")
    seen_zero = False
    for value in active:
        if value == 0:
            seen_zero = True
        elif seen_zero:
            raise ValueError("positive LOD count follows the first zero")


def _parse_source(source: bytes) -> dict[str, object]:
    if type(source) is not bytes:
        raise ValueError("VVD source must be immutable bytes")
    if len(source) < _VVD_HEADER.size:
        raise ValueError("truncated VVD header")
    fields = _VVD_HEADER.unpack_from(source)
    magic, version, _checksum, num_lods = fields[:4]
    lod_slots = tuple(fields[4:12])
    fixup_count, fixup_start, vertex_start, tangent_start = fields[-4:]
    if magic != b"IDSV" or version != 4:
        raise ValueError("unsupported VVD header")
    if not 1 <= num_lods <= 8:
        raise ValueError("invalid VVD LOD count")
    if fixup_count and fixup_start == _VVD_HEADER.size:
        candidate_fixup_end = fixup_start + fixup_count * _FIXUP.size
        candidate_padding_length = vertex_start - candidate_fixup_end
        if candidate_padding_length not in _LEGAL_PADDING_LENGTHS:
            raise ValueError("disjoint padding length is not 4, 8, or 12")
    if any(offset % 4 for offset in (fixup_start, vertex_start, tangent_start)):
        raise ValueError("physical ownership offsets are not word aligned")
    if tangent_start <= vertex_start or (tangent_start - vertex_start) % 48:
        raise ValueError("physical ownership base extent is invalid")
    physical_count = (tangent_start - vertex_start) // 48
    if not 0 < physical_count <= 0xFFFFFFFF:
        raise ValueError("invalid physical vertex count")
    tangent_end = tangent_start + physical_count * 16
    if tangent_end != len(source):
        raise ValueError("physical ownership tangent extent does not end at EOF")
    active_lods = lod_slots[:num_lods]
    _validate_active_lods(active_lods, physical_count=physical_count)

    if fixup_count == 0:
        if fixup_start != vertex_start or vertex_start != _VVD_HEADER.size:
            raise ValueError("contiguous no-fixup ownership is not canonical")
        layout = "contiguous_no_fixup"
        layout_code = LAYOUT_CONTIGUOUS_NO_FIXUP
        fixup_end = vertex_start
        padding_start = 0
        padding_length = 0
        rows: tuple[tuple[int, int, int], ...] = ()
    else:
        if fixup_start != _VVD_HEADER.size:
            raise ValueError("disjoint fixup span must start after the VVD header")
        fixup_end = fixup_start + fixup_count * _FIXUP.size
        if fixup_end > len(source) or vertex_start <= fixup_end:
            raise ValueError("fixup ownership overlaps or exceeds the source")
        padding_start = fixup_end
        padding_length = vertex_start - fixup_end
        if padding_length not in _LEGAL_PADDING_LENGTHS:
            raise ValueError("disjoint padding length is not 4, 8, or 12")
        if any(source[padding_start:vertex_start]):
            raise ValueError("disjoint padding is not all zero")
        layout = "disjoint_padded"
        layout_code = LAYOUT_DISJOINT_PADDED
        rows = tuple(
            _FIXUP.unpack_from(source, fixup_start + index * _FIXUP.size)
            for index in range(fixup_count)
        )
        for index, (lod, source_vertex, count) in enumerate(rows):
            if lod >= num_lods:
                raise ValueError(f"fixup {index} LOD exceeds the declared range")
            if (
                count <= 0
                or source_vertex >= physical_count
                or count > physical_count - source_vertex
            ):
                raise ValueError(f"fixup {index} exceeds physical bounds")
        for root_lod, declared in enumerate(active_lods):
            expanded = sum(count for lod, _source, count in rows if lod >= root_lod)
            if expanded != declared:
                raise ValueError("fixup expansion differs from active LOD count")

    return {
        "header": source[:64],
        "source_size": len(source),
        "physical_count": physical_count,
        "fixup_count": fixup_count,
        "num_lods": num_lods,
        "vertex_start": vertex_start,
        "tangent_start": tangent_start,
        "tangent_end": tangent_end,
        "fixup_start": fixup_start,
        "fixup_end": fixup_end,
        "padding_start": padding_start,
        "padding_length": padding_length,
        "layout": layout,
        "layout_code": layout_code,
        "rows": rows,
    }


def _encode_deltas(
    rows: tuple[tuple[int, int, int], ...], *, physical_count: int
) -> bytes:
    output = bytearray()
    previous_end = 0
    for index, (_lod, source_vertex, count) in enumerate(rows):
        if (
            count <= 0
            or source_vertex >= physical_count
            or count > physical_count - source_vertex
        ):
            raise ValueError(f"fixup {index} exceeds physical bounds")
        output.extend(_encode_uleb(_zigzag(source_vertex - previous_end)))
        output.extend(_encode_uleb(count))
        previous_end = source_vertex + count
    return bytes(output)


def encode_vvd(source: bytes) -> EncodeResult:
    """Encode bytes selected exclusively by their VVD physical structure."""
    parsed = _parse_source(source)
    physical_count = int(parsed["physical_count"])
    vertex_start = int(parsed["vertex_start"])
    tangent_start = int(parsed["tangent_start"])

    lane_blobs: list[bytes] = []
    selections: list[LaneSelection] = []
    for lane in range(12):
        words = tuple(
            source[
                vertex_start + cell * 48 + lane * 4 :
                vertex_start + cell * 48 + lane * 4 + 4
            ]
            for cell in range(physical_count)
        )
        blob, selected = _encode_lane(words)
        lane_blobs.append(blob)
        selections.append(_selection(selected))
    for lane in range(4):
        words = tuple(
            source[
                tangent_start + cell * 16 + lane * 4 :
                tangent_start + cell * 16 + lane * 4 + 4
            ]
            for cell in range(physical_count)
        )
        blob, selected = _encode_lane(words)
        lane_blobs.append(blob)
        selections.append(_selection(selected))

    rows = parsed["rows"]
    if not isinstance(rows, tuple):
        raise AssertionError("internal row contract")
    lod_lane = b""
    delta_payload = b""
    lod_lane_count = 0
    if rows:
        lod_words = tuple(struct.pack("<I", row[0]) for row in rows)
        lod_lane, selected = _encode_lane(lod_words)
        selections.append(_selection(selected))
        delta_payload = _encode_deltas(rows, physical_count=physical_count)
        lod_lane_count = 1

    fixed_header = CONTAINER_HEADER.pack(
        MAGIC,
        VERSION,
        FIXED_HEADER_SIZE,
        0,
        int(parsed["source_size"]),
        physical_count,
        int(parsed["fixup_count"]),
        int(parsed["num_lods"]),
        vertex_start,
        tangent_start,
        int(parsed["tangent_end"]),
        int(parsed["fixup_start"]),
        int(parsed["fixup_end"]),
        int(parsed["padding_start"]),
        int(parsed["padding_length"]),
        int(parsed["layout_code"]),
        12,
        4,
        lod_lane_count,
        0,
        len(lod_lane),
        len(delta_payload),
        parsed["header"],
        hashlib.sha256(source).digest(),
        b"\0" * 12,
    )
    return EncodeResult(
        fixed_header + b"".join(lane_blobs) + lod_lane + delta_payload,
        tuple(selections),
        str(parsed["layout"]),
    )
