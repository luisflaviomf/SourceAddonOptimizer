"""Binary definitions for the three-layout scalar/tiled exact VVD P1 codec."""

from __future__ import annotations

from dataclasses import dataclass
import struct


CODEC_DOMAIN = "vvd-scalar-tiled-fixup-delta-exact-p1/v1"
MAGIC = b"CVVDP101"
VERSION = 1
FIXED_HEADER_SIZE = 192
TRUSTED_P1_MAXIMUM_OUTPUT = 64 * 1024 * 1024

MODE_CONST = 0
MODE_RAW = 1
MODE_DICT = 2

LAYOUT_OVERLAY = 1
LAYOUT_SUFFIX_OVERLAY_TAIL = 2
LAYOUT_DISJOINT = 3
LAYOUT_NAMES = {
    LAYOUT_OVERLAY: "overlay",
    LAYOUT_SUFFIX_OVERLAY_TAIL: "suffix_overlay_tail",
    LAYOUT_DISJOINT: "disjoint",
}
LAYOUT_CODES = {name: code for code, name in LAYOUT_NAMES.items()}

TILE_SCOPES = (0, 128, 512, 2_048, 8_192, 32_768, 65_536)

# mode/scope, index-bits/reserved, reserved, word-count, secondary-count,
# payload byte count. Both records are exactly 16 bytes.
TILE_HEADER = struct.Struct("<BBHIII")
LANE_HEADER = struct.Struct("<BBHIII")

# magic, version/header size/flags, source size, nine u32 source/layout fields,
# four lane counters, two payload lengths, exact original VVD header, source
# digest, and zero padding. The source/layout fields are, in order:
# physical_count, fixup_count, num_lods, vertex_start, tangent_start,
# tangent_end, fixup_start, fixup_end, layout_code.
CONTAINER_HEADER = struct.Struct("<8sHHIQ9I4HII64s32s20s")

assert TILE_HEADER.size == 16
assert LANE_HEADER.size == 16
assert CONTAINER_HEADER.size == FIXED_HEADER_SIZE


@dataclass(frozen=True)
class LaneSelection:
    scope: int
    tile_count: int
    tile_modes: tuple[int, ...]


@dataclass(frozen=True)
class EncodeResult:
    blob: bytes
    lane_selections: tuple[LaneSelection, ...]
    layout: str


@dataclass(frozen=True)
class ContainerSummary:
    source_size: int
    physical_count: int
    fixup_count: int
    layout: str
    blob_size: int
    lane_selections: tuple[LaneSelection, ...]

