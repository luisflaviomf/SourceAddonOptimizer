from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import struct
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SUMMARY_NAME = "map_bsp_scan_summary.json"
PAK_LUMP_INDEX = 40
LUMP_SIZE = 16
LUMP_COUNT = 64


@dataclass
class RunConfig:
    root: Path
    work_dir: Path
    cancel_file: Path | None = None


@dataclass
class BspScanItem:
    relative_path: str
    bsp_path: str
    bsp_size: int
    status: str
    message: str
    version: int = 0
    pak_offset: int = 0
    pak_size: int = 0
    pak_percent_of_bsp: float = 0.0
    pak_zip_valid: bool = False
    pak_entry_count: int = 0
    pak_at_eof: bool = False
    staging_eligible: bool = False
    staging_status: str = "not_attempted"
    staging_dir: str = ""
    staged_file_count: int = 0
    staged_total_bytes: int = 0
    inventory_by_extension: list[dict] | None = None
    phase2_candidate: bool = False
    phase2_blockers: list[str] | None = None


class MapScanError(RuntimeError):
    pass


class CancelRequested(MapScanError):
    pass


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent


def _default_work_dir(root: Path) -> Path:
    return _runtime_root() / "work" / f"{root.name or 'root'}_map_scan_runs" / _ts()


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.name


def _cancel_requested(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _raise_if_cancelled(cancel_file: Path | None, *, context: str = "") -> None:
    if _cancel_requested(cancel_file):
        message = "Cancelled by user"
        if context:
            message += f" during {context}"
        raise CancelRequested(message)


def _cleanup_cancel_file(cancel_file: Path | None) -> None:
    if not cancel_file:
        return
    try:
        if cancel_file.exists():
            cancel_file.unlink()
    except Exception:
        pass


def _safe_zip_destination(root: Path, member_name: str) -> Path:
    member = member_name.replace("\\", "/").strip("/")
    if not member:
        raise MapScanError("ZIP entry has an empty name")

    rel_path = Path(member)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise MapScanError(f"Unsafe ZIP entry path: {member_name}")

    root_resolved = root.resolve()
    destination = (root / rel_path).resolve()
    try:
        destination.relative_to(root_resolved)
    except ValueError as ex:
        raise MapScanError(f"ZIP entry escapes extraction root: {member_name}") from ex
    return destination


def _sanitize_stage_name(relative_path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", relative_path.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "bsp"


def _find_map_bsp_files(root: Path, cancel_file: Path | None = None) -> tuple[list[Path], list[str]]:
    items: list[Path] = []
    errors: list[str] = []

    def on_error(err: OSError) -> None:
        errors.append(str(err))

    for cur_dir, dirnames, filenames in os.walk(root, onerror=on_error):
        _raise_if_cancelled(cancel_file, context="scan")
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        cur_path = Path(cur_dir)
        cur_parts = [part.lower() for part in cur_path.parts]
        if "maps" not in cur_parts:
            continue
        for name in filenames:
            _raise_if_cancelled(cancel_file, context="scan")
            if not name.lower().endswith(".bsp"):
                continue
            path = (cur_path / name).resolve()
            if path.is_file():
                items.append(path)

    items.sort(key=lambda p: str(p).lower())
    return items, errors


def _scan_total_bytes(root: Path, cancel_file: Path | None = None) -> tuple[int, list[str]]:
    total = 0
    errors: list[str] = []

    def on_error(err: OSError) -> None:
        errors.append(str(err))

    for cur_dir, dirnames, filenames in os.walk(root, onerror=on_error):
        _raise_if_cancelled(cancel_file, context="size scan")
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        cur_path = Path(cur_dir)
        for name in filenames:
            _raise_if_cancelled(cancel_file, context="size scan")
            path = cur_path / name
            try:
                total += path.stat().st_size
            except Exception as ex:
                errors.append(f"{path}: {type(ex).__name__}: {ex}")

    return total, errors


def _validate_zip_payload(payload: bytes) -> tuple[bool, int, str]:
    if not payload.startswith(b"PK\x03\x04"):
        return False, 0, "pak_not_zip"

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            bad_member = archive.testzip()
            if bad_member:
                return False, 0, f"pak_zip_invalid:{bad_member}"
            file_count = sum(1 for info in archive.infolist() if not info.is_dir())
            return True, file_count, "pak_valid_zip"
    except zipfile.BadZipFile:
        return False, 0, "pak_zip_invalid"


def _read_bsp_pak_payload(path: Path) -> tuple[int, int, int, bytes]:
    bsp_size = path.stat().st_size
    with path.open("rb") as fh:
        ident = fh.read(4)
        if ident != b"VBSP":
            raise MapScanError(f"Invalid BSP header in {path.name}: {ident.decode('ascii', errors='replace') or 'unknown'}")

        version_bytes = fh.read(4)
        if len(version_bytes) != 4:
            raise MapScanError(f"BSP header too short: {path.name}")

        version = struct.unpack("<i", version_bytes)[0]
        fh.seek(8 + PAK_LUMP_INDEX * LUMP_SIZE)
        lump_bytes = fh.read(LUMP_SIZE)
        if len(lump_bytes) != LUMP_SIZE:
            raise MapScanError(f"BSP pak lump header missing: {path.name}")

        pak_offset, pak_size, _, _ = struct.unpack("<iiii", lump_bytes)
        if pak_size <= 0:
            return version, pak_offset, 0, b""

        if pak_offset < 0 or pak_size < 0 or pak_offset + pak_size > bsp_size:
            raise MapScanError(
                f"BSP pak lump out of range in {path.name}: offset={pak_offset}, length={pak_size}, size={bsp_size}"
            )

        fh.seek(pak_offset)
        pak_bytes = fh.read(pak_size)

    if len(pak_bytes) != pak_size:
        raise MapScanError(f"Failed to read full BSP pak lump from {path.name}")

    return version, pak_offset, pak_size, pak_bytes


def _scan_directory_inventory(root: Path, cancel_file: Path | None = None) -> tuple[list[dict], int, int]:
    buckets: dict[str, dict[str, int | str]] = {}
    total_files = 0
    total_bytes = 0

    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower()):
        _raise_if_cancelled(cancel_file, context="staging inventory")
        try:
            size = path.stat().st_size
        except Exception:
            continue

        ext = path.suffix.lower() or "(no extension)"
        bucket = buckets.setdefault(ext, {"extension": ext, "file_count": 0, "total_bytes": 0})
        bucket["file_count"] = int(bucket["file_count"]) + 1
        bucket["total_bytes"] = int(bucket["total_bytes"]) + size
        total_files += 1
        total_bytes += size

    inventory = sorted(
        buckets.values(),
        key=lambda item: (-int(item["total_bytes"]), str(item["extension"])),
    )
    return inventory, total_files, total_bytes


def _extract_pak_bytes_to_dir(pak_bytes: bytes, destination_dir: Path, cancel_file: Path | None = None) -> int:
    destination_dir.mkdir(parents=True, exist_ok=True)
    file_count = 0

    try:
        with zipfile.ZipFile(io.BytesIO(pak_bytes)) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise MapScanError(f"Corrupted ZIP entry inside BSP pak: {bad_member}")

            for info in archive.infolist():
                _raise_if_cancelled(cancel_file, context="BSP pak staging")
                if info.is_dir():
                    continue

                destination = _safe_zip_destination(destination_dir, info.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                file_count += 1
    except zipfile.BadZipFile as ex:
        raise MapScanError("Invalid ZIP data inside BSP pak") from ex

    return file_count


def _stage_bsp_pak(
    config: RunConfig,
    item: BspScanItem,
    stage_index: int,
) -> None:
    item.staging_eligible = item.pak_zip_valid
    if not item.staging_eligible:
        item.staging_status = "skipped"
        return

    if item.status != "ok":
        item.staging_status = "skipped"
        return

    relative_stage_name = _sanitize_stage_name(item.relative_path)
    stage_root = config.work_dir / "staging" / f"{stage_index:03d}_{relative_stage_name}"
    stage_contents = stage_root / "pak_contents"

    if stage_root.exists():
        shutil.rmtree(stage_root, ignore_errors=True)
    stage_contents.mkdir(parents=True, exist_ok=True)

    try:
        _, _, _, pak_bytes = _read_bsp_pak_payload(Path(item.bsp_path))
        extracted_count = _extract_pak_bytes_to_dir(pak_bytes, stage_contents, cancel_file=config.cancel_file)
        inventory, staged_files, staged_bytes = _scan_directory_inventory(stage_contents, cancel_file=config.cancel_file)
        item.staging_status = "extracted"
        item.staging_dir = str(stage_contents)
        item.staged_file_count = staged_files if staged_files > 0 else extracted_count
        item.staged_total_bytes = staged_bytes
        item.inventory_by_extension = inventory

        manifest = {
            "source_bsp": item.bsp_path,
            "relative_path": item.relative_path,
            "bsp_size": item.bsp_size,
            "pak_size": item.pak_size,
            "pak_percent_of_bsp": item.pak_percent_of_bsp,
            "staged_file_count": item.staged_file_count,
            "staged_total_bytes": item.staged_total_bytes,
            "inventory_by_extension": inventory,
        }
        (stage_root / "stage_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as ex:
        item.staging_status = "failed"
        item.staging_dir = str(stage_contents)
        item.message = f"{item.message}; staging_failed:{type(ex).__name__}"


def _analyze_bsp(path: Path, root: Path, cancel_file: Path | None = None) -> BspScanItem:
    _raise_if_cancelled(cancel_file, context="BSP analysis")

    relative_path = _safe_relative(path, root)
    bsp_size = path.stat().st_size
    blockers: list[str] = []

    try:
        version, pak_offset, pak_size, pak_bytes = _read_bsp_pak_payload(path)
    except Exception as ex:
        lower_message = str(ex).lower()
        blocker = "read_error"
        if "invalid bsp header" in lower_message:
            blocker = "invalid_bsp_header"
        elif "header too short" in lower_message:
            blocker = "bsp_header_too_short"
        elif "pak lump header missing" in lower_message:
            blocker = "pak_lump_header_missing"
        elif "out of range" in lower_message:
            blocker = "pak_out_of_range"
        return BspScanItem(
            relative_path=relative_path,
            bsp_path=str(path),
            bsp_size=bsp_size,
            status="error",
            message=f"{type(ex).__name__}: {ex}",
            phase2_blockers=[blocker],
        )

    if pak_size <= 0:
        blockers.append("pak_missing_or_empty")
        return BspScanItem(
            relative_path=relative_path,
            bsp_path=str(path),
            bsp_size=bsp_size,
            status="ok",
            message="pak_missing_or_empty",
            version=version,
            pak_offset=pak_offset,
            pak_size=0,
            pak_percent_of_bsp=0.0,
            pak_zip_valid=False,
            pak_entry_count=0,
            pak_at_eof=False,
            staging_eligible=False,
            phase2_candidate=False,
            phase2_blockers=blockers,
            inventory_by_extension=[],
        )

    pak_zip_valid, pak_entry_count, zip_message = _validate_zip_payload(pak_bytes)
    if not pak_zip_valid:
        blockers.append(zip_message)

    pak_at_eof = pak_offset + pak_size == bsp_size
    if not pak_at_eof:
        blockers.append("pak_not_at_eof")

    phase2_candidate = pak_zip_valid and pak_at_eof and not blockers
    message = "pak_ready_for_future_reinject" if phase2_candidate else (blockers[0] if blockers else "pak_scanned")

    return BspScanItem(
        relative_path=relative_path,
        bsp_path=str(path),
        bsp_size=bsp_size,
        status="ok",
        message=message,
        version=version,
        pak_offset=pak_offset,
        pak_size=pak_size,
        pak_percent_of_bsp=round((pak_size / bsp_size * 100.0) if bsp_size > 0 else 0.0, 2),
        pak_zip_valid=pak_zip_valid,
        pak_entry_count=pak_entry_count,
        pak_at_eof=pak_at_eof,
        staging_eligible=pak_zip_valid,
        inventory_by_extension=[],
        phase2_candidate=phase2_candidate,
        phase2_blockers=blockers,
    )


def _build_summary(
    config: RunConfig,
    started_at: str,
    finished_at: str,
    exit_code: int,
    addon_total_bytes: int,
    bsp_items: list[BspScanItem],
    scan_errors: list[str],
    cancelled: bool,
) -> dict:
    bsp_total = len(bsp_items)
    bsp_with_pak = sum(1 for item in bsp_items if item.pak_size > 0)
    bsp_with_valid_zip = sum(1 for item in bsp_items if item.pak_zip_valid)
    staging_eligible_count = sum(1 for item in bsp_items if item.staging_eligible)
    staged_bsp_count = sum(1 for item in bsp_items if item.staging_status == "extracted")
    staged_files_total = sum(item.staged_file_count for item in bsp_items)
    phase2_candidate_count = sum(1 for item in bsp_items if item.phase2_candidate)
    analysis_errors = sum(1 for item in bsp_items if item.status == "error")
    bsp_total_bytes = sum(item.bsp_size for item in bsp_items)
    pak_total_bytes = sum(item.pak_size for item in bsp_items if item.pak_size > 0)
    staged_total_bytes = sum(item.staged_total_bytes for item in bsp_items)

    overall_inventory_buckets: dict[str, dict[str, int | str]] = {}
    for item in bsp_items:
        for entry in item.inventory_by_extension or []:
            ext = str(entry.get("extension", "(unknown)"))
            bucket = overall_inventory_buckets.setdefault(ext, {"extension": ext, "file_count": 0, "total_bytes": 0})
            bucket["file_count"] = int(bucket["file_count"]) + int(entry.get("file_count", 0))
            bucket["total_bytes"] = int(bucket["total_bytes"]) + int(entry.get("total_bytes", 0))

    overall_inventory = sorted(
        overall_inventory_buckets.values(),
        key=lambda item: (-int(item["total_bytes"]), str(item["extension"])),
    )

    return {
        "summary_version": 2,
        "run": {
            "root": str(config.root),
            "work_dir": str(config.work_dir),
            "summary_path": str(config.work_dir / SUMMARY_NAME),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "cancelled": cancelled,
            "phase": "scan_and_stage",
        },
        "counts": {
            "bsp_total": bsp_total,
            "bsp_with_pak": bsp_with_pak,
            "bsp_with_valid_zip": bsp_with_valid_zip,
            "staging_eligible_count": staging_eligible_count,
            "staged_bsp_count": staged_bsp_count,
            "staged_files_total": staged_files_total,
            "phase2_candidate_count": phase2_candidate_count,
            "phase2_blocked_count": max(0, bsp_total - phase2_candidate_count),
            "analysis_errors": analysis_errors,
            "scan_errors": len(scan_errors),
            "cancelled": cancelled,
        },
        "sizes": {
            "addon_total_bytes": addon_total_bytes,
            "bsp_total_bytes": bsp_total_bytes,
            "pak_total_bytes": pak_total_bytes,
            "staged_total_bytes": staged_total_bytes,
            "pak_share_of_all_bsp_percent": round((pak_total_bytes / bsp_total_bytes * 100.0) if bsp_total_bytes > 0 else 0.0, 2),
        },
        "staging": {
            "root": str(config.work_dir / "staging"),
            "total_dirs": staged_bsp_count,
            "inventory_overall": overall_inventory,
        },
        "future_validation": {
            "phase": "future_reinject_gate_not_implemented_in_phase_2",
            "required_checks": [
                "Output file must still start with VBSP and remain readable as a BSP.",
                "PAKFILE lump offset and size must remain within file bounds after reinjection.",
                "Rebuilt pak blob must open as ZIP and pass testzip().",
                "Original BSP must remain untouched; write to a new output location only.",
                "Early reinjection support should only accept BSPs where the pak is at EOF or the layout is explicitly handled.",
                "If reinjection would require shifting later lumps or unknown trailing data, block the operation.",
            ],
            "hard_blockers": [
                "invalid_bsp_header",
                "bsp_header_too_short",
                "pak_lump_header_missing",
                "pak_missing_or_empty",
                "pak_out_of_range",
                "pak_not_zip",
                "pak_zip_invalid",
                "pak_not_at_eof",
                "read_error",
            ],
            "phase2_candidate_rule": "pak_zip_valid && pak_at_eof && no blockers",
        },
        "scan_errors": scan_errors,
        "items": [
            {
                "relative_path": item.relative_path,
                "bsp_path": item.bsp_path,
                "bsp_size": item.bsp_size,
                "status": item.status,
                "message": item.message,
                "version": item.version,
                "pak_offset": item.pak_offset,
                "pak_size": item.pak_size,
                "pak_percent_of_bsp": item.pak_percent_of_bsp,
                "pak_zip_valid": item.pak_zip_valid,
                "pak_entry_count": item.pak_entry_count,
                "pak_at_eof": item.pak_at_eof,
                "staging_eligible": item.staging_eligible,
                "staging_status": item.staging_status,
                "staging_dir": item.staging_dir,
                "staged_file_count": item.staged_file_count,
                "staged_total_bytes": item.staged_total_bytes,
                "inventory_by_extension": list(item.inventory_by_extension or []),
                "phase2_candidate": item.phase2_candidate,
                "phase2_blockers": list(item.phase2_blockers or []),
            }
            for item in bsp_items
        ],
    }


def _write_summary(summary_path: Path, summary: dict) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def run(config: RunConfig) -> int:
    started_at = _iso_now()
    print(f"MAPSCAN_WORK_DIR: {config.work_dir}")
    config.work_dir.mkdir(parents=True, exist_ok=True)
    addon_total_bytes = 0
    scan_errors: list[str] = []
    bsp_items: list[BspScanItem] = []

    try:
        print("== Step 1/4: Scan addon size ==")
        addon_total_bytes, size_errors = _scan_total_bytes(config.root, config.cancel_file)

        print("== Step 2/4: Scan BSP files ==")
        bsp_paths, scan_errors = _find_map_bsp_files(config.root, config.cancel_file)
        scan_errors.extend(size_errors)
        total = len(bsp_paths)
        for index, bsp_path in enumerate(bsp_paths, start=1):
            _raise_if_cancelled(config.cancel_file, context="BSP analysis")
            relative = _safe_relative(bsp_path, config.root)
            print(f"=== ({index}/{total}) BSP: {relative}")
            item = _analyze_bsp(bsp_path, config.root, config.cancel_file)
            bsp_items.append(item)
            print(
                f"  BSP={item.bsp_size} bytes | PAK={item.pak_size} bytes | "
                f"{item.pak_percent_of_bsp:.2f}% | zip_valid={item.pak_zip_valid} | "
                f"phase2_candidate={item.phase2_candidate}"
            )

        print("== Step 3/4: Extract pak staging ==")
        stage_total = sum(1 for item in bsp_items if item.staging_eligible)
        staged_index = 0
        for item in bsp_items:
            if not item.staging_eligible:
                if item.phase2_blockers:
                    print(f"[STAGE] Skipped {item.relative_path}: {', '.join(item.phase2_blockers)}")
                continue
            staged_index += 1
            print(f"=== ({staged_index}/{stage_total}) STAGE: {item.relative_path}")
            _stage_bsp_pak(config, item, staged_index)
            if item.staging_status == "extracted":
                print(
                    f"[STAGE] Extracted to {item.staging_dir} "
                    f"({item.staged_file_count} file(s), {item.staged_total_bytes} bytes)"
                )
            elif item.staging_status == "failed":
                print(f"[STAGE][WARN] Failed staging for {item.relative_path}: {item.message}")

        print("== Step 4/4: Write summary ==")
        summary = _build_summary(
            config=config,
            started_at=started_at,
            finished_at=_iso_now(),
            exit_code=0,
            addon_total_bytes=addon_total_bytes,
            bsp_items=bsp_items,
            scan_errors=scan_errors,
            cancelled=False,
        )
        summary_path = config.work_dir / SUMMARY_NAME
        _write_summary(summary_path, summary)
        print(f"Found BSP files: {summary['counts']['bsp_total']}")
        print(f"Valid pak ZIPs: {summary['counts']['bsp_with_valid_zip']}")
        print(f"Staged BSPs: {summary['counts']['staged_bsp_count']}")
        print(f"Staged files: {summary['counts']['staged_files_total']}")
        print(f"Future phase-2 candidates: {summary['counts']['phase2_candidate_count']}")
        print(f"MAPSCAN_SUMMARY: {summary_path}")
        return 0
    except CancelRequested:
        summary = _build_summary(
            config=config,
            started_at=started_at,
            finished_at=_iso_now(),
            exit_code=130,
            addon_total_bytes=addon_total_bytes,
            bsp_items=bsp_items,
            scan_errors=scan_errors,
            cancelled=True,
        )
        summary_path = config.work_dir / SUMMARY_NAME
        _write_summary(summary_path, summary)
        print(f"MAPSCAN_SUMMARY: {summary_path}")
        return 130
    finally:
        _cleanup_cancel_file(config.cancel_file)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan extracted addon folders for maps/*.bsp and analyze the embedded pak lump.")
    ap.add_argument("root", help="Root folder that already contains extracted addon content")
    ap.add_argument("--work", default=None, help="Work dir for scan summaries")
    ap.add_argument("--cancel-file", default=None, help="Optional cancel token file path")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Root folder not found: {root}")
        return 2

    work_dir = Path(args.work).expanduser().resolve() if args.work else _default_work_dir(root)
    cancel_file = Path(args.cancel_file).expanduser().resolve() if args.cancel_file else None
    config = RunConfig(root=root, work_dir=work_dir, cancel_file=cancel_file)
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
