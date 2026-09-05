"""Independent bounded decoder for the exact three-layout VVD P1 container."""

from __future__ import annotations

import hashlib
import struct

from .format import (
    CONTAINER_HEADER,
    FIXED_HEADER_SIZE,
    LANE_HEADER,
    LAYOUT_NAMES,
    MAGIC,
    MODE_CONST,
    MODE_DICT,
    MODE_RAW,
    TILE_HEADER,
    TILE_SCOPES,
    TRUSTED_P1_MAXIMUM_OUTPUT,
    VERSION,
    ContainerSummary,
    LaneSelection,
)


_VVD_HEADER = struct.Struct("<4sIII8I4I")
_FIXUP = struct.Struct("<III")


def _tile_extent(data: bytes, offset: int) -> int:
    if offset < 0 or len(data) - offset < TILE_HEADER.size:
        raise ValueError("truncated tile header")
    payload_bytes = TILE_HEADER.unpack_from(data, offset)[5]
    end = offset + TILE_HEADER.size + payload_bytes
    if end > len(data):
        raise ValueError("truncated tile payload")
    return end


def decode_tile(blob: bytes, *, expected_words: int) -> bytes:
    if len(blob) < TILE_HEADER.size:
        raise ValueError("truncated tile header")
    mode, index_bits, reserved, word_count, dictionary_count, payload_bytes = TILE_HEADER.unpack_from(blob)
    if reserved != 0 or word_count != expected_words or word_count == 0:
        raise ValueError("malformed tile header")
    if payload_bytes != len(blob) - TILE_HEADER.size:
        raise ValueError("tile payload length or trailing bytes")
    payload = memoryview(blob)[TILE_HEADER.size:]
    if mode == MODE_CONST:
        if index_bits or dictionary_count or payload_bytes != 4:
            raise ValueError("malformed CONST tile")
        return bytes(payload) * word_count
    if mode == MODE_RAW:
        if index_bits or dictionary_count or payload_bytes != word_count * 4:
            raise ValueError("malformed RAW tile")
        return bytes(payload)
    if mode != MODE_DICT:
        raise ValueError("unknown tile mode")
    if dictionary_count < 2 or index_bits != (dictionary_count - 1).bit_length():
        raise ValueError("malformed DICT header")
    index_bytes = (word_count * index_bits + 7) // 8
    if payload_bytes != dictionary_count * 4 + index_bytes:
        raise ValueError("malformed DICT payload length")
    dictionary_blob = bytes(payload[: dictionary_count * 4])
    dictionary = tuple(
        dictionary_blob[offset : offset + 4]
        for offset in range(0, len(dictionary_blob), 4)
    )
    keys = tuple(int.from_bytes(word, "little") for word in dictionary)
    if tuple(sorted(set(keys))) != keys:
        raise ValueError("DICT words are not sorted unique unsigned patterns")
    packed = payload[dictionary_count * 4 :]
    output = bytearray()
    bit_offset = 0
    for _ in range(word_count):
        index = 0
        for bit in range(index_bits):
            if packed[bit_offset >> 3] & (1 << (bit_offset & 7)):
                index |= 1 << bit
            bit_offset += 1
        if index >= dictionary_count:
            raise ValueError("DICT index is out of range")
        output.extend(dictionary[index])
    if bit_offset & 7:
        padding_mask = ~((1 << (bit_offset & 7)) - 1) & 0xFF
        if packed[-1] & padding_mask:
            raise ValueError("non-zero DICT padding bits")
    return bytes(output)


def _lane_extent(data: bytes, offset: int) -> int:
    if offset < 0 or len(data) - offset < LANE_HEADER.size:
        raise ValueError("truncated lane header")
    payload_bytes = LANE_HEADER.unpack_from(data, offset)[5]
    end = offset + LANE_HEADER.size + payload_bytes
    if end > len(data):
        raise ValueError("truncated lane payload")
    return end


def decode_lane(blob: bytes, *, expected_words: int) -> tuple[bytes, LaneSelection]:
    if len(blob) < LANE_HEADER.size:
        raise ValueError("truncated lane header")
    scope_code, reserved8, reserved16, word_count, tile_count, payload_bytes = LANE_HEADER.unpack_from(blob)
    if scope_code >= len(TILE_SCOPES) or reserved8 or reserved16:
        raise ValueError("malformed lane header")
    if word_count != expected_words or word_count == 0 or tile_count == 0:
        raise ValueError("lane word or tile count differs")
    if payload_bytes != len(blob) - LANE_HEADER.size:
        raise ValueError("lane payload length or trailing bytes")
    scope = TILE_SCOPES[scope_code]
    expected_tiles = 1 if scope == 0 else (word_count + scope - 1) // scope
    if tile_count != expected_tiles:
        raise ValueError("lane tile count differs from scope")
    output = bytearray()
    modes: list[int] = []
    offset = LANE_HEADER.size
    remaining = word_count
    for _ in range(tile_count):
        end = _tile_extent(blob, offset)
        mode = TILE_HEADER.unpack_from(blob, offset)[0]
        tile_words = remaining if scope == 0 else min(scope, remaining)
        output.extend(decode_tile(blob[offset:end], expected_words=tile_words))
        modes.append(mode)
        remaining -= tile_words
        offset = end
    if offset != len(blob) or remaining:
        raise ValueError("lane did not consume all bytes and words")
    return bytes(output), LaneSelection(scope, tile_count, tuple(modes))


def decode_uleb(data: bytes, offset: int, *, maximum: int) -> tuple[int, int]:
    if offset < 0 or maximum < 0:
        raise ValueError("invalid ULEB offset or bound")
    start = offset
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated ULEB")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if value > maximum:
            raise ValueError("ULEB overflow")
        if not byte & 0x80:
            if offset - start > 1 and byte == 0:
                raise ValueError("non-canonical ULEB")
            return value, offset
        shift += 7
        if shift > max(7, maximum.bit_length() + 6):
            raise ValueError("ULEB overflow")


def _unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _classify_layout(
    *, source_size: int, vertex_start: int, tangent_start: int, tangent_end: int,
    fixup_start: int, fixup_end: int,
) -> str:
    if fixup_start == _VVD_HEADER.size and fixup_end == vertex_start and tangent_end == source_size:
        return "disjoint"
    if vertex_start != _VVD_HEADER.size:
        raise ValueError("physical ownership has an unsupported vertex start")
    if fixup_start == tangent_start and fixup_end <= tangent_end and tangent_end == source_size:
        return "overlay"
    if tangent_start <= fixup_start < tangent_end < fixup_end == source_size:
        return "suffix_overlay_tail"
    raise ValueError("physical ownership does not match a supported layout")


def _owned_ranges(
    *, source_size: int, vertex_start: int, tangent_start: int, tangent_end: int,
    fixup_start: int, fixup_end: int,
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
    positions = sorted({position for extent in ranges for position in extent})
    for left, right in zip(positions, positions[1:]):
        owners = sum(1 for start, end in ranges if start <= left and right <= end)
        if owners != 1:
            raise ValueError("physical ownership is not exactly one owner per byte")
    if not positions or positions[0] != 0 or positions[-1] != source_size:
        raise ValueError("physical ownership does not cover complete source")


def _word_is_fixup_owned(offset: int, *, fixup_start: int, fixup_end: int) -> bool:
    return fixup_start <= offset and offset + 4 <= fixup_end


def _parse_header(blob: bytes, *, maximum_output: int) -> dict:
    if maximum_output < _VVD_HEADER.size:
        raise ValueError("invalid output bound")
    if len(blob) < FIXED_HEADER_SIZE:
        raise ValueError("truncated container header")
    fields = CONTAINER_HEADER.unpack_from(blob)
    (
        magic, version, header_size, flags, source_size, physical_count,
        fixup_count, num_lods, vertex_start, tangent_start, tangent_end,
        fixup_start, fixup_end, layout_code, base_lanes, tangent_lanes,
        lane_count, reserved16, lod_lane_bytes, delta_bytes, original_header,
        source_sha, padding,
    ) = fields
    if magic != MAGIC or version != VERSION or header_size != FIXED_HEADER_SIZE:
        raise ValueError("unknown P1 container header")
    if flags or reserved16 or any(padding):
        raise ValueError("non-zero reserved container field")
    if source_size > maximum_output:
        raise ValueError("declared source exceeds output bound")
    if source_size < _VVD_HEADER.size or physical_count == 0 or fixup_count == 0:
        raise ValueError("invalid declared source geometry")
    if not 1 <= num_lods <= 8:
        raise ValueError("invalid declared LOD count")
    if layout_code not in LAYOUT_NAMES:
        raise ValueError("unknown layout code")
    if (base_lanes, tangent_lanes, lane_count) != (12, 4, 17):
        raise ValueError("unexpected lane contract")
    if lod_lane_bytes < LANE_HEADER.size or delta_bytes == 0:
        raise ValueError("invalid fixup payload lengths")
    if any(offset % 4 for offset in (vertex_start, tangent_start, tangent_end, fixup_start, fixup_end)):
        raise ValueError("physical ownership offsets are not word aligned")
    if tangent_start <= vertex_start or tangent_start - vertex_start != physical_count * 48:
        raise ValueError("physical ownership base extent differs")
    if tangent_end - tangent_start != physical_count * 16:
        raise ValueError("physical ownership tangent extent differs")
    if fixup_end - fixup_start != fixup_count * _FIXUP.size:
        raise ValueError("physical ownership fixup extent differs")
    layout = _classify_layout(
        source_size=source_size,
        vertex_start=vertex_start,
        tangent_start=tangent_start,
        tangent_end=tangent_end,
        fixup_start=fixup_start,
        fixup_end=fixup_end,
    )
    if LAYOUT_NAMES[layout_code] != layout:
        raise ValueError("declared layout differs from physical offsets")
    ranges = _owned_ranges(
        source_size=source_size,
        vertex_start=vertex_start,
        tangent_start=tangent_start,
        tangent_end=tangent_end,
        fixup_start=fixup_start,
        fixup_end=fixup_end,
    )
    _validate_one_owner(source_size, ranges)
    source_fields = _VVD_HEADER.unpack(original_header)
    if source_fields[0] != b"IDSV" or source_fields[1] != 4:
        raise ValueError("stored VVD header is invalid")
    if source_fields[3] != num_lods or source_fields[-4:] != (
        fixup_count, fixup_start, vertex_start, tangent_start
    ):
        raise ValueError("stored VVD header differs from container metadata")
    return {
        "source_size": source_size,
        "physical_count": physical_count,
        "fixup_count": fixup_count,
        "num_lods": num_lods,
        "vertex_start": vertex_start,
        "tangent_start": tangent_start,
        "tangent_end": tangent_end,
        "fixup_start": fixup_start,
        "fixup_end": fixup_end,
        "layout": layout,
        "lod_lane_bytes": lod_lane_bytes,
        "delta_bytes": delta_bytes,
        "original_header": original_header,
        "source_sha": source_sha,
        "source_fields": source_fields,
    }


def _decode_internal(blob: bytes, *, maximum_output: int) -> tuple[bytes, dict, tuple[LaneSelection, ...]]:
    meta = _parse_header(blob, maximum_output=maximum_output)
    offset = FIXED_HEADER_SIZE
    decoded_lanes: list[bytes] = []
    selections: list[LaneSelection] = []
    for lane in range(12):
        end = _lane_extent(blob, offset)
        words, selection = decode_lane(
            blob[offset:end], expected_words=meta["physical_count"]
        )
        decoded_lanes.append(words)
        selections.append(selection)
        offset = end
    tangent_offsets: list[tuple[int, ...]] = []
    for lane in range(4):
        positions = tuple(
            meta["tangent_start"] + cell * 16 + lane * 4
            for cell in range(meta["physical_count"])
            if not _word_is_fixup_owned(
                meta["tangent_start"] + cell * 16 + lane * 4,
                fixup_start=meta["fixup_start"],
                fixup_end=meta["fixup_end"],
            )
        )
        if not positions:
            raise ValueError("tangent ownership leaves an empty component lane")
        end = _lane_extent(blob, offset)
        words, selection = decode_lane(blob[offset:end], expected_words=len(positions))
        decoded_lanes.append(words)
        selections.append(selection)
        tangent_offsets.append(positions)
        offset = end
    lod_end = offset + meta["lod_lane_bytes"]
    if lod_end > len(blob):
        raise ValueError("truncated fixup LOD lane")
    lod_words, selection = decode_lane(
        blob[offset:lod_end], expected_words=meta["fixup_count"]
    )
    selections.append(selection)
    offset = lod_end
    delta_end = offset + meta["delta_bytes"]
    if delta_end > len(blob):
        raise ValueError("truncated fixup delta payload")
    delta_payload = blob[offset:delta_end]
    if delta_end != len(blob):
        raise ValueError("container has trailing bytes")

    rows: list[tuple[int, int, int]] = []
    delta_offset = 0
    previous_end = 0
    for index in range(meta["fixup_count"]):
        encoded_delta, delta_offset = decode_uleb(
            delta_payload, delta_offset, maximum=0x1FFFFFFFF
        )
        count, delta_offset = decode_uleb(
            delta_payload, delta_offset, maximum=meta["physical_count"]
        )
        source = previous_end + _unzigzag(encoded_delta)
        if count <= 0 or source < 0 or source >= meta["physical_count"] or count > meta["physical_count"] - source:
            raise ValueError(f"decoded fixup {index} exceeds physical bounds")
        lod = struct.unpack_from("<I", lod_words, index * 4)[0]
        rows.append((lod, source, count))
        previous_end = source + count
    if delta_offset != len(delta_payload):
        raise ValueError("fixup delta payload has trailing bytes")
    active_lods = tuple(meta["source_fields"][4 : 4 + meta["num_lods"]])
    if any(count == 0 for count in active_lods) or any(
        left < right for left, right in zip(active_lods, active_lods[1:])
    ):
        raise ValueError("invalid active LOD counts")
    for root, declared in enumerate(active_lods):
        if sum(count for lod, _source, count in rows if lod >= root) != declared:
            raise ValueError("decoded fixup expansion differs from LOD count")

    output = bytearray(meta["source_size"])
    output[: _VVD_HEADER.size] = meta["original_header"]
    for lane in range(12):
        lane_bytes = decoded_lanes[lane]
        for cell in range(meta["physical_count"]):
            target = meta["vertex_start"] + cell * 48 + lane * 4
            output[target : target + 4] = lane_bytes[cell * 4 : cell * 4 + 4]
    for lane, positions in enumerate(tangent_offsets):
        lane_bytes = decoded_lanes[12 + lane]
        for index, target in enumerate(positions):
            output[target : target + 4] = lane_bytes[index * 4 : index * 4 + 4]
    for index, row in enumerate(rows):
        _FIXUP.pack_into(output, meta["fixup_start"] + index * _FIXUP.size, *row)
    reconstructed = bytes(output)
    if hashlib.sha256(reconstructed).digest() != meta["source_sha"]:
        raise ValueError("reconstructed source SHA-256 differs")
    return reconstructed, meta, tuple(selections)


def decode_vvd(
    blob: bytes, *, maximum_output: int = TRUSTED_P1_MAXIMUM_OUTPUT
) -> bytes:
    if not isinstance(blob, bytes):
        raise ValueError("container must be immutable bytes")
    reconstructed, _meta, _selections = _decode_internal(
        blob, maximum_output=maximum_output
    )
    return reconstructed


def inspect_container(
    blob: bytes, *, maximum_output: int = TRUSTED_P1_MAXIMUM_OUTPUT
) -> ContainerSummary:
    _reconstructed, meta, selections = _decode_internal(
        blob, maximum_output=maximum_output
    )
    return ContainerSummary(
        source_size=meta["source_size"],
        physical_count=meta["physical_count"],
        fixup_count=meta["fixup_count"],
        layout=meta["layout"],
        blob_size=len(blob),
        lane_selections=selections,
    )

