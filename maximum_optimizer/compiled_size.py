from __future__ import annotations

import struct
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .domain import ArtifactStat, CompiledSizeSnapshot

SOURCE_SUFFIXES = (".mdl", ".vvd", ".vtx", ".ani", ".phy")
VVD_HEADER = struct.Struct("<4siii8i")


def _kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".vtx"):
        stem = name[:-4]
        variant = stem.rsplit(".", 1)[-1] if "." in stem else "plain"
        return f".{variant}.vtx" if variant != "plain" else ".vtx"
    return path.suffix.lower()


def read_vvd_lod_vertices(path: Path) -> tuple[int, ...]:
    try:
        data = path.read_bytes()[: VVD_HEADER.size]
        ident, version, _checksum, lod_count, *lods = VVD_HEADER.unpack(data)
    except (OSError, struct.error):
        return ()
    if ident != b"IDSV" or version <= 0 or not 1 <= lod_count <= 8:
        return ()
    return tuple(max(0, int(value)) for value in lods[:lod_count])


def scan_compiled_models(models_dir: Path) -> CompiledSizeSnapshot:
    root = models_dir.resolve()
    artifacts = []
    bytes_by_kind = defaultdict(int)
    vertices_by_lod = defaultdict(int)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        kind = _kind(path)
        lods = read_vvd_lod_vertices(path) if kind == ".vvd" else ()
        size = path.stat().st_size
        artifacts.append(ArtifactStat(path.relative_to(root).as_posix(), kind, size, lods))
        bytes_by_kind[kind] += size
        for index, count in enumerate(lods):
            vertices_by_lod[index] += count
    return CompiledSizeSnapshot(
        root=root,
        total_bytes=sum(item.size_bytes for item in artifacts),
        bytes_by_kind=dict(sorted(bytes_by_kind.items())),
        vertices_by_lod=dict(sorted(vertices_by_lod.items())),
        artifacts=tuple(artifacts),
    )


def compare_snapshots(
    original: CompiledSizeSnapshot,
    control: CompiledSizeSnapshot,
    selected: CompiledSizeSnapshot,
    optional_removed_kinds: Iterable[str] = (".dx80.vtx",),
) -> dict[str, int | float]:
    roundtrip_delta_bytes = control.total_bytes - original.total_bytes
    optional_removed_bytes = sum(
        max(
            0,
            control.bytes_by_kind.get(kind, 0)
            - selected.bytes_by_kind.get(kind, 0),
        )
        for kind in optional_removed_kinds
    )
    geometric_delta_bytes = (
        control.total_bytes - selected.total_bytes - optional_removed_bytes
    )
    total_saving_bytes = original.total_bytes - selected.total_bytes

    return {
        "roundtrip_delta_bytes": roundtrip_delta_bytes,
        "roundtrip_delta_percent": (
            roundtrip_delta_bytes / original.total_bytes * 100
            if original.total_bytes
            else 0.0
        ),
        "optional_removed_bytes": optional_removed_bytes,
        "optional_removed_percent": (
            optional_removed_bytes / control.total_bytes * 100
            if control.total_bytes
            else 0.0
        ),
        "geometric_delta_bytes": geometric_delta_bytes,
        "geometric_delta_percent": (
            geometric_delta_bytes / control.total_bytes * 100
            if control.total_bytes
            else 0.0
        ),
        "total_saving_bytes": total_saving_bytes,
        "total_saving_percent": (
            total_saving_bytes / original.total_bytes * 100
            if original.total_bytes
            else 0.0
        ),
    }
