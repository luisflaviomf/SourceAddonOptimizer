"""Read-only engine collision scanner for selected portability targets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

import vpk


def _digest(handle: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def scan_engine_overlaps(engine_root: Path, entries: list[dict[str, object]]) -> dict[str, object]:
    """Return exact selected-path intersections with an isolated engine tree.

    VPK and loose files are read but never extracted or modified.  Any observed
    identity disagreement is reported as a conflict and is excluded from the
    allow-map.
    """

    root = Path(engine_root)
    expected: dict[str, dict[str, object]] = {}
    for entry in entries:
        path = entry.get("target_path")
        size = entry.get("source_size")
        sha256 = entry.get("source_sha256")
        if (
            not isinstance(path, str)
            or path != path.lower()
            or "\\" in path
            or path in expected
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise ValueError("entries must have unique safe target paths and source identities")
        expected[path] = {"size": size, "sha256": sha256}

    evidence: list[dict[str, object]] = []
    vpk_paths = [
        path for path in sorted(root.rglob("*_dir.vpk"))
        if "addons" not in {part.lower() for part in path.relative_to(root).parts}
    ]
    for vpk_path in vpk_paths:
        archive = vpk.open(str(vpk_path))
        names = {name.lower(): name for name, _ in archive.items() if name.lower() in expected}
        for normalized, stored_name in sorted(names.items()):
            packed = archive.get_file(stored_name)
            try:
                size, sha256 = _digest(packed)
                crc_ok = packed.verify()
                index_crc = f"{packed.crc32:08x}"
            finally:
                packed.close()
            identity = expected[normalized]
            evidence.append({
                "path": normalized,
                "vpk": vpk_path.relative_to(root).as_posix(),
                "index_crc": index_crc,
                "size": size,
                "sha256": sha256,
                "vpk_crc_ok": crc_ok,
                "matches_entry": crc_ok and size == identity["size"] and sha256 == identity["sha256"],
            })

    loose_roots = [root / name for name in ("garrysmod", "platform", "sourceengine")]
    for loose_root in loose_roots:
        for target, identity in expected.items():
            path = loose_root.joinpath(*target.split("/"))
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                size, sha256 = _digest(handle)
            evidence.append({
                "path": target,
                "vpk": None,
                "index_crc": None,
                "size": size,
                "sha256": sha256,
                "vpk_crc_ok": None,
                "matches_entry": size == identity["size"] and sha256 == identity["sha256"],
            })

    evidence.sort(key=lambda item: (str(item["path"]), str(item["vpk"] or "")))
    observed: dict[str, list[dict[str, object]]] = {}
    for item in evidence:
        observed.setdefault(str(item["path"]), []).append(item)
    exact = {
        path: expected[path]
        for path, records in sorted(observed.items())
        if all(item["matches_entry"] is True for item in records)
    }
    conflicts = sorted(
        path for path, records in observed.items()
        if any(item["matches_entry"] is not True for item in records)
    )
    return {
        "engine_preexisting": exact,
        "evidence": evidence,
        "conflicts": conflicts,
        "scanned": {
            "engine_root": str(root.resolve()),
            "selected_targets": len(expected),
            "vpk_count": len(vpk_paths),
            "loose_root_count": len(loose_roots),
            "intersection_records": len(evidence),
            "overlapping_targets": len(observed),
            "exact_targets": len(exact),
            "conflict_targets": len(conflicts),
        },
    }
