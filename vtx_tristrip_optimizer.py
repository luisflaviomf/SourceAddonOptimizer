from __future__ import annotations

import collections
import ctypes
import dataclasses
import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Iterable, Sequence


TRI_LIST = 0x01
TRI_STRIP = 0x02
STRIPIFIER_SHA256 = "A20DADA9544A7217B148BA73D0784041104DE08D26F7DD1AFFE6B7338807AF57"

FILE_HEADER_SIZE = 36
MATERIAL_LIST_SIZE = 8
MATERIAL_REPLACEMENT_SIZE = 6
BODY_PART_SIZE = 8
MODEL_SIZE = 8
LOD_SIZE = 12
MESH_SIZE = 9
STRIP_GROUP_SIZE = 25
VERTEX_SIZE = 9
STRIP_SIZE = 27
BONE_STATE_CHANGE_SIZE = 8


class VtxOptimizeError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


@dataclasses.dataclass(frozen=True)
class Fixup:
    field: int
    base: int
    target: int
    kind: str


@dataclasses.dataclass(frozen=True)
class Strip:
    base: int
    num_indices: int
    index_offset: int
    num_vertices: int
    vertex_offset: int
    num_bones: int
    flags: int
    num_bone_changes: int
    bone_change_offset: int


@dataclasses.dataclass(frozen=True)
class StripGroup:
    base: int
    flags: int
    vertices: tuple[bytes, ...]
    indices: tuple[int, ...]
    strips: tuple[Strip, ...]


@dataclasses.dataclass(frozen=True)
class ParsedVtx:
    version: int
    checksum: int
    lod_count: int
    body_part_count: int
    strip_groups: tuple[StripGroup, ...]


@dataclasses.dataclass(frozen=True)
class VtxOptimizeReport:
    source_bytes: int
    candidate_bytes: int
    saved_bytes: int
    source_index_bytes: int
    candidate_index_bytes: int
    saved_index_bytes: int
    converted_groups: int
    converted_strips: int
    source_sha256: str
    candidate_sha256: str
    recovered_triangle_list: tuple[int, ...] = dataclasses.field(repr=False)


@dataclasses.dataclass(frozen=True)
class VtxTreeSummary:
    scanned_files: int
    optimized_files: int
    already_tristrip_files: int
    skipped_files: int
    failed_files: int
    source_bytes: int
    candidate_bytes: int
    saved_bytes: int
    source_index_bytes: int
    candidate_index_bytes: int
    saved_index_bytes: int
    files: tuple[dict[str, object], ...] = dataclasses.field(repr=False)


class VtxReader:
    def __init__(self, path: Path, data: bytes | None = None) -> None:
        self.path = path
        self.data = path.read_bytes() if data is None else data
        self.spans: list[Span] = []
        self.fixups: list[Fixup] = []
        self.strip_groups: list[StripGroup] = []

    def require(self, offset: int, size: int, context: str) -> None:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise VtxOptimizeError(
                f"{self.path}: {context} out of bounds: "
                f"offset={offset}, size={size}, file={len(self.data)}"
            )

    def mark(self, offset: int, size: int, kind: str) -> None:
        self.require(offset, size, kind)
        if size:
            self.spans.append(Span(offset, offset + size, kind))

    def pointer(self, field: int, base: int, target: int, kind: str) -> None:
        self.require(field, 4, f"{kind} pointer field")
        self.require(base, 1, f"{kind} pointer base")
        self.require(target, 1, f"{kind} pointer target")
        self.fixups.append(Fixup(field, base, target, kind))

    def unpack(self, fmt: str, offset: int, context: str) -> tuple[object, ...]:
        size = struct.calcsize(fmt)
        self.require(offset, size, context)
        return struct.unpack_from(fmt, self.data, offset)

    @staticmethod
    def sane_count(value: int, context: str, maximum: int = 10_000_000) -> int:
        if value < 0 or value > maximum:
            raise VtxOptimizeError(f"unreasonable {context}: {value}")
        return value

    def cstring(self, offset: int, context: str) -> bytes:
        self.require(offset, 1, context)
        end = self.data.find(b"\0", offset)
        if end < 0:
            raise VtxOptimizeError(f"{self.path}: unterminated {context} at {offset}")
        value = self.data[offset : end + 1]
        self.mark(offset, len(value), "material_name")
        return value

    def parse(self) -> ParsedVtx:
        self.mark(0, FILE_HEADER_SIZE, "file_header")
        (
            version,
            _vertex_cache_size,
            _max_bones_per_strip,
            _max_bones_per_tri,
            _max_bones_per_vertex,
            checksum,
            lod_count,
            material_list_offset,
            body_part_count,
            body_part_offset,
        ) = self.unpack("<iiHHiiiiii", 0, "file header")
        version = int(version)
        checksum = int(checksum)
        lod_count = int(lod_count)
        body_part_count = int(body_part_count)
        material_list_offset = int(material_list_offset)
        body_part_offset = int(body_part_offset)
        if version != 7:
            raise VtxOptimizeError(f"unsupported VTX version {version}")
        self.sane_count(lod_count, "LOD count", 256)
        self.sane_count(body_part_count, "body-part count", 100_000)

        if lod_count:
            if material_list_offset <= 0:
                raise VtxOptimizeError("nonzero LOD count with no material-list offset")
            self.pointer(24, 0, material_list_offset, "material_lists")
            self._parse_material_lists(material_list_offset, lod_count)
        if body_part_count:
            if body_part_offset <= 0:
                raise VtxOptimizeError("nonzero body-part count with no body-part offset")
            self.pointer(32, 0, body_part_offset, "body_parts")
            self._parse_body_parts(body_part_offset, body_part_count)

        return ParsedVtx(
            version=version,
            checksum=checksum,
            lod_count=lod_count,
            body_part_count=body_part_count,
            strip_groups=tuple(self.strip_groups),
        )

    def _parse_material_lists(self, base: int, count: int) -> None:
        self.mark(base, count * MATERIAL_LIST_SIZE, "material_list_headers")
        for index in range(count):
            item = base + index * MATERIAL_LIST_SIZE
            replacement_count, replacement_offset = self.unpack(
                "<ii", item, f"material list {index}"
            )
            replacement_count = int(replacement_count)
            replacement_offset = int(replacement_offset)
            self.sane_count(replacement_count, "material replacement count", 100_000)
            if not replacement_count:
                continue
            if not replacement_offset:
                raise VtxOptimizeError("material replacements have zero offset")
            replacement_base = item + replacement_offset
            self.pointer(item + 4, item, replacement_base, "material_replacements")
            self.mark(
                replacement_base,
                replacement_count * MATERIAL_REPLACEMENT_SIZE,
                "material_replacement_headers",
            )
            for replacement_index in range(replacement_count):
                header = replacement_base + replacement_index * MATERIAL_REPLACEMENT_SIZE
                _material_id, name_offset = self.unpack(
                    "<hi", header, f"material replacement {replacement_index}"
                )
                name_offset = int(name_offset)
                if name_offset:
                    self.pointer(header + 2, header, header + name_offset, "material_name")
                    self.cstring(header + name_offset, "replacement material name")

    def _parse_body_parts(self, base: int, count: int) -> None:
        self.mark(base, count * BODY_PART_SIZE, "body_part_headers")
        for body_index in range(count):
            body = base + body_index * BODY_PART_SIZE
            model_count, model_offset = self.unpack("<ii", body, f"body {body_index}")
            model_count = int(model_count)
            model_offset = int(model_offset)
            self.sane_count(model_count, "model count", 100_000)
            if not model_count:
                continue
            if not model_offset:
                raise VtxOptimizeError("models have zero offset")
            models = body + model_offset
            self.pointer(body + 4, body, models, "models")
            self.mark(models, model_count * MODEL_SIZE, "model_headers")
            for model_index in range(model_count):
                self._parse_model(models + model_index * MODEL_SIZE, body_index, model_index)

    def _parse_model(self, base: int, body_index: int, model_index: int) -> None:
        lod_count, lod_offset = self.unpack(
            "<ii", base, f"body {body_index} model {model_index}"
        )
        lod_count = int(lod_count)
        lod_offset = int(lod_offset)
        self.sane_count(lod_count, "model LOD count", 256)
        if not lod_count:
            return
        if not lod_offset:
            raise VtxOptimizeError("model LODs have zero offset")
        lods = base + lod_offset
        self.pointer(base + 4, base, lods, "lods")
        self.mark(lods, lod_count * LOD_SIZE, "lod_headers")
        for lod_index in range(lod_count):
            lod = lods + lod_index * LOD_SIZE
            mesh_count, mesh_offset, _switch_point = self.unpack(
                "<iif", lod, f"body {body_index} model {model_index} LOD {lod_index}"
            )
            mesh_count = int(mesh_count)
            mesh_offset = int(mesh_offset)
            self.sane_count(mesh_count, "mesh count", 1_000_000)
            if not mesh_count:
                continue
            if not mesh_offset:
                raise VtxOptimizeError("meshes have zero offset")
            meshes = lod + mesh_offset
            self.pointer(lod + 4, lod, meshes, "meshes")
            self.mark(meshes, mesh_count * MESH_SIZE, "mesh_headers")
            for mesh_index in range(mesh_count):
                self._parse_mesh(
                    meshes + mesh_index * MESH_SIZE,
                    (body_index, model_index, lod_index, mesh_index),
                )

    def _parse_mesh(self, base: int, location: tuple[int, int, int, int]) -> None:
        strip_group_count, strip_group_offset, _flags = self.unpack(
            "<iiB", base, f"mesh {location}"
        )
        strip_group_count = int(strip_group_count)
        strip_group_offset = int(strip_group_offset)
        self.sane_count(strip_group_count, "strip-group count", 1_000_000)
        if not strip_group_count:
            return
        if not strip_group_offset:
            raise VtxOptimizeError("strip groups have zero offset")
        groups = base + strip_group_offset
        self.pointer(base + 4, base, groups, "strip_groups")
        self.mark(groups, strip_group_count * STRIP_GROUP_SIZE, "strip_group_headers")
        for group_index in range(strip_group_count):
            self._parse_strip_group(
                groups + group_index * STRIP_GROUP_SIZE,
                (*location, group_index),
            )

    def _parse_strip_group(self, base: int, location: tuple[int, ...]) -> None:
        (
            vertex_count,
            vertex_offset,
            index_count,
            index_offset,
            strip_count,
            strip_offset,
            flags,
        ) = self.unpack("<iiiiiiB", base, f"strip group {location}")
        vertex_count = int(vertex_count)
        vertex_offset = int(vertex_offset)
        index_count = int(index_count)
        index_offset = int(index_offset)
        strip_count = int(strip_count)
        strip_offset = int(strip_offset)
        flags = int(flags)
        self.sane_count(vertex_count, "strip-group vertex count")
        self.sane_count(index_count, "strip-group index count")
        self.sane_count(strip_count, "strip count")
        if vertex_count and not vertex_offset:
            raise VtxOptimizeError("vertices have zero offset")
        if index_count and not index_offset:
            raise VtxOptimizeError("indices have zero offset")
        if strip_count and not strip_offset:
            raise VtxOptimizeError("strips have zero offset")

        vertex_base = base + vertex_offset if vertex_count else base
        index_base = base + index_offset if index_count else base
        strip_base = base + strip_offset if strip_count else base
        if vertex_count:
            self.pointer(base + 4, base, vertex_base, "vertices")
        if index_count:
            self.pointer(base + 12, base, index_base, "indices")
        if strip_count:
            self.pointer(base + 20, base, strip_base, "strips")
        self.mark(vertex_base, vertex_count * VERTEX_SIZE, "vertices")
        self.mark(index_base, index_count * 2, "indices")
        self.mark(strip_base, strip_count * STRIP_SIZE, "strip_headers")

        vertices = tuple(
            self.data[offset : offset + VERTEX_SIZE]
            for offset in range(vertex_base, vertex_base + vertex_count * VERTEX_SIZE, VERTEX_SIZE)
        )
        indices = (
            tuple(struct.unpack_from(f"<{index_count}H", self.data, index_base))
            if index_count
            else ()
        )
        for value in indices:
            if value >= vertex_count:
                raise VtxOptimizeError(
                    f"strip group {location}: index {value} >= vertex count {vertex_count}"
                )

        strips: list[Strip] = []
        for strip_index in range(strip_count):
            strip = strip_base + strip_index * STRIP_SIZE
            (
                num_indices,
                local_index_offset,
                num_vertices,
                local_vertex_offset,
                num_bones,
                strip_flags,
                bone_change_count,
                bone_change_offset,
            ) = self.unpack("<iiiihBii", strip, f"strip {location + (strip_index,)}")
            num_indices = int(num_indices)
            local_index_offset = int(local_index_offset)
            num_vertices = int(num_vertices)
            local_vertex_offset = int(local_vertex_offset)
            num_bones = int(num_bones)
            strip_flags = int(strip_flags)
            bone_change_count = int(bone_change_count)
            bone_change_offset = int(bone_change_offset)
            self.sane_count(num_indices, "strip index count")
            self.sane_count(num_vertices, "strip vertex count")
            self.sane_count(bone_change_count, "bone-state-change count")
            if local_index_offset < 0 or local_index_offset + num_indices > index_count:
                raise VtxOptimizeError(f"strip {location + (strip_index,)} index range out of bounds")
            if local_vertex_offset < 0 or local_vertex_offset + num_vertices > vertex_count:
                raise VtxOptimizeError(f"strip {location + (strip_index,)} vertex range out of bounds")
            local_indices = indices[local_index_offset : local_index_offset + num_indices]
            if local_indices and (
                min(local_indices) < local_vertex_offset
                or max(local_indices) >= local_vertex_offset + num_vertices
            ):
                raise VtxOptimizeError(
                    f"strip {location + (strip_index,)} references outside its vertex range"
                )
            if bone_change_count:
                if not bone_change_offset:
                    raise VtxOptimizeError("bone-state changes have zero offset")
                bone_change_base = strip + bone_change_offset
                self.pointer(strip + 23, strip, bone_change_base, "bone_state_changes")
                self.mark(
                    bone_change_base,
                    bone_change_count * BONE_STATE_CHANGE_SIZE,
                    "bone_state_changes",
                )
            strips.append(
                Strip(
                    base=strip,
                    num_indices=num_indices,
                    index_offset=local_index_offset,
                    num_vertices=num_vertices,
                    vertex_offset=local_vertex_offset,
                    num_bones=num_bones,
                    flags=strip_flags,
                    num_bone_changes=bone_change_count,
                    bone_change_offset=bone_change_offset,
                )
            )

        self.strip_groups.append(
            StripGroup(
                base=base,
                flags=flags,
                vertices=vertices,
                indices=indices,
                strips=tuple(strips),
            )
        )


class _Stripifier:
    def __init__(self, dll_path: Path) -> None:
        dll_path = dll_path.resolve()
        if not dll_path.is_file():
            raise VtxOptimizeError(f"meshoptimizer stripifier DLL not found: {dll_path}")
        actual_sha256 = hashlib.sha256(dll_path.read_bytes()).hexdigest().upper()
        if actual_sha256 != STRIPIFIER_SHA256:
            raise VtxOptimizeError(
                "meshoptimizer stripifier SHA-256 does not match the GMod-tested build: "
                f"expected={STRIPIFIER_SHA256}, actual={actual_sha256}, path={dll_path}"
            )
        try:
            self.dll = ctypes.CDLL(str(dll_path))
        except OSError as error:
            raise VtxOptimizeError(f"could not load stripifier DLL {dll_path}: {error}") from error
        uint_pointer = ctypes.POINTER(ctypes.c_uint)
        self.dll.probe_stripify_bound.argtypes = [ctypes.c_size_t]
        self.dll.probe_stripify_bound.restype = ctypes.c_size_t
        self.dll.probe_stripify.argtypes = [
            uint_pointer,
            uint_pointer,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        self.dll.probe_stripify.restype = ctypes.c_size_t
        self.dll.probe_unstripify_bound.argtypes = [ctypes.c_size_t]
        self.dll.probe_unstripify_bound.restype = ctypes.c_size_t
        self.dll.probe_unstripify.argtypes = [uint_pointer, uint_pointer, ctypes.c_size_t]
        self.dll.probe_unstripify.restype = ctypes.c_size_t

    @staticmethod
    def _array(values: Sequence[int]) -> ctypes.Array[ctypes.c_uint]:
        array_type = ctypes.c_uint * len(values)
        return array_type(*(int(value) for value in values))

    def stripify(self, indices: Sequence[int], vertex_count: int) -> tuple[int, ...]:
        if not indices:
            return ()
        source = self._array(indices)
        bound = int(self.dll.probe_stripify_bound(len(indices)))
        destination_type = ctypes.c_uint * bound
        destination = destination_type()
        count = int(self.dll.probe_stripify(destination, source, len(indices), vertex_count))
        result = tuple(int(destination[index]) for index in range(count))
        if any(value > 0xFFFF for value in result):
            raise VtxOptimizeError("stripified index does not fit Source VTX uint16 storage")
        return result

    def unstripify(self, indices: Sequence[int]) -> tuple[int, ...]:
        if not indices:
            return ()
        source = self._array(indices)
        bound = int(self.dll.probe_unstripify_bound(len(indices)))
        destination_type = ctypes.c_uint * bound
        destination = destination_type()
        count = int(self.dll.probe_unstripify(destination, source, len(indices)))
        return tuple(int(destination[index]) for index in range(count))


def validate_stripifier(dll_path: Path) -> None:
    """Fail early when the pinned native helper is absent, altered, or unloadable."""

    _Stripifier(dll_path)


def canonical_oriented_triangle(triangle: Sequence[int]) -> tuple[int, int, int]:
    if len(triangle) != 3:
        raise VtxOptimizeError(f"triangle must have exactly three indices: {triangle!r}")
    a, b, c = (int(value) for value in triangle)
    return min((a, b, c), (b, c, a), (c, a, b))


def oriented_triangle_counter(
    indices: Sequence[int],
) -> collections.Counter[tuple[int, int, int]]:
    if len(indices) % 3:
        raise VtxOptimizeError(
            f"triangle-list index count is not divisible by three: {len(indices)}"
        )
    result: collections.Counter[tuple[int, int, int]] = collections.Counter()
    for offset in range(0, len(indices), 3):
        triangle = tuple(int(value) for value in indices[offset : offset + 3])
        if len(set(triangle)) < 3:
            continue
        result[canonical_oriented_triangle(triangle)] += 1
    return result


def _unique_blocks(reader: VtxReader) -> list[Span]:
    by_range: dict[tuple[int, int, str], Span] = {}
    for span in reader.spans:
        by_range.setdefault((span.start, span.end, span.kind), span)
    blocks = sorted(by_range.values(), key=lambda item: (item.start, item.end, item.kind))
    for previous, current in zip(blocks, blocks[1:]):
        if current.start < previous.end:
            raise VtxOptimizeError(
                "overlapping reachable blocks are not rewrite-safe: "
                f"{previous.kind}@{previous.start}:{previous.end} and "
                f"{current.kind}@{current.start}:{current.end}"
            )
    return blocks


def _containing_block(blocks: Sequence[Span], address: int, size: int = 1) -> Span:
    matches = [
        block
        for block in blocks
        if block.start <= address and address + size <= block.end
    ]
    if len(matches) != 1:
        raise VtxOptimizeError(
            f"address {address}:{address + size} belongs to {len(matches)} reachable blocks"
        )
    return matches[0]


def _replace_block_bytes(
    source: bytes,
    blocks: Sequence[Span],
    replacements: dict[Span, bytes],
    address: int,
    data: bytes,
) -> None:
    block = _containing_block(blocks, address, len(data))
    blob = bytearray(replacements.get(block, source[block.start : block.end]))
    relative = address - block.start
    blob[relative : relative + len(data)] = data
    replacements[block] = bytes(blob)


def _serialize_relocated(
    source: bytes,
    reader: VtxReader,
    blocks: Sequence[Span],
    replacements: dict[Span, bytes],
) -> bytes:
    payload_kinds = frozenset({"vertices", "indices", "bone_state_changes", "material_name"})
    poolable_kinds = frozenset({"vertices", "indices", "bone_state_changes"})
    layout_order = sorted(
        blocks,
        key=lambda item: (item.kind in payload_kinds, item.start, item.end, item.kind),
    )
    output = bytearray()
    new_starts: dict[Span, int] = {}
    pooled: dict[tuple[str, bytes], int] = {}
    for block in layout_order:
        blob = replacements.get(block, source[block.start : block.end])
        key = (block.kind, blob)
        if block.kind in poolable_kinds and key in pooled:
            new_starts[block] = pooled[key]
            continue
        new_starts[block] = len(output)
        output.extend(blob)
        if block.kind in poolable_kinds:
            pooled[key] = new_starts[block]

    def relocate(address: int, size: int = 1) -> int:
        block = _containing_block(blocks, address, size)
        return new_starts[block] + (address - block.start)

    for fixup in reader.fixups:
        new_field = relocate(fixup.field, 4)
        new_base = relocate(fixup.base)
        new_target = relocate(fixup.target)
        relative = new_target - new_base
        if not -(2**31) <= relative < 2**31:
            raise VtxOptimizeError(f"{fixup.kind} relocation does not fit int32: {relative}")
        struct.pack_into("<i", output, new_field, relative)
    return bytes(output)


def _all_strips(parsed: ParsedVtx) -> list[tuple[StripGroup, Strip]]:
    return [(group, strip) for group in parsed.strip_groups for strip in group.strips]


def optimize_vtx_bytes(
    source: bytes,
    dll_path: Path,
    *,
    _stripifier: _Stripifier | None = None,
) -> tuple[bytes, VtxOptimizeReport]:
    stripifier = _stripifier or _Stripifier(dll_path)
    reader = VtxReader(Path("<source>"), source)
    parsed = reader.parse()
    source_strips = _all_strips(parsed)
    if not source_strips:
        raise VtxOptimizeError("VTX contains no strips")
    if any(strip.flags != TRI_LIST for _group, strip in source_strips):
        flags = sorted({strip.flags for _group, strip in source_strips})
        raise VtxOptimizeError(f"VTX is not an all-TRI_LIST input; flags={flags}")

    blocks = _unique_blocks(reader)
    replacements: dict[Span, bytes] = {}
    source_index_bytes = 0
    candidate_index_bytes = 0
    recovered_flat: list[int] = []
    source_topology: list[collections.Counter[tuple[int, int, int]]] = []
    converted_strips = 0
    converted_groups = 0

    for group in parsed.strip_groups:
        if not group.indices:
            continue
        converted_groups += 1
        new_indices: list[int] = []
        for strip in group.strips:
            original = tuple(
                group.indices[strip.index_offset : strip.index_offset + strip.num_indices]
            )
            converted = stripifier.stripify(original, len(group.vertices))
            recovered = stripifier.unstripify(converted)
            topology = oriented_triangle_counter(original)
            if topology != oriented_triangle_counter(recovered):
                raise VtxOptimizeError(f"oriented topology changed for strip at {strip.base}")
            source_topology.append(topology)
            recovered_flat.extend(recovered)

            new_offset = len(new_indices)
            new_indices.extend(converted)
            _replace_block_bytes(
                source, blocks, replacements, strip.base, struct.pack("<i", len(converted))
            )
            _replace_block_bytes(
                source, blocks, replacements, strip.base + 4, struct.pack("<i", new_offset)
            )
            _replace_block_bytes(
                source, blocks, replacements, strip.base + 18, bytes([TRI_STRIP])
            )
            source_index_bytes += len(original) * 2
            candidate_index_bytes += len(converted) * 2
            converted_strips += 1

        _replace_block_bytes(
            source, blocks, replacements, group.base + 8, struct.pack("<i", len(new_indices))
        )
        old_index_offset = struct.unpack_from("<i", source, group.base + 12)[0]
        old_index_base = group.base + old_index_offset
        old_index_size = len(group.indices) * 2
        index_block = _containing_block(blocks, old_index_base, old_index_size)
        packed = struct.pack(f"<{len(new_indices)}H", *new_indices) if new_indices else b""
        previous = replacements.get(index_block)
        if previous is not None and previous != packed:
            raise VtxOptimizeError("shared source index block produced inconsistent tristrips")
        replacements[index_block] = packed

    candidate = _serialize_relocated(source, reader, blocks, replacements)
    candidate_parsed = VtxReader(Path("<candidate>"), candidate).parse()
    candidate_strips = _all_strips(candidate_parsed)
    if len(candidate_strips) != converted_strips:
        raise VtxOptimizeError("candidate strip count changed after serialization")
    if any(strip.flags != TRI_STRIP for _group, strip in candidate_strips):
        raise VtxOptimizeError("candidate contains a non-TRI_STRIP strip")
    for index, (group, strip) in enumerate(candidate_strips):
        encoded = group.indices[strip.index_offset : strip.index_offset + strip.num_indices]
        if oriented_triangle_counter(stripifier.unstripify(encoded)) != source_topology[index]:
            raise VtxOptimizeError(f"candidate topology changed after serialization at strip {index}")
    if len(candidate) >= len(source):
        raise VtxOptimizeError(
            f"TRI_STRIP candidate did not shrink: {len(source)} -> {len(candidate)}"
        )

    return candidate, VtxOptimizeReport(
        source_bytes=len(source),
        candidate_bytes=len(candidate),
        saved_bytes=len(source) - len(candidate),
        source_index_bytes=source_index_bytes,
        candidate_index_bytes=candidate_index_bytes,
        saved_index_bytes=source_index_bytes - candidate_index_bytes,
        converted_groups=converted_groups,
        converted_strips=converted_strips,
        source_sha256=hashlib.sha256(source).hexdigest(),
        candidate_sha256=hashlib.sha256(candidate).hexdigest(),
        recovered_triangle_list=tuple(recovered_flat),
    )


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _summary_document(summary: VtxTreeSummary) -> dict[str, object]:
    return {
        "schema": "gmod-addon-optimizer-vtx-tristrip-v1",
        "summary": {
            key: value
            for key, value in dataclasses.asdict(summary).items()
            if key != "files"
        },
        "files": list(summary.files),
    }


def optimize_tree(
    root: Path,
    dll_path: Path,
    *,
    report_path: Path | None = None,
) -> VtxTreeSummary:
    root = root.resolve()
    if not root.is_dir():
        raise VtxOptimizeError(f"VTX optimization root is not a directory: {root}")
    stripifier = _Stripifier(dll_path)
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.name.casefold().endswith(".dx90.vtx")
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    records: list[dict[str, object]] = []
    optimized = 0
    already = 0
    skipped = 0
    failed = 0
    source_bytes = 0
    candidate_bytes = 0
    source_index_bytes = 0
    candidate_index_bytes = 0

    for path in paths:
        relative = path.relative_to(root).as_posix()
        before = path.read_bytes()
        try:
            parsed = VtxReader(path, before).parse()
            strips = _all_strips(parsed)
            if strips and all(strip.flags == TRI_STRIP for _group, strip in strips):
                already += 1
                records.append({"path": relative, "status": "already_tristrip", "bytes": len(before)})
                continue
            if not strips:
                skipped += 1
                records.append({"path": relative, "status": "skipped", "reason": "no_strips"})
                continue
            candidate, report = optimize_vtx_bytes(
                before, dll_path, _stripifier=stripifier
            )
            _write_atomic(path, candidate)
            if path.read_bytes() != candidate:
                raise VtxOptimizeError("transactional write verification failed")
            optimized += 1
            source_bytes += report.source_bytes
            candidate_bytes += report.candidate_bytes
            source_index_bytes += report.source_index_bytes
            candidate_index_bytes += report.candidate_index_bytes
            record = dataclasses.asdict(report)
            record.pop("recovered_triangle_list", None)
            records.append({"path": relative, "status": "optimized", **record})
        except Exception as error:
            failed += 1
            if path.read_bytes() != before:
                _write_atomic(path, before)
            records.append(
                {
                    "path": relative,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    summary = VtxTreeSummary(
        scanned_files=len(paths),
        optimized_files=optimized,
        already_tristrip_files=already,
        skipped_files=skipped,
        failed_files=failed,
        source_bytes=source_bytes,
        candidate_bytes=candidate_bytes,
        saved_bytes=source_bytes - candidate_bytes,
        source_index_bytes=source_index_bytes,
        candidate_index_bytes=candidate_index_bytes,
        saved_index_bytes=source_index_bytes - candidate_index_bytes,
        files=tuple(records),
    )
    if report_path is not None:
        document = json.dumps(_summary_document(summary), indent=2, ensure_ascii=False).encode("utf-8")
        _write_atomic(report_path.resolve(), document + b"\n")
    return summary


__all__ = [
    "TRI_LIST",
    "TRI_STRIP",
    "STRIPIFIER_SHA256",
    "ParsedVtx",
    "VtxOptimizeError",
    "VtxOptimizeReport",
    "VtxReader",
    "VtxTreeSummary",
    "canonical_oriented_triangle",
    "optimize_tree",
    "optimize_vtx_bytes",
    "oriented_triangle_counter",
    "validate_stripifier",
]
