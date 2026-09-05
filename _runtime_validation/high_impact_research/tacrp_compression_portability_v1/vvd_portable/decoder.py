"""Independent bounded decoder for the portable VVD container."""

from __future__ import annotations

import hashlib
import struct

from _runtime_validation.high_impact_research.vvd_scalar_tiled_fixup_delta_exact_p1.decoder import (
    decode_lane as _decode_lane,
    decode_uleb as _decode_uleb,
)

from .format import (
    CONTAINER_HEADER,
    FIXED_HEADER_SIZE,
    LANE_HEADER,
    LAYOUT_CONTIGUOUS_NO_FIXUP,
    LAYOUT_DISJOINT_PADDED,
    MAGIC,
    TRUSTED_MAXIMUM_OUTPUT,
    VERSION,
    ContainerSummary,
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


def _lane_extent(data: bytes, offset: int) -> int:
    if offset < 0 or len(data) - offset < LANE_HEADER.size:
        raise ValueError("truncated lane header")
    payload_bytes = LANE_HEADER.unpack_from(data, offset)[5]
    end = offset + LANE_HEADER.size + payload_bytes
    if end > len(data):
        raise ValueError("truncated lane payload")
    return end


def _unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


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


def _validate_owned_ranges(
    source_size: int, ranges: tuple[tuple[int, int], ...]
) -> None:
    cursor = 0
    for start, end in ranges:
        if start != cursor or end <= start or end > source_size:
            raise ValueError("physical ownership is not exactly one owner per byte")
        cursor = end
    if cursor != source_size:
        raise ValueError("physical ownership does not end at EOF")


def _parse_header(blob: bytes, *, maximum_output: int) -> dict[str, object]:
    if type(maximum_output) is not int or maximum_output < _VVD_HEADER.size:
        raise ValueError("invalid output bound")
    if len(blob) < FIXED_HEADER_SIZE:
        raise ValueError("truncated container header")
    fields = CONTAINER_HEADER.unpack_from(blob)
    (
        magic,
        version,
        header_size,
        flags,
        source_size,
        physical_count,
        fixup_count,
        num_lods,
        vertex_start,
        tangent_start,
        tangent_end,
        fixup_start,
        fixup_end,
        padding_start,
        padding_length,
        layout_code,
        base_lane_count,
        tangent_lane_count,
        lod_lane_count,
        reserved16,
        lod_lane_bytes,
        delta_bytes,
        original_header,
        source_sha,
        reserved_tail,
    ) = fields
    if magic == b"CVVDP101":
        raise ValueError("foreign VVD codec domain")
    if magic != MAGIC or version != VERSION or header_size != FIXED_HEADER_SIZE:
        raise ValueError("unknown portable VVD container header")
    if flags or reserved16 or any(reserved_tail):
        raise ValueError("non-zero reserved container field")
    if source_size > maximum_output:
        raise ValueError("declared source exceeds output bound")
    if source_size < _VVD_HEADER.size or physical_count == 0:
        raise ValueError("invalid declared source geometry")
    if not 1 <= num_lods <= 8:
        raise ValueError("invalid declared LOD count")
    if (base_lane_count, tangent_lane_count) != (12, 4):
        raise ValueError("unexpected physical lane contract")
    if any(
        offset % 4
        for offset in (
            vertex_start,
            tangent_start,
            tangent_end,
            fixup_start,
            fixup_end,
            padding_start,
        )
    ):
        raise ValueError("physical ownership offsets are not word aligned")
    if tangent_start <= vertex_start or tangent_start - vertex_start != physical_count * 48:
        raise ValueError("physical ownership base extent differs")
    if tangent_end - tangent_start != physical_count * 16 or tangent_end != source_size:
        raise ValueError("physical ownership tangent extent differs or does not end at EOF")

    if layout_code == LAYOUT_CONTIGUOUS_NO_FIXUP:
        if (
            fixup_count != 0
            or fixup_start != _VVD_HEADER.size
            or fixup_end != _VVD_HEADER.size
            or vertex_start != _VVD_HEADER.size
            or padding_start != 0
            or padding_length != 0
            or lod_lane_count != 0
            or lod_lane_bytes != 0
            or delta_bytes != 0
        ):
            raise ValueError("contiguous no-fixup metadata is not canonical")
        layout = "contiguous_no_fixup"
        ranges = ((0, 64), (vertex_start, tangent_start), (tangent_start, tangent_end))
    elif layout_code == LAYOUT_DISJOINT_PADDED:
        if fixup_count == 0 or fixup_start != _VVD_HEADER.size:
            raise ValueError("disjoint fixup metadata is absent")
        if fixup_end - fixup_start != fixup_count * _FIXUP.size:
            raise ValueError("physical ownership fixup extent differs")
        if padding_length not in _LEGAL_PADDING_LENGTHS:
            raise ValueError("disjoint padding length is not 4, 8, or 12")
        if padding_start != fixup_end or vertex_start != padding_start + padding_length:
            raise ValueError("disjoint padding ownership differs")
        if lod_lane_count != 1 or lod_lane_bytes < LANE_HEADER.size or delta_bytes == 0:
            raise ValueError("disjoint LOD/delta payload contract differs")
        layout = "disjoint_padded"
        ranges = (
            (0, 64),
            (fixup_start, fixup_end),
            (padding_start, vertex_start),
            (vertex_start, tangent_start),
            (tangent_start, tangent_end),
        )
    else:
        raise ValueError("unknown portable layout code")
    _validate_owned_ranges(source_size, ranges)

    source_fields = _VVD_HEADER.unpack(original_header)
    if source_fields[0] != b"IDSV" or source_fields[1] != 4:
        raise ValueError("stored VVD header is invalid")
    if source_fields[3] != num_lods or source_fields[-4:] != (
        fixup_count,
        fixup_start,
        vertex_start,
        tangent_start,
    ):
        raise ValueError("stored VVD header differs from container metadata")
    active_lods = tuple(source_fields[4 : 4 + num_lods])
    _validate_active_lods(active_lods, physical_count=physical_count)
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
        "padding_start": padding_start,
        "padding_length": padding_length,
        "layout": layout,
        "lod_lane_bytes": lod_lane_bytes,
        "delta_bytes": delta_bytes,
        "original_header": original_header,
        "source_sha": source_sha,
        "active_lods": active_lods,
    }


def _decode_internal(
    blob: bytes, *, maximum_output: int
) -> tuple[bytes, dict[str, object], tuple[LaneSelection, ...]]:
    meta = _parse_header(blob, maximum_output=maximum_output)
    physical_count = int(meta["physical_count"])
    offset = FIXED_HEADER_SIZE
    decoded_lanes: list[bytes] = []
    selections: list[LaneSelection] = []
    for _lane in range(16):
        end = _lane_extent(blob, offset)
        words, selected = _decode_lane(blob[offset:end], expected_words=physical_count)
        decoded_lanes.append(words)
        selections.append(_selection(selected))
        offset = end

    rows: list[tuple[int, int, int]] = []
    if meta["layout"] == "disjoint_padded":
        lod_end = offset + int(meta["lod_lane_bytes"])
        if lod_end > len(blob):
            raise ValueError("truncated fixup LOD lane")
        if _lane_extent(blob, offset) != lod_end:
            raise ValueError("fixup LOD lane boundary differs")
        lod_words, selected = _decode_lane(
            blob[offset:lod_end], expected_words=int(meta["fixup_count"])
        )
        selections.append(_selection(selected))
        offset = lod_end
        delta_end = offset + int(meta["delta_bytes"])
        if delta_end > len(blob):
            raise ValueError("truncated fixup delta payload")
        if delta_end != len(blob):
            raise ValueError("container has trailing bytes")
        delta_payload = blob[offset:delta_end]
        delta_offset = 0
        previous_end = 0
        for index in range(int(meta["fixup_count"])):
            encoded_delta, delta_offset = _decode_uleb(
                delta_payload, delta_offset, maximum=0x1FFFFFFFF
            )
            count, delta_offset = _decode_uleb(
                delta_payload, delta_offset, maximum=physical_count
            )
            source_vertex = previous_end + _unzigzag(encoded_delta)
            lod = struct.unpack_from("<I", lod_words, index * 4)[0]
            if lod >= int(meta["num_lods"]):
                raise ValueError(f"decoded fixup {index} LOD exceeds the declared range")
            if (
                count <= 0
                or source_vertex < 0
                or source_vertex >= physical_count
                or count > physical_count - source_vertex
            ):
                raise ValueError(f"decoded fixup {index} exceeds physical bounds")
            rows.append((lod, source_vertex, count))
            previous_end = source_vertex + count
        if delta_offset != len(delta_payload):
            raise ValueError("fixup delta payload has trailing bytes")
        active_lods = meta["active_lods"]
        if not isinstance(active_lods, tuple):
            raise AssertionError("internal LOD contract")
        for root_lod, declared in enumerate(active_lods):
            expanded = sum(count for lod, _source, count in rows if lod >= root_lod)
            if expanded != declared:
                raise ValueError("decoded fixup expansion differs from LOD count")
    elif offset != len(blob):
        raise ValueError("container has trailing bytes")

    output = bytearray(int(meta["source_size"]))
    original_header = meta["original_header"]
    if not isinstance(original_header, bytes):
        raise AssertionError("internal source-header contract")
    output[:64] = original_header
    vertex_start = int(meta["vertex_start"])
    tangent_start = int(meta["tangent_start"])
    for lane in range(12):
        words = decoded_lanes[lane]
        for cell in range(physical_count):
            target = vertex_start + cell * 48 + lane * 4
            output[target : target + 4] = words[cell * 4 : cell * 4 + 4]
    for lane in range(4):
        words = decoded_lanes[12 + lane]
        for cell in range(physical_count):
            target = tangent_start + cell * 16 + lane * 4
            output[target : target + 4] = words[cell * 4 : cell * 4 + 4]
    fixup_start = int(meta["fixup_start"])
    for index, row in enumerate(rows):
        _FIXUP.pack_into(output, fixup_start + index * 12, *row)
    reconstructed = bytes(output)
    source_sha = meta["source_sha"]
    if not isinstance(source_sha, bytes):
        raise AssertionError("internal source-SHA contract")
    if hashlib.sha256(reconstructed).digest() != source_sha:
        raise ValueError("reconstructed source SHA-256 differs")
    return reconstructed, meta, tuple(selections)


def decode_vvd(
    blob: bytes, *, maximum_output: int = TRUSTED_MAXIMUM_OUTPUT
) -> bytes:
    if type(blob) is not bytes:
        raise ValueError("container must be immutable bytes")
    reconstructed, _meta, _selections = _decode_internal(
        blob, maximum_output=maximum_output
    )
    return reconstructed


def inspect_container(
    blob: bytes, *, maximum_output: int = TRUSTED_MAXIMUM_OUTPUT
) -> ContainerSummary:
    if type(blob) is not bytes:
        raise ValueError("container must be immutable bytes")
    _reconstructed, meta, selections = _decode_internal(
        blob, maximum_output=maximum_output
    )
    return ContainerSummary(
        source_size=int(meta["source_size"]),
        physical_count=int(meta["physical_count"]),
        fixup_count=int(meta["fixup_count"]),
        layout=str(meta["layout"]),
        blob_size=len(blob),
        lane_selections=selections,
    )

