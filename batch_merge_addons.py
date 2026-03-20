from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable


CANONICAL_CONTENT_DIRS = {
    "cfg",
    "data_static",
    "gamemodes",
    "lua",
    "maps",
    "materials",
    "models",
    "particles",
    "resource",
    "scenes",
    "scripts",
    "sound",
}

DEFAULT_GMAD_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\gmad.exe"),
    Path(r"C:\Program Files\Steam\steamapps\common\GarrysMod\bin\gmad.exe"),
]

DEFAULT_COMPAT_IGNORE = ["models/*.sw.vtx"]
STRICT_PACKAGE_INVALID_PATTERNS = tuple(DEFAULT_COMPAT_IGNORE)
SUMMARY_FILE_NAME = "addon_merge_summary.json"


@dataclass(frozen=True)
class AddonManifest:
    exists: bool
    path: Path | None
    title: str | None
    addon_type: str | None
    tags: list[str]
    ignore: list[str]
    parse_error: str | None


@dataclass(frozen=True)
class AddonSource:
    name: str
    root: Path
    detection_reason: str
    discovery_index: int
    manifest: AddonManifest


@dataclass(frozen=True)
class FileRecord:
    addon_name: str
    addon_root: Path
    source_path: Path
    rel_path: str
    rel_key: str
    size: int
    sha256: str
    top_level: str


@dataclass(frozen=True)
class PendingFile:
    addon_name: str
    addon_root: Path
    source_path: Path
    rel_path: str
    rel_key: str
    top_level: str


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _slugify_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "root"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent


def _default_work_dir(root: Path) -> Path:
    return _runtime_root() / "work" / f"{_slugify_name(root.name)}_addonmerge_runs" / _ts()


def _safe_read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _truncate_text(value: str | None, max_chars: int = 1800) -> str | None:
    if not value:
        return value
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]..."


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _normalize_excluded_roots(root: Path, excluded_roots: list[Path] | None) -> list[Path]:
    normalized: list[Path] = []
    for candidate in excluded_roots or []:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved == root or is_descendant(resolved, root):
            normalized.append(resolved)
    return sorted(normalized, key=lambda item: str(item).casefold())


def _should_skip_dir(path: Path, excluded_roots: list[Path]) -> bool:
    return any(path == excluded or is_descendant(path, excluded) for excluded in excluded_roots)


def _filter_walk_dirs(current: Path, dir_names: list[str], excluded_roots: list[Path]) -> None:
    if not excluded_roots:
        return
    kept: list[str] = []
    for name in dir_names:
        candidate = current / name
        if _should_skip_dir(candidate, excluded_roots):
            continue
        kept.append(name)
    dir_names[:] = kept


def _sanitize_bundle_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name.strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "merged_addon"


def _default_bundle_name(root: Path) -> str:
    return f"{_slugify_name(root.name)}_merged"


def _default_output_root(root: Path) -> Path:
    return root


def _resolve_output_paths(root: Path, output_root: str | None, bundle_name: str | None) -> tuple[Path, str, Path, Path]:
    target_root = Path(output_root).expanduser().resolve() if output_root else _default_output_root(root)
    bundle = _sanitize_bundle_name(bundle_name or _default_bundle_name(root))
    merged_root = target_root / bundle
    gma_path = target_root / f"{bundle}.gma"
    return target_root, bundle, merged_root, gma_path


def _default_hash_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(2, min(8, cpu_count))


def _default_copy_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(4, min(16, cpu_count * 2))


def _progress_interval(total: int) -> int:
    if total <= 0:
        return 1
    return max(1, min(100, total // 150 or 1))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(addon_root: Path) -> AddonManifest:
    manifest_path = addon_root / "addon.json"
    if not manifest_path.exists():
        return AddonManifest(False, None, None, None, [], [], None)

    try:
        raw = _safe_read_json(manifest_path)
    except Exception as ex:
        return AddonManifest(True, manifest_path, None, None, [], [], str(ex))

    return AddonManifest(
        True,
        manifest_path,
        str(raw.get("title")) if raw.get("title") is not None else None,
        str(raw.get("type")) if raw.get("type") is not None else None,
        [str(tag) for tag in raw.get("tags", []) if tag is not None],
        [str(pattern) for pattern in raw.get("ignore", []) if pattern is not None],
        None,
    )


def is_descendant(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def detect_content_only_roots(root: Path, excluded_roots: list[Path] | None = None) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    excluded = _normalize_excluded_roots(root, excluded_roots)
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        _filter_walk_dirs(current, dir_names, excluded)
        dir_set = {name.casefold() for name in dir_names}
        if dir_set.intersection(CANONICAL_CONTENT_DIRS):
            candidates.append((current, "canonical-content-dir"))
            dir_names[:] = []
            continue
        if any(name.casefold() in CANONICAL_CONTENT_DIRS for name in file_names):
            candidates.append((current, "canonical-content-file"))
            dir_names[:] = []
    return candidates


def discover_addons(
    root: Path,
    recursive: bool,
    allow_content_only: bool,
    excluded_roots: list[Path] | None = None,
) -> list[AddonSource]:
    manifest_candidates: list[tuple[Path, str]] = []
    if recursive:
        excluded = _normalize_excluded_roots(root, excluded_roots)
        for current_root, dir_names, file_names in os.walk(root):
            current = Path(current_root)
            _filter_walk_dirs(current, dir_names, excluded)
            if "addon.json" in file_names:
                manifest_candidates.append((current, "addon.json"))
                dir_names[:] = []
    else:
        manifest_candidates.extend(
            (entry, "addon.json")
            for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold())
            if entry.is_dir()
            and not _should_skip_dir(entry.resolve(), _normalize_excluded_roots(root, excluded_roots))
            and (entry / "addon.json").exists()
        )

    content_candidates = detect_content_only_roots(root, excluded_roots=excluded_roots) if allow_content_only else []
    combined = sorted(
        manifest_candidates + content_candidates,
        key=lambda pair: (len(pair[0].parts), str(pair[0]).casefold(), pair[1]),
    )

    accepted: list[AddonSource] = []
    for candidate_root, reason in combined:
        if any(is_descendant(candidate_root, existing.root) for existing in accepted):
            continue
        accepted.append(
            AddonSource(
                candidate_root.name,
                candidate_root,
                reason,
                len(accepted),
                load_manifest(candidate_root),
            )
        )
    return accepted


def matches_ignore(pattern: str, rel_path: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/").strip()
    normalized_rel = rel_path.replace("\\", "/")
    rel_name = PurePosixPath(normalized_rel).name

    checks = [
        fnmatch.fnmatchcase(normalized_rel, normalized_pattern),
        fnmatch.fnmatchcase(rel_name, normalized_pattern),
    ]

    dir_pattern = normalized_pattern.rstrip("/")
    if dir_pattern and "/" not in dir_pattern:
        checks.append(normalized_rel.startswith(f"{dir_pattern}/"))
        checks.append(PurePosixPath(normalized_rel).parts[:1] == (dir_pattern,))
    else:
        checks.append(normalized_rel.startswith(f"{dir_pattern}/"))

    return any(checks)


def collect_pending_files(
    addon: AddonSource,
    respect_ignore: bool,
    extra_ignore_patterns: list[str],
) -> tuple[list[PendingFile], list[dict[str, Any]]]:
    pending: list[PendingFile] = []
    ignored: list[dict[str, Any]] = []

    for source_path in sorted(addon.root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not source_path.is_file():
            continue
        rel_path = source_path.relative_to(addon.root).as_posix()
        if rel_path.casefold() == "addon.json":
            continue

        if respect_ignore:
            matched = next((pattern for pattern in addon.manifest.ignore if matches_ignore(pattern, rel_path)), None)
            if matched:
                ignored.append({"rel_path": rel_path, "matched_pattern": matched, "source": "manifest"})
                continue

            matched = next((pattern for pattern in extra_ignore_patterns if matches_ignore(pattern, rel_path)), None)
            if matched:
                ignored.append({"rel_path": rel_path, "matched_pattern": matched, "source": "extra"})
                continue

        pending.append(
            PendingFile(
                addon.name,
                addon.root,
                source_path,
                rel_path,
                rel_path.casefold(),
                rel_path.split("/", 1)[0].casefold(),
            )
        )

    return pending, ignored


def build_record_from_pending(pending: PendingFile) -> FileRecord:
    return FileRecord(
        addon_name=pending.addon_name,
        addon_root=pending.addon_root,
        source_path=pending.source_path,
        rel_path=pending.rel_path,
        rel_key=pending.rel_key,
        size=pending.source_path.stat().st_size,
        sha256=sha256_file(pending.source_path),
        top_level=pending.top_level,
    )


def record_to_dict(record: FileRecord) -> dict[str, Any]:
    return {
        "addon_name": record.addon_name,
        "addon_root": str(record.addon_root),
        "source_path": str(record.source_path),
        "rel_path": record.rel_path,
        "rel_key": record.rel_key,
        "size": record.size,
        "sha256": record.sha256,
        "top_level": record.top_level,
    }


def copy_file_fast(source_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, destination)


def scan_addons(
    root: Path,
    recursive: bool,
    allow_content_only: bool,
    respect_ignore: bool,
    extra_ignore_patterns: list[str],
    excluded_roots: list[Path] | None = None,
    hash_workers: int = 0,
    on_addon_progress: Callable[[int, int, AddonSource], None] | None = None,
    on_file_progress: Callable[[str, int, int, str], None] | None = None,
) -> dict[str, Any]:
    addons = discover_addons(
        root,
        recursive=recursive,
        allow_content_only=allow_content_only,
        excluded_roots=excluded_roots,
    )
    addon_entries: list[dict[str, Any]] = []
    all_records: list[FileRecord] = []
    all_pending: list[PendingFile] = []
    by_rel_key: dict[str, list[FileRecord]] = defaultdict(list)
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)

    total = len(addons)
    for index, addon in enumerate(addons, start=1):
        if on_addon_progress:
            on_addon_progress(index, total, addon)

        pending_files, ignored = collect_pending_files(
            addon,
            respect_ignore=respect_ignore,
            extra_ignore_patterns=extra_ignore_patterns,
        )
        all_pending.extend(pending_files)

        addon_entries.append(
            {
                "name": addon.name,
                "root": str(addon.root),
                "detection_reason": addon.detection_reason,
                "discovery_index": addon.discovery_index,
                "manifest": {
                    "exists": addon.manifest.exists,
                    "path": str(addon.manifest.path) if addon.manifest.path else None,
                    "title": addon.manifest.title,
                    "type": addon.manifest.addon_type,
                    "tags": addon.manifest.tags,
                    "ignore": addon.manifest.ignore,
                    "parse_error": addon.manifest.parse_error,
                },
                "file_count": len(pending_files),
                "ignored_file_count": len(ignored),
                "ignored_files": ignored,
            }
        )

    total_files = len(all_pending)
    if total_files > 0:
        workers = hash_workers if hash_workers > 0 else _default_hash_workers()
        progress_every = _progress_interval(total_files)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(build_record_from_pending, pending): pending for pending in all_pending}
            completed = 0
            for future in as_completed(futures):
                record = future.result()
                completed += 1
                all_records.append(record)
                by_rel_key[record.rel_key].append(record)
                by_hash[record.sha256].append(record)
                if on_file_progress and (completed == total_files or completed % progress_every == 0):
                    on_file_progress("scan-files", completed, total_files, f"{record.addon_name}/{record.rel_path}")

        all_records.sort(key=lambda record: (record.addon_name.casefold(), record.rel_path.casefold()))

    same_path_identical: list[dict[str, Any]] = []
    same_path_different: list[dict[str, Any]] = []
    for rel_key, records in sorted(by_rel_key.items()):
        if len(records) <= 1:
            continue
        unique_hashes = sorted({record.sha256 for record in records})
        entry = {
            "rel_key": rel_key,
            "raw_rel_paths": sorted({record.rel_path for record in records}),
            "addons": [record.addon_name for record in records],
            "records": [record_to_dict(record) for record in records],
            "hash_count": len(unique_hashes),
            "identical_content": len(unique_hashes) == 1,
        }
        if len(unique_hashes) == 1:
            same_path_identical.append(entry)
        else:
            same_path_different.append(entry)

    duplicate_groups: list[dict[str, Any]] = []
    for sha256, records in sorted(by_hash.items()):
        unique_keys = sorted({record.rel_key for record in records})
        if len(unique_keys) <= 1:
            continue
        duplicate_groups.append(
            {
                "sha256": sha256,
                "file_count": len(records),
                "paths": sorted({record.rel_path for record in records}),
                "addons": sorted({record.addon_name for record in records}),
            }
        )

    return {
        "generated_at": _iso_now(),
        "root": str(root),
        "recursive": recursive,
        "allow_content_only": allow_content_only,
        "respect_ignore": respect_ignore,
        "extra_ignore_patterns": extra_ignore_patterns,
        "addon_count": len(addons),
        "total_file_count": len(all_records),
        "total_size_bytes": sum(record.size for record in all_records),
        "unique_rel_path_count": len(by_rel_key),
        "excluded_roots": [str(path) for path in _normalize_excluded_roots(root, excluded_roots)],
        "hash_workers": hash_workers if hash_workers > 0 else _default_hash_workers(),
        "file_records": [record_to_dict(record) for record in all_records],
        "addons": addon_entries,
        "same_path_identical_collisions": same_path_identical,
        "same_path_different_collisions": same_path_different,
        "cross_path_duplicate_groups": duplicate_groups,
    }


def reorder_addons(addons: list[dict[str, Any]], priority_mode: str) -> list[dict[str, Any]]:
    if priority_mode == "discovery":
        return sorted(addons, key=lambda addon: (addon.get("discovery_index", 0), addon["name"].casefold()))
    if priority_mode == "name-desc":
        return sorted(addons, key=lambda addon: addon["name"].casefold(), reverse=True)
    return sorted(addons, key=lambda addon: addon["name"].casefold())


def choose_record(
    records: list[dict[str, Any]],
    addon_priority: dict[str, int],
    conflict_policy: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    sorted_records = sorted(
        records,
        key=lambda record: (addon_priority[record["addon_name"].casefold()], record["rel_path"].casefold()),
    )
    unique_hashes = {record["sha256"] for record in sorted_records}

    if len(sorted_records) == 1:
        return sorted_records[0], [], "unique"
    if len(unique_hashes) == 1:
        return sorted_records[0], sorted_records[1:], "identical-dedup"
    if conflict_policy == "fail":
        return None, sorted_records, "blocked-different"
    if conflict_policy == "first":
        return sorted_records[0], sorted_records[1:], "first-wins"
    if conflict_policy == "last":
        return sorted_records[-1], sorted_records[:-1], "last-wins"
    raise ValueError(f"Unsupported conflict policy: {conflict_policy}")


def build_merged_manifest(
    ordered_addons: list[dict[str, Any]],
    merged_rel_paths: list[str],
    title_override: str | None,
    type_override: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged_title = title_override or f"Merged Addon Bundle ({len(ordered_addons)} addons)"

    source_types = [addon["manifest"]["type"] for addon in ordered_addons if addon["manifest"]["type"]]
    if type_override:
        merged_type = type_override
        type_reason = "user-override"
    elif len(set(source_types)) == 1 and source_types:
        merged_type = source_types[0]
        type_reason = "all-same"
    elif source_types:
        merged_type = Counter(source_types).most_common(1)[0][0]
        type_reason = "majority"
    else:
        merged_type = "vehicle"
        type_reason = "fallback"

    tag_counter: Counter[str] = Counter()
    tag_first_seen: dict[str, tuple[int, int, str]] = {}
    for addon_index, addon in enumerate(ordered_addons):
        for tag_index, tag in enumerate(addon["manifest"]["tags"]):
            key = tag.casefold()
            tag_counter[key] += 1
            tag_first_seen.setdefault(key, (addon_index, tag_index, tag))

    sorted_tags = sorted(
        tag_counter.keys(),
        key=lambda key: (-tag_counter[key], tag_first_seen[key][0], tag_first_seen[key][1], key),
    )
    selected_tag_keys = sorted_tags[:2]
    selected_tags = [tag_first_seen[key][2] for key in selected_tag_keys]
    dropped_tags = [tag_first_seen[key][2] for key in sorted_tags[2:]]

    safe_ignore: list[str] = []
    risky_ignore: list[dict[str, Any]] = []
    seen_ignore: set[str] = set()
    for addon in ordered_addons:
        for pattern in addon["manifest"]["ignore"]:
            folded = pattern.casefold()
            if folded in seen_ignore:
                continue
            seen_ignore.add(folded)
            matches = sorted(path for path in merged_rel_paths if matches_ignore(pattern, path))
            if matches:
                risky_ignore.append({"pattern": pattern, "match_count": len(matches), "matches": matches[:25]})
                continue
            safe_ignore.append(pattern)

    return (
        {
            "title": merged_title,
            "type": merged_type,
            "tags": selected_tags,
            "ignore": safe_ignore,
        },
        {
            "selected_type_reason": type_reason,
            "source_types": source_types,
            "dropped_tags": dropped_tags,
            "risky_ignore_dropped": risky_ignore,
        },
    )


def build_records_from_scan(scan_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if scan_report.get("file_records"):
        records_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in scan_report["file_records"]:
            records_by_key[record["rel_key"]].append(record)
        return records_by_key

    ignored_by_addon = {
        addon["name"]: {item["rel_path"] for item in addon.get("ignored_files", [])}
        for addon in scan_report["addons"]
    }

    records_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for addon in scan_report["addons"]:
        addon_root = Path(addon["root"])
        ignored = ignored_by_addon.get(addon["name"], set())
        for file_path in addon_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(addon_root).as_posix()
            if rel_path.casefold() == "addon.json" or rel_path in ignored:
                continue
            records_by_key[rel_path.casefold()].append(
                {
                    "addon_name": addon["name"],
                    "addon_root": str(addon_root),
                    "source_path": str(file_path),
                    "rel_path": rel_path,
                    "rel_key": rel_path.casefold(),
                    "size": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                }
            )
    return records_by_key


def copy_conflict_sample(source_path: Path, conflict_root: Path, addon_name: str, rel_path: str) -> None:
    destination = conflict_root / addon_name / rel_path
    copy_file_fast(source_path, destination)


def merge_from_scan(
    scan_report: dict[str, Any],
    work_dir: Path,
    merged_root: Path,
    conflict_policy: str,
    priority_mode: str,
    title_override: str | None,
    type_override: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered_addons = reorder_addons(scan_report["addons"], priority_mode=priority_mode)
    addon_priority = {addon["name"].casefold(): index for index, addon in enumerate(ordered_addons)}

    reports_root = work_dir / "reports"
    conflict_root = work_dir / "conflict_samples"
    ensure_empty_dir(merged_root)
    ensure_empty_dir(conflict_root)
    reports_root.mkdir(parents=True, exist_ok=True)

    records_by_key = build_records_from_scan(scan_report)
    merged_files: list[dict[str, Any]] = []
    identical_dedupes: list[dict[str, Any]] = []
    overridden_conflicts: list[dict[str, Any]] = []
    blocked_conflicts: list[dict[str, Any]] = []

    total_keys = len(records_by_key)
    planning_progress_every = _progress_interval(total_keys)
    for index, (rel_key, records) in enumerate(sorted(records_by_key.items()), start=1):
        if index == total_keys or index % planning_progress_every == 0:
            print(f"ADDON_MERGE_PROGRESS: merge-plan|{index}|{total_keys}|{records[0]['rel_path']}")
        winner, losers, resolution = choose_record(records, addon_priority=addon_priority, conflict_policy=conflict_policy)
        if winner is None:
            blocked_conflicts.append({"rel_key": rel_key, "resolution": resolution, "records": records})
            for loser in losers:
                copy_conflict_sample(Path(loser["source_path"]), conflict_root, loser["addon_name"], loser["rel_path"])
            continue

        destination = merged_root / winner["rel_path"]
        merged_files.append({**winner, "destination_path": str(destination), "resolution": resolution})

        if resolution == "identical-dedup":
            identical_dedupes.append({"rel_key": rel_key, "winner": winner, "losers": losers})
        elif resolution in {"first-wins", "last-wins"}:
            overridden_conflicts.append({"rel_key": rel_key, "resolution": resolution, "winner": winner, "losers": losers})
            for loser in losers:
                copy_conflict_sample(Path(loser["source_path"]), conflict_root, loser["addon_name"], loser["rel_path"])

    merged_files.sort(key=lambda item: item["rel_path"].casefold())
    total_merged_files = len(merged_files)
    total_merged_bytes = sum(int(item["size"]) for item in merged_files)
    if total_merged_files > 0:
        progress_every = _progress_interval(total_merged_files)
        copy_workers = _default_copy_workers()
        with ThreadPoolExecutor(max_workers=copy_workers) as executor:
            futures = {
                executor.submit(copy_file_fast, Path(item["source_path"]), Path(item["destination_path"])): item
                for item in merged_files
            }
            copied_files = 0
            copied_bytes = 0
            for future in as_completed(futures):
                item = futures[future]
                future.result()
                copied_files += 1
                copied_bytes += int(item["size"])
                if copied_files == total_merged_files or copied_files % progress_every == 0:
                    print(f"ADDON_MERGE_PROGRESS: merge-files|{copied_files}|{total_merged_files}|{item['rel_path']}")
                    print(f"ADDON_MERGE_PROGRESS: merge-bytes|{copied_bytes}|{max(total_merged_bytes, 1)}|{item['rel_path']}")

    manifest, manifest_diag = build_merged_manifest(
        ordered_addons,
        sorted(file["rel_path"] for file in merged_files),
        title_override,
        type_override,
    )
    (merged_root / "addon.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report = {
        "generated_at": _iso_now(),
        "status": "blocked" if blocked_conflicts else "merged",
        "conflict_policy": conflict_policy,
        "priority_mode": priority_mode,
        "priority_order": [addon["name"] for addon in ordered_addons],
        "output_dir": str(work_dir),
        "merged_root": str(merged_root),
        "merged_file_count": len(merged_files),
        "identical_dedupe_count": len(identical_dedupes),
        "overridden_conflict_count": len(overridden_conflicts),
        "blocked_conflict_count": len(blocked_conflicts),
        "manifest": manifest,
        "manifest_diagnostics": manifest_diag,
        "identical_dedupes": identical_dedupes,
        "overridden_conflicts": overridden_conflicts,
        "blocked_conflicts": blocked_conflicts,
    }
    write_json(reports_root / "merge_report.json", report)
    return report, merged_files


def build_manifest_for_directory(
    root: Path,
    exclude_rel_keys: set[str] | None = None,
    *,
    hash_workers: int = 0,
    progress_phase: str | None = None,
) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    excluded = exclude_rel_keys or set()
    files: list[PendingFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        if rel_path.casefold() in excluded:
            continue
        files.append(
            PendingFile(
                addon_name=root.name,
                addon_root=root,
                source_path=path,
                rel_path=rel_path,
                rel_key=rel_path.casefold(),
                top_level=rel_path.split("/", 1)[0].casefold(),
            )
        )

    if not files:
        return manifest

    workers = hash_workers if hash_workers > 0 else _default_hash_workers()
    total = len(files)
    progress_every = _progress_interval(total)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(build_record_from_pending, pending): pending for pending in files}
        completed = 0
        for future in as_completed(futures):
            record = future.result()
            completed += 1
            manifest[record.rel_key] = {
                "rel_path": record.rel_path,
                "size": record.size,
                "sha256": record.sha256,
            }
            if progress_phase and (completed == total or completed % progress_every == 0):
                print(f"ADDON_MERGE_PROGRESS: {progress_phase}|{completed}|{total}|{record.rel_path}")
    return manifest


def resolve_gmad(explicit_path: str | None) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        return candidate if candidate.exists() else None
    for candidate in DEFAULT_GMAD_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def run_gmad(args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout


def build_packaging_preflight(merged_files: list[dict[str, Any]]) -> dict[str, Any]:
    invalid_files: list[dict[str, Any]] = []
    total_payload_bytes = 0
    for item in merged_files:
        rel_path = item["rel_path"]
        total_payload_bytes += int(item["size"])
        matched_pattern = next(
            (pattern for pattern in STRICT_PACKAGE_INVALID_PATTERNS if matches_ignore(pattern, rel_path)),
            None,
        )
        if matched_pattern:
            invalid_files.append(
                {
                    "rel_path": rel_path,
                    "matched_pattern": matched_pattern,
                    "size": int(item["size"]),
                }
            )

    return {
        "strict_invalid_patterns": list(STRICT_PACKAGE_INVALID_PATTERNS),
        "strict_invalid_file_count": len(invalid_files),
        "invalid_files": invalid_files,
        "total_payload_bytes": total_payload_bytes,
        "total_payload_mib": round(total_payload_bytes / (1024 * 1024), 2),
    }


def _should_skip_warninvalid_diagnostic(create_output: str) -> tuple[bool, str | None]:
    lowered = (create_output or "").lower()
    if "not allowed by whitelist" in lowered or "file list verification failed" in lowered:
        return True, "verification-failed"
    if "failed to allocate buffer" in lowered or "can't grow buffer" in lowered:
        return True, "buffer-allocation-failed"
    return False, None


def validate_merge(
    merged_root: Path,
    work_dir: Path,
    final_gma_path: Path | None,
    merged_files: list[dict[str, Any]],
    gmad_path: Path | None,
    package: bool,
    hash_workers: int = 0,
) -> dict[str, Any]:
    reports_root = work_dir / "reports"
    local_pack_manifest = {
        item["rel_key"]: {
            "rel_path": item["rel_path"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        for item in merged_files
    }
    addon_json_path = merged_root / "addon.json"
    local_manifest = dict(local_pack_manifest)
    if addon_json_path.exists():
        local_manifest["addon.json"] = {
            "rel_path": "addon.json",
            "size": addon_json_path.stat().st_size,
            "sha256": sha256_file(addon_json_path),
        }
    local_total_size_bytes = sum(int(entry["size"]) for entry in local_manifest.values())
    report: dict[str, Any] = {
        "generated_at": _iso_now(),
        "merged_root": str(merged_root),
        "local_file_count": len(local_manifest),
        "local_total_size_bytes": local_total_size_bytes,
        "status": "validated-local",
        "package_requested": package,
        "gmad_path": str(gmad_path) if gmad_path else None,
        "packaging": None,
        "roundtrip": None,
    }

    if package and gmad_path is None:
        report["status"] = "packaging-skipped-no-gmad"
        write_json(reports_root / "validation_report.json", report)
        return report

    if package and gmad_path is not None:
        gma_path = final_gma_path or (merged_root.parent / f"{merged_root.name}.gma")
        roundtrip_root = work_dir / "roundtrip_extracted"
        ensure_dir(gma_path.parent)
        if gma_path.exists():
            gma_path.unlink()
        ensure_empty_dir(roundtrip_root)

        print("ADDON_MERGE_PROGRESS: validate-preflight|1|1|packaging preflight")
        packaging_preflight = build_packaging_preflight(merged_files)
        if packaging_preflight["strict_invalid_file_count"] > 0:
            preview = packaging_preflight["invalid_files"][:25]
            preview_lines = [
                f"{item['rel_path']} (pattern: {item['matched_pattern']})"
                for item in preview
            ]
            remaining = packaging_preflight["strict_invalid_file_count"] - len(preview_lines)
            output_lines = [
                "Strict packaging blocked before invoking gmad.exe.",
                f"Detected {packaging_preflight['strict_invalid_file_count']} file(s) that gmad strict mode rejects.",
                f"Payload size: {packaging_preflight['total_payload_mib']} MiB.",
            ]
            if preview_lines:
                output_lines.extend(["Invalid file preview:"] + preview_lines)
            if remaining > 0:
                output_lines.append(f"... and {remaining} more invalid file(s).")

            report["packaging"] = {
                "command": None,
                "return_code": None,
                "output": "\n".join(output_lines),
                "gma_exists": False,
                "gma_size_bytes": 0,
                "create_duration_sec": 0.0,
                "warninvalid_diagnostic": None,
                "preflight": packaging_preflight,
            }
            report["status"] = "packaging-blocked-invalid-files"
            write_json(reports_root / "validation_report.json", report)
            return report

        print("ADDON_MERGE_PROGRESS: validate-step|1|4|gmad create")
        create_args = [str(gmad_path), "create", "-folder", str(merged_root), "-out", str(gma_path), "-quiet"]
        create_started = time.perf_counter()
        create_code, create_output = run_gmad(create_args)
        create_duration_sec = round(time.perf_counter() - create_started, 3)
        packaging = {
            "command": create_args,
            "return_code": create_code,
            "output": create_output,
            "gma_exists": gma_path.exists(),
            "gma_size_bytes": gma_path.stat().st_size if gma_path.exists() else 0,
            "create_duration_sec": create_duration_sec,
            "warninvalid_diagnostic": None,
            "preflight": packaging_preflight,
        }
        report["packaging"] = packaging

        if create_code == 0 and gma_path.exists():
            print("ADDON_MERGE_PROGRESS: validate-step|2|4|gmad extract")
            extract_args = [str(gmad_path), "extract", "-file", str(gma_path), "-out", str(roundtrip_root), "-quiet"]
            extract_started = time.perf_counter()
            extract_code, extract_output = run_gmad(extract_args)
            extract_duration_sec = round(time.perf_counter() - extract_started, 3)
            extracted_manifest = (
                build_manifest_for_directory(
                    roundtrip_root,
                    hash_workers=hash_workers,
                    progress_phase="validate-files",
                )
                if extract_code == 0
                else {}
            )

            missing: list[str] = []
            changed: list[dict[str, Any]] = []
            extra: list[str] = []

            print("ADDON_MERGE_PROGRESS: validate-step|3|4|compare roundtrip")
            for rel_key, source_entry in local_pack_manifest.items():
                extracted_entry = extracted_manifest.get(rel_key)
                if extracted_entry is None:
                    missing.append(source_entry["rel_path"])
                    continue
                if extracted_entry["sha256"] != source_entry["sha256"]:
                    changed.append(
                        {
                            "rel_path": source_entry["rel_path"],
                            "source_sha256": source_entry["sha256"],
                            "extracted_sha256": extracted_entry["sha256"],
                        }
                    )

            for rel_key, extracted_entry in extracted_manifest.items():
                if rel_key not in local_pack_manifest:
                    extra.append(extracted_entry["rel_path"])

            report["roundtrip"] = {
                "command": extract_args,
                "return_code": extract_code,
                "output": extract_output,
                "extract_duration_sec": extract_duration_sec,
                "extracted_file_count": len(extracted_manifest),
                "missing_after_roundtrip": missing,
                "changed_after_roundtrip": changed,
                "extra_after_roundtrip": extra,
                "roundtrip_ok": extract_code == 0 and not missing and not changed and not extra,
            }
            print("ADDON_MERGE_PROGRESS: validate-step|4|4|validate done")
            report["status"] = "validated-roundtrip" if report["roundtrip"]["roundtrip_ok"] else "roundtrip-diff"
        else:
            should_skip_warninvalid, skip_reason = _should_skip_warninvalid_diagnostic(create_output)
            if should_skip_warninvalid:
                packaging["warninvalid_diagnostic"] = {
                    "skipped": True,
                    "reason": skip_reason,
                    "command": None,
                    "return_code": None,
                    "output": None,
                    "gma_exists": False,
                    "gma_size_bytes": 0,
                    "roundtrip": None,
                }
            else:
                warninvalid_gma = work_dir / "merged_addon_warninvalid.gma"
                warninvalid_root = work_dir / "roundtrip_warninvalid"
                if warninvalid_gma.exists():
                    warninvalid_gma.unlink()
                ensure_empty_dir(warninvalid_root)

                print("ADDON_MERGE_PROGRESS: validate-step|2|4|warninvalid diagnostic")
                warninvalid_args = [
                    str(gmad_path),
                    "create",
                    "-folder",
                    str(merged_root),
                    "-out",
                    str(warninvalid_gma),
                    "-warninvalid",
                    "-quiet",
                ]
                warninvalid_started = time.perf_counter()
                warninvalid_code, warninvalid_output = run_gmad(warninvalid_args)
                warninvalid_duration_sec = round(time.perf_counter() - warninvalid_started, 3)
                warninvalid_report: dict[str, Any] = {
                    "skipped": False,
                    "command": warninvalid_args,
                    "return_code": warninvalid_code,
                    "output": warninvalid_output,
                    "gma_exists": warninvalid_gma.exists(),
                    "gma_size_bytes": warninvalid_gma.stat().st_size if warninvalid_gma.exists() else 0,
                    "create_duration_sec": warninvalid_duration_sec,
                    "roundtrip": None,
                }

                if warninvalid_code == 0 and warninvalid_gma.exists():
                    extract_warn_args = [
                        str(gmad_path),
                        "extract",
                        "-file",
                        str(warninvalid_gma),
                        "-out",
                        str(warninvalid_root),
                        "-quiet",
                    ]
                    extract_warn_started = time.perf_counter()
                    extract_warn_code, extract_warn_output = run_gmad(extract_warn_args)
                    extract_warn_duration_sec = round(time.perf_counter() - extract_warn_started, 3)
                    extracted_warn_manifest = (
                        build_manifest_for_directory(
                            warninvalid_root,
                            hash_workers=hash_workers,
                            progress_phase="validate-files",
                        )
                        if extract_warn_code == 0
                        else {}
                    )

                    missing = []
                    changed = []
                    extra = []

                    print("ADDON_MERGE_PROGRESS: validate-step|3|4|compare warninvalid")
                    for rel_key, source_entry in local_pack_manifest.items():
                        extracted_entry = extracted_warn_manifest.get(rel_key)
                        if extracted_entry is None:
                            missing.append(source_entry["rel_path"])
                            continue
                        if extracted_entry["sha256"] != source_entry["sha256"]:
                            changed.append(
                                {
                                    "rel_path": source_entry["rel_path"],
                                    "source_sha256": source_entry["sha256"],
                                    "extracted_sha256": extracted_entry["sha256"],
                                }
                            )

                    for rel_key, extracted_entry in extracted_warn_manifest.items():
                        if rel_key not in local_pack_manifest:
                            extra.append(extracted_entry["rel_path"])

                    warninvalid_report["roundtrip"] = {
                        "command": extract_warn_args,
                        "return_code": extract_warn_code,
                        "output": extract_warn_output,
                        "extract_duration_sec": extract_warn_duration_sec,
                        "extracted_file_count": len(extracted_warn_manifest),
                        "missing_after_roundtrip": missing,
                        "changed_after_roundtrip": changed,
                        "extra_after_roundtrip": extra,
                        "roundtrip_ok": extract_warn_code == 0 and not missing and not changed and not extra,
                    }

                packaging["warninvalid_diagnostic"] = warninvalid_report
            print("ADDON_MERGE_PROGRESS: validate-step|4|4|packaging failed")
            report["status"] = "packaging-failed"

    write_json(reports_root / "validation_report.json", report)
    return report


def collect_discarded_files(scan_report: dict[str, Any]) -> list[dict[str, Any]]:
    discarded: list[dict[str, Any]] = []
    for addon in scan_report["addons"]:
        for ignored in addon.get("ignored_files", []):
            if ignored.get("source") != "extra":
                continue
            discarded.append(
                {
                    "addon_name": addon["name"],
                    "rel_path": ignored.get("rel_path"),
                    "matched_pattern": ignored.get("matched_pattern"),
                }
            )
    return discarded


def build_summary_markdown(
    scan_report: dict[str, Any],
    merge_report: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    package_mode: str,
    bundle_name: str,
    output_root: Path,
) -> str:
    lines = [
        "# Addon Merge Summary",
        "",
        "## Scan",
        f"- Root: `{scan_report['root']}`",
        f"- Nome do addon final: `{bundle_name}`",
        f"- Destino final: `{output_root}`",
        f"- Addons detectados: `{scan_report['addon_count']}`",
        f"- Arquivos totais considerados: `{scan_report['total_file_count']}`",
        f"- Caminhos relativos unicos: `{scan_report['unique_rel_path_count']}`",
        f"- Colisoes identicas no mesmo caminho: `{len(scan_report['same_path_identical_collisions'])}`",
        f"- Colisoes diferentes no mesmo caminho: `{len(scan_report['same_path_different_collisions'])}`",
        f"- Extra ignore: `{', '.join(scan_report.get('extra_ignore_patterns', [])) or '(none)'}`",
    ]

    if merge_report is not None:
        lines.extend(
            [
                "",
                "## Merge",
                f"- Status: `{merge_report['status']}`",
                f"- Politica: `{merge_report['conflict_policy']}`",
                f"- Prioridade: `{merge_report['priority_mode']}`",
                f"- Arquivos no addon consolidado: `{merge_report['merged_file_count']}`",
                f"- Dedupe identico: `{merge_report['identical_dedupe_count']}`",
                f"- Conflitos sobrescritos: `{merge_report['overridden_conflict_count']}`",
                f"- Conflitos bloqueados: `{merge_report['blocked_conflict_count']}`",
            ]
        )

    if validation_report is not None:
        lines.extend(
            [
                "",
                "## Validacao",
                f"- Modo de empacotamento: `{package_mode}`",
                f"- Status: `{validation_report['status']}`",
                f"- Empacotamento solicitado: `{validation_report['package_requested']}`",
                f"- Tamanho local consolidado: `{validation_report.get('local_total_size_bytes', 0)}` bytes",
            ]
        )
        packaging = validation_report.get("packaging")
        if packaging:
            preflight = packaging.get("preflight")
            if preflight:
                lines.append(
                    f"- Preflight de empacotamento: `{preflight['strict_invalid_file_count']}` arquivo(s) invalido(s) detectado(s)"
                )
            lines.append(
                f"- `gmad create`: retorno `{packaging['return_code']}`, `.gma` gerado: `{packaging['gma_exists']}`"
            )
            diagnostic = packaging.get("warninvalid_diagnostic")
            if diagnostic:
                if diagnostic.get("skipped"):
                    lines.append(
                        f"- Diagnostico `-warninvalid`: pulado (`{diagnostic.get('reason') or 'unspecified'}`)"
                    )
                else:
                    lines.append(
                        f"- Diagnostico `-warninvalid`: retorno `{diagnostic['return_code']}`, `.gma` gerado: `{diagnostic['gma_exists']}`"
                    )

        roundtrip = validation_report.get("roundtrip")
        if roundtrip:
            lines.append(f"- Roundtrip `.gma -> extracao`: `{roundtrip['roundtrip_ok']}`")
            lines.append(f"- Faltando apos roundtrip: `{len(roundtrip['missing_after_roundtrip'])}`")
            lines.append(f"- Alterados apos roundtrip: `{len(roundtrip['changed_after_roundtrip'])}`")
            lines.append(f"- Extras apos roundtrip: `{len(roundtrip['extra_after_roundtrip'])}`")

    return "\n".join(lines) + "\n"


def build_summary_payload(
    *,
    root: Path,
    work_dir: Path,
    output_root: Path,
    bundle_name: str,
    merged_root: Path,
    final_gma_path: Path | None,
    gmad_path: Path | None,
    scan_only: bool,
    conflict_policy: str,
    priority_mode: str,
    package_mode: str,
    scan_report: dict[str, Any],
    merge_report: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    summary_md_path: Path,
    exit_code: int,
) -> dict[str, Any]:
    packaging = validation_report.get("packaging") if validation_report else None
    warninvalid = packaging.get("warninvalid_diagnostic") if packaging else None

    if scan_only:
        overall_status = "scan-completed"
    elif merge_report is not None and merge_report["status"] != "merged":
        overall_status = "merge-blocked"
    elif validation_report is None:
        overall_status = "merge-completed"
    elif validation_report["status"] in {"validated-local", "validated-roundtrip"}:
        overall_status = "ok"
    else:
        overall_status = "validation-failed"

    return {
        "generated_at": _iso_now(),
        "run": {
            "root": str(root),
            "work_dir": str(work_dir),
            "output_root": str(output_root),
            "bundle_name": bundle_name,
            "scan_only": scan_only,
            "conflict_policy": conflict_policy,
            "priority_mode": priority_mode,
            "package_mode": package_mode,
            "gmad_path": str(gmad_path) if gmad_path else None,
            "compatibility_ignore_patterns": scan_report.get("extra_ignore_patterns", []),
        },
        "paths": {
            "reports_dir": str(work_dir / "reports"),
            "scan_report": str(work_dir / "reports" / "scan_report.json"),
            "merge_report": str(work_dir / "reports" / "merge_report.json"),
            "validation_report": str(work_dir / "reports" / "validation_report.json"),
            "summary_markdown": str(summary_md_path),
            "merged_root": str(merged_root) if merged_root.exists() else None,
            "gma_path": str(final_gma_path) if final_gma_path and final_gma_path.exists() else None,
            "warninvalid_gma_path": str(work_dir / "merged_addon_warninvalid.gma")
            if (work_dir / "merged_addon_warninvalid.gma").exists()
            else None,
        },
        "status": {
            "exit_code": exit_code,
            "overall_status": overall_status,
            "merge_status": merge_report["status"] if merge_report else None,
            "validation_status": validation_report["status"] if validation_report else None,
        },
        "scan": {
            "addon_count": scan_report["addon_count"],
            "total_file_count": scan_report["total_file_count"],
            "total_size_bytes": scan_report["total_size_bytes"],
            "unique_rel_path_count": scan_report["unique_rel_path_count"],
            "same_path_identical_collisions": len(scan_report["same_path_identical_collisions"]),
            "same_path_different_collisions": len(scan_report["same_path_different_collisions"]),
            "cross_path_duplicate_groups": len(scan_report["cross_path_duplicate_groups"]),
            "extra_ignore_patterns": scan_report.get("extra_ignore_patterns", []),
        },
        "merge": {
            "status": merge_report["status"],
            "conflict_policy": merge_report["conflict_policy"],
            "priority_mode": merge_report["priority_mode"],
            "priority_order": merge_report["priority_order"],
            "merged_file_count": merge_report["merged_file_count"],
            "identical_dedupe_count": merge_report["identical_dedupe_count"],
            "overridden_conflict_count": merge_report["overridden_conflict_count"],
            "blocked_conflict_count": merge_report["blocked_conflict_count"],
            "manifest": merge_report["manifest"],
        }
        if merge_report
        else None,
        "validation": {
            "status": validation_report["status"],
            "package_requested": validation_report["package_requested"],
            "local_file_count": validation_report["local_file_count"],
            "local_total_size_bytes": validation_report.get("local_total_size_bytes", 0),
            "gmad_path": validation_report["gmad_path"],
            "packaging": packaging,
            "roundtrip": validation_report.get("roundtrip"),
        }
        if validation_report
        else None,
        "addons": [
            {
                "name": addon["name"],
                "root": addon["root"],
                "file_count": addon["file_count"],
                "ignored_file_count": addon["ignored_file_count"],
                "detection_reason": addon["detection_reason"],
            }
            for addon in scan_report["addons"]
        ],
        "details": {
            "discarded_file_count": len(collect_discarded_files(scan_report)),
            "discarded_files_preview": collect_discarded_files(scan_report)[:50],
            "same_path_different_conflict_preview": [
                {
                    "rel_path": item["raw_rel_paths"][0] if item["raw_rel_paths"] else item["rel_key"],
                    "addon_names": item["addons"],
                    "hash_count": item["hash_count"],
                }
                for item in scan_report["same_path_different_collisions"][:25]
            ],
            "same_path_identical_conflict_preview": [
                {
                    "rel_path": item["raw_rel_paths"][0] if item["raw_rel_paths"] else item["rel_key"],
                    "addon_names": item["addons"],
                }
                for item in scan_report["same_path_identical_collisions"][:25]
            ],
            "blocked_conflict_preview": [
                {
                    "rel_key": item["rel_key"],
                    "record_count": len(item.get("records", [])),
                    "addon_names": sorted({record["addon_name"] for record in item.get("records", [])}),
                }
                for item in (merge_report.get("blocked_conflicts", []) if merge_report else [])[:25]
            ],
            "overridden_conflict_preview": [
                {
                    "rel_key": item["rel_key"],
                    "resolution": item["resolution"],
                    "winner": item["winner"]["addon_name"],
                    "loser_count": len(item.get("losers", [])),
                }
                for item in (merge_report.get("overridden_conflicts", []) if merge_report else [])[:25]
            ],
            "risky_ignore_patterns_dropped": (
                merge_report.get("manifest_diagnostics", {}).get("risky_ignore_dropped", [])[:25]
                if merge_report
                else []
            ),
            "packaging_preflight_invalid_files_preview": (
                (packaging.get("preflight") or {}).get("invalid_files", [])[:50]
                if packaging
                else []
            ),
            "packaging_output_excerpt": _truncate_text(packaging.get("output") if packaging else None),
            "warninvalid_output_excerpt": _truncate_text(warninvalid.get("output") if warninvalid else None),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge multiple addon folders into one consolidated addon.")
    parser.add_argument("root", help="Root folder that contains addon folders.")
    parser.add_argument("--work", default=None, help="Work directory for reports and output.")
    parser.add_argument("--scan-only", action="store_true", help="Only scan and summarize. Do not merge.")
    parser.add_argument("--gmad", default=None, help="Explicit path to gmad.exe.")
    parser.add_argument("--recursive", action="store_true", help="Search for addon.json recursively.")
    parser.add_argument("--allow-content-only", action="store_true", help="Accept roots without addon.json if canonical content folders exist.")
    parser.add_argument("--no-respect-ignore", action="store_true", help="Do not apply addon.json ignore patterns.")
    parser.add_argument("--conflict-policy", choices=["fail", "first", "last"], default="first")
    parser.add_argument("--priority-mode", choices=["name-asc", "name-desc", "discovery"], default="name-asc")
    parser.add_argument("--package-mode", choices=["strict", "compatibility", "local-only"], default="strict")
    parser.add_argument("--compat-ignore", action="append", default=[], help="Additional ignore patterns for compatibility mode.")
    parser.add_argument("--title", default=None, help="Override title for generated addon.json.")
    parser.add_argument("--addon-type", default=None, help="Override type for generated addon.json.")
    parser.add_argument("--bundle-name", default=None, help="Folder/base name for the merged addon output.")
    parser.add_argument("--output-root", default=None, help="Directory where the merged addon folder/.gma should be created.")
    parser.add_argument("--exclude-dir", action="append", default=[], help="Directory to exclude from discovery/inventory.")
    parser.add_argument("--reuse-scan-report", default=None, help="Reuse an existing scan_report.json instead of scanning again.")
    parser.add_argument("--hash-workers", type=int, default=0, help="Worker count for file hashing. 0 = auto.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] Root folder not found: {root}")
        return 2

    work_dir = Path(args.work).expanduser().resolve() if args.work else _default_work_dir(root)
    reports_dir = work_dir / "reports"
    work_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_root, bundle_name, merged_root, final_gma_path = _resolve_output_paths(root, args.output_root, args.bundle_name)
    excluded_roots = [Path(value) for value in args.exclude_dir]
    if merged_root == root or is_descendant(merged_root, root):
        excluded_roots.append(merged_root)

    gmad_path = resolve_gmad(args.gmad)
    extra_ignore_patterns: list[str] = []
    if args.package_mode == "compatibility":
        extra_ignore_patterns.extend(DEFAULT_COMPAT_IGNORE)
        extra_ignore_patterns.extend(args.compat_ignore)

    print(f"ADDON_MERGE_WORK_DIR: {work_dir}")
    if args.reuse_scan_report:
        print("== Step 1/3: Reuse scan ==")
        scan_report = _safe_read_json(Path(args.reuse_scan_report).expanduser().resolve())
    else:
        print("== Step 1/3: Scan ==")
        scan_report = scan_addons(
            root=root,
            recursive=args.recursive,
            allow_content_only=args.allow_content_only,
            respect_ignore=not args.no_respect_ignore,
            extra_ignore_patterns=extra_ignore_patterns,
            excluded_roots=excluded_roots,
            hash_workers=args.hash_workers,
            on_addon_progress=lambda index, total, addon: print(f"=== ({index}/{total}) ADDON: {addon.name}"),
            on_file_progress=lambda phase, current, total, label: print(
                f"ADDON_MERGE_PROGRESS: {phase}|{current}|{total}|{label}"
            ),
        )
    write_json(reports_dir / "scan_report.json", scan_report)

    merge_report: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None
    merged_files: list[dict[str, Any]] = []

    if args.scan_only:
        summary_md = reports_dir / "summary.md"
        summary_md.write_text(
            build_summary_markdown(scan_report, None, None, args.package_mode, bundle_name, output_root),
            encoding="utf-8",
        )
        summary_path = reports_dir / SUMMARY_FILE_NAME
        write_json(
            summary_path,
            build_summary_payload(
                root=root,
                work_dir=work_dir,
                output_root=output_root,
                bundle_name=bundle_name,
                merged_root=merged_root,
                final_gma_path=final_gma_path if args.package_mode != "local-only" else None,
                gmad_path=gmad_path,
                scan_only=True,
                conflict_policy=args.conflict_policy,
                priority_mode=args.priority_mode,
                package_mode=args.package_mode,
                scan_report=scan_report,
                merge_report=None,
                validation_report=None,
                summary_md_path=summary_md,
                exit_code=0,
            ),
        )
        print(f"ADDON_MERGE_SUMMARY: {summary_path}")
        return 0

    print("== Step 2/3: Merge ==")
    merge_report, merged_files = merge_from_scan(
        scan_report=scan_report,
        work_dir=work_dir,
        merged_root=merged_root,
        conflict_policy=args.conflict_policy,
        priority_mode=args.priority_mode,
        title_override=args.title,
        type_override=args.addon_type,
    )
    if merged_root.exists():
        print(f"ADDON_MERGED_ROOT: {merged_root}")

    exit_code = 0
    if merge_report["status"] != "merged":
        exit_code = 2
    else:
        print("== Step 3/3: Validate ==")
        validation_report = validate_merge(
            merged_root=merged_root,
            work_dir=work_dir,
            final_gma_path=final_gma_path if args.package_mode != "local-only" else None,
            merged_files=merged_files,
            gmad_path=gmad_path,
            package=args.package_mode != "local-only",
            hash_workers=args.hash_workers,
        )
        if final_gma_path.exists():
            print(f"ADDON_MERGED_GMA: {final_gma_path}")
        if validation_report["status"] not in {"validated-local", "validated-roundtrip"}:
            exit_code = 3

    summary_md = reports_dir / "summary.md"
    summary_md.write_text(
        build_summary_markdown(scan_report, merge_report, validation_report, args.package_mode, bundle_name, output_root),
        encoding="utf-8",
    )
    summary_path = reports_dir / SUMMARY_FILE_NAME
    write_json(
        summary_path,
        build_summary_payload(
            root=root,
            work_dir=work_dir,
            output_root=output_root,
            bundle_name=bundle_name,
            merged_root=merged_root,
            final_gma_path=final_gma_path if args.package_mode != "local-only" else None,
            gmad_path=gmad_path,
            scan_only=False,
            conflict_policy=args.conflict_policy,
            priority_mode=args.priority_mode,
            package_mode=args.package_mode,
            scan_report=scan_report,
            merge_report=merge_report,
            validation_report=validation_report,
            summary_md_path=summary_md,
            exit_code=exit_code,
        ),
    )
    print(f"ADDON_MERGE_SUMMARY: {summary_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
