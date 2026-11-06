from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import struct


_COUNT_NAMES = (
    "bones",
    "bone_controllers",
    "hitbox_sets",
    "animations",
    "sequences",
    "textures",
    "cd_textures",
    "skin_references",
    "skin_families",
    "bodyparts",
    "attachments",
    "nodes",
)
_COUNT_INDICES = (0, 2, 4, 6, 8, 12, 14, 16, 17, 19, 21, 23)


@dataclass(frozen=True)
class _MdlHeader:
    version: int
    checksum: int
    counts: tuple[tuple[str, int], ...]
    materials: tuple[str, ...]
    skin_families: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(frozen=True)
class CompiledFamilyExpectation:
    mdl_relative: PurePosixPath
    mdl_version: int
    counts: tuple[tuple[str, int], ...]
    materials: tuple[str, ...]
    skin_families: tuple[tuple[tuple[str, str], ...], ...]
    require_phy: bool
    require_ani: bool


@dataclass(frozen=True)
class CompiledValidation:
    passed: bool
    failures: tuple[str, ...]
    checksum: int | None
    lod0_vertices: int | None


def _family_path(root: Path, relative: PurePosixPath) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".mdl":
        raise ValueError("compiled family path must be a relative MDL")
    return Path(root) / Path(*path.parts)


def _cstring(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("ascii", errors="ignore")


def _canonical_skin_families(
    data: bytes,
    raw_counts: tuple[int, ...],
    materials: tuple[str, ...],
) -> tuple[tuple[tuple[str, str], ...], ...]:
    reference_count = raw_counts[16]
    family_count = raw_counts[17]
    table_index = raw_counts[18]
    if reference_count == 0 or family_count == 0:
        return ()
    entry_count = reference_count * family_count
    if table_index <= 0 or table_index + entry_count * 2 > len(data):
        raise ValueError("mdl_skin_table")
    values = struct.unpack_from(f"<{entry_count}H", data, table_index)
    if any(index >= len(materials) for index in values):
        raise ValueError("mdl_skin_material")
    rows = tuple(
        values[offset : offset + reference_count]
        for offset in range(0, entry_count, reference_count)
    )
    base = rows[0]
    return tuple(
        tuple(
            sorted(
                (
                    materials[base[column]].casefold(),
                    materials[row[column]].casefold(),
                )
                for column in range(reference_count)
            )
        )
        for row in rows
    )


def _read_mdl(path: Path) -> _MdlHeader:
    data = path.read_bytes()
    if len(data) < 252 or data[:4] != b"IDST":
        raise ValueError("mdl_header")
    version, checksum = struct.unpack_from("<2i", data, 4)
    if version not in (48, 49):
        raise ValueError("mdl_version")
    declared_length = struct.unpack_from("<i", data, 76)[0]
    if declared_length <= 0 or declared_length > len(data):
        raise ValueError("mdl_length")
    raw_counts = struct.unpack_from("<24i", data, 156)
    counts = tuple((name, raw_counts[index]) for name, index in zip(_COUNT_NAMES, _COUNT_INDICES))
    if any(value < 0 for _name, value in counts):
        raise ValueError("mdl_negative_count")
    texture_count = raw_counts[12]
    texture_index = raw_counts[13]
    materials = []
    if texture_count and texture_index > 0:
        for index in range(texture_count):
            base = texture_index + index * 64
            if base + 4 > len(data):
                raise ValueError("mdl_texture_table")
            name_index = struct.unpack_from("<i", data, base)[0]
            materials.append(_cstring(data, base + name_index) if name_index > 0 else "")
    material_tuple = tuple(materials)
    return _MdlHeader(
        version,
        checksum,
        counts,
        material_tuple,
        _canonical_skin_families(data, raw_counts, material_tuple),
    )


def _read_vvd(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b"IDSV":
        raise ValueError("vvd_header")
    version, checksum, lod_count = struct.unpack_from("<3i", data, 4)
    if version != 4 or not 1 <= lod_count <= 8:
        raise ValueError("vvd_version_or_lods")
    lod_vertices = struct.unpack_from("<8i", data, 16)
    lod0 = lod_vertices[0]
    if lod0 < 0 or any(value < 0 or value > lod0 for value in lod_vertices[:lod_count]):
        raise ValueError("vvd_vertex_bounds")
    _fixups, _fixup_start, vertex_start, tangent_start = struct.unpack_from("<4i", data, 48)
    if vertex_start < 64 or tangent_start < vertex_start:
        raise ValueError("vvd_offsets")
    if vertex_start + lod0 * 48 > len(data) or tangent_start + lod0 * 16 > len(data):
        raise ValueError("vvd_data_bounds")
    return checksum, lod0


def _read_vtx_checksum(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("vtx_header")
    version = struct.unpack_from("<i", data, 0)[0]
    if version not in (7,):
        raise ValueError("vtx_version")
    return struct.unpack_from("<i", data, 16)[0]


def expected_compiled_family(models_root: Path, mdl_relative: PurePosixPath) -> CompiledFamilyExpectation:
    mdl_path = _family_path(models_root, mdl_relative)
    header = _read_mdl(mdl_path)
    return CompiledFamilyExpectation(
        PurePosixPath(mdl_relative),
        header.version,
        header.counts,
        tuple(value.casefold() for value in header.materials),
        header.skin_families,
        mdl_path.with_suffix(".phy").is_file(),
        mdl_path.with_suffix(".ani").is_file(),
    )


def validate_compiled_family(
    models_root: Path,
    expected: CompiledFamilyExpectation,
    *,
    studiomdl_succeeded: bool = True,
) -> CompiledValidation:
    failures: list[str] = []
    mdl_path = _family_path(models_root, expected.mdl_relative)
    vvd_path = mdl_path.with_suffix(".vvd")
    dx90_path = mdl_path.with_suffix(".dx90.vtx")
    if not studiomdl_succeeded:
        failures.append("studiomdl_failed")
    for path, failure in ((mdl_path, "missing_mdl"), (vvd_path, "missing_vvd"), (dx90_path, "missing_dx90")):
        if not path.is_file():
            failures.append(failure)
    checksum = None
    lod0 = None
    if any(value in failures for value in ("missing_mdl", "missing_vvd", "missing_dx90")):
        return CompiledValidation(False, tuple(failures), checksum, lod0)
    try:
        mdl = _read_mdl(mdl_path)
        checksum = mdl.checksum
        actual_counts = dict(mdl.counts)
        expected_counts = dict(expected.counts)
        if actual_counts["animations"] < expected_counts["animations"]:
            failures.append("animations_lost")
        comparable_names = tuple(name for name, _value in expected.counts if name != "animations")
        if any(actual_counts[name] != expected_counts[name] for name in comparable_names):
            failures.append("mdl_inventory_changed")
        actual_materials = Counter(value.casefold() for value in mdl.materials)
        if actual_materials != Counter(expected.materials):
            failures.append("materials_changed")
        if mdl.skin_families != expected.skin_families:
            failures.append("skin_families_changed")
        if actual_counts["bones"] > 128:
            failures.append("source_bone_limit")
    except (OSError, ValueError, struct.error) as exc:
        failures.append(str(exc) or "mdl_invalid")
    try:
        vvd_checksum, lod0 = _read_vvd(vvd_path)
        if checksum is not None and vvd_checksum != checksum:
            failures.append("vvd_checksum_mismatch")
    except (OSError, ValueError, struct.error) as exc:
        failures.append(str(exc) or "vvd_invalid")
    try:
        vtx_checksum = _read_vtx_checksum(dx90_path)
        if checksum is not None and vtx_checksum != checksum:
            failures.append("dx90_checksum_mismatch")
    except (OSError, ValueError, struct.error) as exc:
        failures.append(str(exc) or "dx90_invalid")
    if expected.require_phy and not mdl_path.with_suffix(".phy").is_file():
        failures.append("missing_phy")
    if expected.require_ani and not mdl_path.with_suffix(".ani").is_file():
        failures.append("missing_ani")
    return CompiledValidation(not failures, tuple(dict.fromkeys(failures)), checksum, lod0)
