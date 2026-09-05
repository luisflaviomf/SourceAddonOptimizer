"""Deterministic encoder for the exact three-layout VVD P1 container."""

from __future__ import annotations

import hashlib
import struct

from .format import (
    CONTAINER_HEADER,
    FIXED_HEADER_SIZE,
    LANE_HEADER,
    LAYOUT_CODES,
    MAGIC,
    MODE_CONST,
    MODE_DICT,
    MODE_RAW,
    TILE_HEADER,
    TILE_SCOPES,
    VERSION,
    EncodeResult,
    LaneSelection,
)


_VVD_HEADER = struct.Struct("<4sIII8I4I")
_FIXUP = struct.Struct("<III")


def _require_words(words: tuple[bytes, ...]) -> None:
    if not words:
        raise ValueError("a lane or tile must contain at least one word")
    if any(len(word) != 4 for word in words):
        raise ValueError("every lane word must be exactly four bytes")


def _pack_lsb_indexes(indexes: tuple[int, ...], bits: int) -> bytes:
    packed = bytearray((len(indexes) * bits + 7) // 8)
    bit_offset = 0
    for index in indexes:
        for bit in range(bits):
            if index & (1 << bit):
                packed[bit_offset >> 3] |= 1 << (bit_offset & 7)
            bit_offset += 1
    return bytes(packed)


def encode_tile(words: tuple[bytes, ...], *, mode: int) -> bytes:
    words = tuple(words)
    _require_words(words)
    if mode == MODE_CONST:
        if any(word != words[0] for word in words[1:]):
            raise ValueError("CONST tile contains different words")
        index_bits = 0
        dictionary_count = 0
        payload = words[0]
    elif mode == MODE_RAW:
        index_bits = 0
        dictionary_count = 0
        payload = b"".join(words)
    elif mode == MODE_DICT:
        dictionary = tuple(sorted(set(words), key=lambda word: int.from_bytes(word, "little")))
        if len(dictionary) < 2:
            raise ValueError("DICT tile requires at least two words")
        index_bits = (len(dictionary) - 1).bit_length()
        positions = {word: index for index, word in enumerate(dictionary)}
        payload = b"".join(dictionary) + _pack_lsb_indexes(
            tuple(positions[word] for word in words), index_bits
        )
        dictionary_count = len(dictionary)
    else:
        raise ValueError("unknown tile mode")
    return TILE_HEADER.pack(
        mode, index_bits, 0, len(words), dictionary_count, len(payload)
    ) + payload


def _best_tile(words: tuple[bytes, ...]) -> tuple[bytes, int]:
    candidates: list[tuple[int, int, bytes]] = []
    if all(word == words[0] for word in words[1:]):
        encoded = encode_tile(words, mode=MODE_CONST)
        candidates.append((len(encoded), MODE_CONST, encoded))
    encoded = encode_tile(words, mode=MODE_RAW)
    candidates.append((len(encoded), MODE_RAW, encoded))
    if len(set(words)) >= 2:
        encoded = encode_tile(words, mode=MODE_DICT)
        candidates.append((len(encoded), MODE_DICT, encoded))
    _, mode, encoded = min(candidates, key=lambda item: (item[0], item[1]))
    return encoded, mode


def encode_lane(words: tuple[bytes, ...]) -> tuple[bytes, LaneSelection]:
    words = tuple(words)
    _require_words(words)
    candidates: list[tuple[int, int, int, bytes, LaneSelection]] = []
    for scope_code, scope in enumerate(TILE_SCOPES):
        tile_size = len(words) if scope == 0 else scope
        tile_blobs: list[bytes] = []
        modes: list[int] = []
        for start in range(0, len(words), tile_size):
            tile_blob, mode = _best_tile(words[start : start + tile_size])
            tile_blobs.append(tile_blob)
            modes.append(mode)
        payload = b"".join(tile_blobs)
        blob = LANE_HEADER.pack(
            scope_code, 0, 0, len(words), len(tile_blobs), len(payload)
        ) + payload
        selection = LaneSelection(scope, len(tile_blobs), tuple(modes))
        candidates.append((len(blob), len(tile_blobs), scope_code, blob, selection))
    _, _, _, blob, selection = min(candidates, key=lambda item: item[:3])
    return blob, selection


def encode_uleb(value: int) -> bytes:
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


def _validate_lod_expansion(
    active_lod_counts: tuple[int, ...], rows: tuple[tuple[int, int, int], ...]
) -> None:
    if not active_lod_counts or any(count == 0 for count in active_lod_counts):
        raise ValueError("active LOD vertex count is zero")
    if any(left < right for left, right in zip(active_lod_counts, active_lod_counts[1:])):
        raise ValueError("active LOD counts are not non-increasing")
    for root_lod, declared in enumerate(active_lod_counts):
        expanded = sum(count for lod, _source, count in rows if lod >= root_lod)
        if expanded != declared:
            raise ValueError("fixup expansion differs from active LOD count")


def _classify_layout(
    *,
    source_size: int,
    vertex_start: int,
    tangent_start: int,
    tangent_end: int,
    fixup_start: int,
    fixup_end: int,
) -> str:
    if fixup_start == _VVD_HEADER.size and fixup_end == vertex_start:
        if tangent_end != source_size:
            raise ValueError("disjoint tangent extent does not fill source")
        return "disjoint"
    if vertex_start != _VVD_HEADER.size:
        raise ValueError("physical ownership has an unsupported vertex start")
    if fixup_start == tangent_start and fixup_end <= tangent_end and tangent_end == source_size:
        return "overlay"
    if (
        tangent_start <= fixup_start < tangent_end
        and tangent_end < fixup_end
        and fixup_end == source_size
    ):
        return "suffix_overlay_tail"
    raise ValueError("physical ownership does not match a supported layout")


def _owned_ranges(
    *,
    source_size: int,
    vertex_start: int,
    tangent_start: int,
    tangent_end: int,
    fixup_start: int,
    fixup_end: int,
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = [
        (0, _VVD_HEADER.size),
        (vertex_start, tangent_start),
        (fixup_start, fixup_end),
    ]
    if tangent_start < min(fixup_start, tangent_end):
        ranges.append((tangent_start, min(fixup_start, tangent_end)))
    if max(fixup_end, tangent_start) < tangent_end:
        ranges.append((max(fixup_end, tangent_start), tangent_end))
    if any(start < 0 or end <= start or end > source_size or start % 4 or end % 4 for start, end in ranges):
        raise ValueError("physical ownership contains an invalid range")
    return tuple(ranges)


def _validate_one_owner(source_size: int, ranges: tuple[tuple[int, int], ...]) -> None:
    events: list[tuple[int, int]] = []
    for start, end in ranges:
        events.extend(((start, 1), (end, -1)))
    positions = sorted({position for position, _delta in events})
    for left, right in zip(positions, positions[1:]):
        if left == right:
            continue
        owners = sum(1 for start, end in ranges if start <= left and right <= end)
        if owners != 1:
            raise ValueError("physical ownership is not exactly one owner per byte")
    if not positions or positions[0] != 0 or positions[-1] != source_size:
        raise ValueError("physical ownership does not cover the complete source")


def _parse_source(source: bytes) -> dict:
    if not isinstance(source, bytes):
        raise ValueError("VVD source must be immutable bytes")
    if len(source) < _VVD_HEADER.size:
        raise ValueError("truncated VVD header")
    fields = _VVD_HEADER.unpack_from(source)
    magic, version, _checksum, num_lods = fields[:4]
    fixup_count, fixup_start, vertex_start, tangent_start = fields[-4:]
    if magic != b"IDSV" or version != 4:
        raise ValueError("unsupported VVD header")
    if not 1 <= num_lods <= 8:
        raise ValueError("invalid VVD LOD count")
    if fixup_count <= 0:
        raise ValueError("P1 requires at least one fixup row")
    if any(offset % 4 for offset in (fixup_start, vertex_start, tangent_start)):
        raise ValueError("physical ownership offsets are not word aligned")
    if tangent_start <= vertex_start or (tangent_start - vertex_start) % 48:
        raise ValueError("physical ownership base extent is invalid")
    physical_count = (tangent_start - vertex_start) // 48
    if not 0 < physical_count <= 0xFFFFFFFF:
        raise ValueError("invalid physical vertex count")
    tangent_end = tangent_start + physical_count * 16
    fixup_end = fixup_start + fixup_count * _FIXUP.size
    if tangent_end > len(source) or fixup_end > len(source):
        raise ValueError("physical ownership range exceeds source")
    layout = _classify_layout(
        source_size=len(source),
        vertex_start=vertex_start,
        tangent_start=tangent_start,
        tangent_end=tangent_end,
        fixup_start=fixup_start,
        fixup_end=fixup_end,
    )
    ranges = _owned_ranges(
        source_size=len(source),
        vertex_start=vertex_start,
        tangent_start=tangent_start,
        tangent_end=tangent_end,
        fixup_start=fixup_start,
        fixup_end=fixup_end,
    )
    _validate_one_owner(len(source), ranges)
    rows = tuple(
        _FIXUP.unpack_from(source, fixup_start + index * _FIXUP.size)
        for index in range(fixup_count)
    )
    for index, (_lod, row_source, count) in enumerate(rows):
        if count <= 0:
            raise ValueError(f"fixup {index} has zero count")
        if row_source >= physical_count or count > physical_count - row_source:
            raise ValueError(f"fixup {index} exceeds physical bounds")
    _validate_lod_expansion(tuple(fields[4 : 4 + num_lods]), rows)
    return {
        "header": source[: _VVD_HEADER.size],
        "physical_count": physical_count,
        "fixup_count": fixup_count,
        "num_lods": num_lods,
        "vertex_start": vertex_start,
        "tangent_start": tangent_start,
        "tangent_end": tangent_end,
        "fixup_start": fixup_start,
        "fixup_end": fixup_end,
        "layout": layout,
        "rows": rows,
    }


def _word_is_fixup_owned(offset: int, *, fixup_start: int, fixup_end: int) -> bool:
    return fixup_start <= offset and offset + 4 <= fixup_end


def _encode_fixup_deltas(
    rows: tuple[tuple[int, int, int], ...], *, physical_count: int
) -> bytes:
    output = bytearray()
    previous_end = 0
    for index, (_lod, source, count) in enumerate(rows):
        if count <= 0 or source >= physical_count or count > physical_count - source:
            raise ValueError(f"fixup {index} exceeds physical bounds")
        output.extend(encode_uleb(_zigzag(source - previous_end)))
        output.extend(encode_uleb(count))
        previous_end = source + count
    return bytes(output)


def encode_vvd(source: bytes) -> EncodeResult:
    """Encode one supported VVD while treating every four-byte word opaquely."""
    parsed = _parse_source(source)
    physical_count = parsed["physical_count"]
    vertex_start = parsed["vertex_start"]
    tangent_start = parsed["tangent_start"]
    fixup_start = parsed["fixup_start"]
    fixup_end = parsed["fixup_end"]

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
        lane_blob, selection = encode_lane(words)
        lane_blobs.append(lane_blob)
        selections.append(selection)

    for lane in range(4):
        words = tuple(
            source[offset : offset + 4]
            for cell in range(physical_count)
            for offset in (tangent_start + cell * 16 + lane * 4,)
            if not _word_is_fixup_owned(
                offset, fixup_start=fixup_start, fixup_end=fixup_end
            )
        )
        lane_blob, selection = encode_lane(words)
        lane_blobs.append(lane_blob)
        selections.append(selection)

    lod_words = tuple(struct.pack("<I", row[0]) for row in parsed["rows"])
    lod_lane, lod_selection = encode_lane(lod_words)
    selections.append(lod_selection)
    delta_payload = _encode_fixup_deltas(
        parsed["rows"], physical_count=physical_count
    )
    fixed_header = CONTAINER_HEADER.pack(
        MAGIC,
        VERSION,
        FIXED_HEADER_SIZE,
        0,
        len(source),
        physical_count,
        parsed["fixup_count"],
        parsed["num_lods"],
        vertex_start,
        tangent_start,
        parsed["tangent_end"],
        fixup_start,
        fixup_end,
        LAYOUT_CODES[parsed["layout"]],
        12,
        4,
        17,
        0,
        len(lod_lane),
        len(delta_payload),
        parsed["header"],
        hashlib.sha256(source).digest(),
        b"\0" * 20,
    )
    blob = fixed_header + b"".join(lane_blobs) + lod_lane + delta_payload
    return EncodeResult(blob, tuple(selections), parsed["layout"])

