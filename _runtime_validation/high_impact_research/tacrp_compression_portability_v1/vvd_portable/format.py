"""Binary contract for the separately versioned portable VVD codec."""

from __future__ import annotations

from dataclasses import dataclass
import struct


CODEC_DOMAIN = "vvd-scalar-tiled-fixup-delta-exact-portable/v1"
MAGIC = b"CVVDPT01"
VERSION = 1
FIXED_HEADER_SIZE = 192
TRUSTED_MAXIMUM_OUTPUT = 64 * 1024 * 1024

LAYOUT_CONTIGUOUS_NO_FIXUP = 1
LAYOUT_DISJOINT_PADDED = 2
LAYOUT_NAMES = {
    LAYOUT_CONTIGUOUS_NO_FIXUP: "contiguous_no_fixup",
    LAYOUT_DISJOINT_PADDED: "disjoint_padded",
}

# Frozen P1 self-delimiting lane header, repeated without importing P1 format.
LANE_HEADER = struct.Struct("<BBHIII")

# magic, version, header size, flags, source size, eleven layout u32 fields,
# four lane-count u16 fields, two payload sizes, exact original VVD header,
# source digest, and canonical zero tail.
CONTAINER_HEADER = struct.Struct("<8sHHIQ11I4HII64s32s12s")

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
    codec_domain: str = CODEC_DOMAIN


@dataclass(frozen=True)
class ContainerSummary:
    source_size: int
    physical_count: int
    fixup_count: int
    layout: str
    blob_size: int
    lane_selections: tuple[LaneSelection, ...]
    codec_domain: str = CODEC_DOMAIN

