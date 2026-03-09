from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import struct
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import batch_scan_map_bsp


SUMMARY_NAME = "map_bsp_build_summary.json"
MAP_OUTPUT_SUFFIX = "_mapassets_optimized"


@dataclass
class RunConfig:
    root: Path
    work_dir: Path
    output_dir: Path
    cancel_file: Path | None = None


@dataclass
class BuildItem:
    relative_path: str
    source_bsp_path: str
    status: str
    reason: str
    eligible: bool = False
    reinjected: bool = False
    source_bsp_size: int = 0
    output_bsp_size: int = 0
    pak_offset: int = 0
    original_pak_size: int = 0
    rebuilt_pak_size: int = 0
    rebuilt_pak_path: str = ""
    rebuilt_pak_sha256: str = ""
    output_pak_sha256: str = ""
    pak_hash_match: bool = False
    pak_zip_valid_after: bool = False
    output_bsp_path: str = ""
    stage_dir: str = ""
    phase2_blockers: list[str] | None = None


class MapBuildError(RuntimeError):
    pass


class CancelRequested(MapBuildError):
    pass


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _default_output_dir(root: Path) -> Path:
    parent = root.parent
    base = parent / f"{root.name}{MAP_OUTPUT_SUFFIX}"
    if not base.exists():
        return base
    return parent / f"{root.name}{MAP_OUTPUT_SUFFIX}_{_ts()}"


def _raise_if_cancelled(cancel_file: Path | None, *, context: str = "") -> None:
    if cancel_file and cancel_file.exists():
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


def _load_scan_summary(config: RunConfig) -> dict:
    summary_path = config.work_dir / batch_scan_map_bsp.SUMMARY_NAME
    if not summary_path.exists():
        raise MapBuildError(f"Missing map scan summary: {summary_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    root_from_summary = Path(summary.get("run", {}).get("root", "")).resolve()
    if root_from_summary != config.root.resolve():
        raise MapBuildError(
            f"Scan summary root does not match requested root. "
            f"Summary={root_from_summary} Requested={config.root.resolve()}"
        )
    return summary


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _compare_file_ranges(path_a: Path, path_b: Path, ranges: list[tuple[int, int]]) -> bool:
    with path_a.open("rb") as fa, path_b.open("rb") as fb:
        for start, length in ranges:
            if length <= 0:
                continue

            fa.seek(start)
            fb.seek(start)
            remaining = length
            while remaining > 0:
                chunk_size = min(1024 * 1024, remaining)
                a = fa.read(chunk_size)
                b = fb.read(chunk_size)
                if a != b:
                    return False
                remaining -= chunk_size

    return True


def _sanitize_name(relative_path: str) -> str:
    return batch_scan_map_bsp._sanitize_stage_name(relative_path)


def _safe_arcname(stage_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(stage_root).as_posix()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise MapBuildError(f"Unsafe staged path for ZIP rebuild: {file_path}")
    return rel


def _build_rebuilt_zip(stage_dir: Path, destination_zip: Path, cancel_file: Path | None) -> tuple[int, int]:
    if not stage_dir.exists() or not stage_dir.is_dir():
        raise MapBuildError(f"Staging directory not found: {stage_dir}")

    files = sorted((path for path in stage_dir.rglob("*") if path.is_file()), key=lambda p: str(p).lower())
    if not files:
        raise MapBuildError(f"Staging directory is empty: {stage_dir}")

    destination_zip.parent.mkdir(parents=True, exist_ok=True)
    if destination_zip.exists():
        destination_zip.unlink()

    with zipfile.ZipFile(destination_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in files:
            _raise_if_cancelled(cancel_file, context="ZIP rebuild")
            archive.write(file_path, arcname=_safe_arcname(stage_dir, file_path))

    if destination_zip.stat().st_size <= 0:
        raise MapBuildError(f"Rebuilt pak ZIP is empty: {destination_zip}")

    with zipfile.ZipFile(destination_zip, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise MapBuildError(f"Rebuilt pak ZIP failed validation: {bad_member}")
        entry_count = sum(1 for info in archive.infolist() if not info.is_dir())

    return entry_count, destination_zip.stat().st_size


def _patch_pak_lump_header(output_bsp: Path, pak_offset: int, pak_size: int) -> None:
    header_offset = 8 + batch_scan_map_bsp.PAK_LUMP_INDEX * batch_scan_map_bsp.LUMP_SIZE
    with output_bsp.open("r+b") as fh:
        fh.seek(header_offset)
        fh.write(struct.pack("<ii", pak_offset, pak_size))


def _reinject_pak_eof_only(
    source_bsp: Path,
    rebuilt_zip: Path,
    temp_bsp: Path,
    expected_offset: int,
    expected_old_size: int,
    cancel_file: Path | None,
) -> tuple[int, str, str]:
    _raise_if_cancelled(cancel_file, context="BSP reinjection")

    version, pak_offset, pak_size, _ = batch_scan_map_bsp._read_bsp_pak_payload(source_bsp)
    if pak_offset != expected_offset or pak_size != expected_old_size:
        raise MapBuildError(
            f"BSP pak metadata changed since scan for {source_bsp.name}: "
            f"expected offset={expected_offset}, size={expected_old_size}; "
            f"got offset={pak_offset}, size={pak_size}"
        )

    bsp_size = source_bsp.stat().st_size
    if pak_offset + pak_size != bsp_size:
        raise MapBuildError(f"BSP is not EOF-only safe for reinjection: {source_bsp}")

    rebuilt_bytes = rebuilt_zip.read_bytes()
    rebuilt_hash = _hash_bytes(rebuilt_bytes)

    temp_bsp.parent.mkdir(parents=True, exist_ok=True)
    if temp_bsp.exists():
        temp_bsp.unlink()

    with source_bsp.open("rb") as src, temp_bsp.open("wb") as dst:
        remaining = pak_offset
        while remaining > 0:
            chunk = src.read(min(1024 * 1024, remaining))
            if not chunk:
                raise MapBuildError(f"Unexpected EOF while copying BSP prefix: {source_bsp}")
            dst.write(chunk)
            remaining -= len(chunk)

        dst.write(rebuilt_bytes)

    _patch_pak_lump_header(temp_bsp, pak_offset, len(rebuilt_bytes))

    if temp_bsp.stat().st_size != pak_offset + len(rebuilt_bytes):
        raise MapBuildError(f"Unexpected rebuilt BSP size for {temp_bsp.name}")

    _, output_pak_offset, output_pak_size, output_pak_bytes = batch_scan_map_bsp._read_bsp_pak_payload(temp_bsp)
    if output_pak_offset != pak_offset:
        raise MapBuildError(f"PAK offset changed unexpectedly in rebuilt BSP: {temp_bsp.name}")
    if output_pak_size != len(rebuilt_bytes):
        raise MapBuildError(f"PAK size mismatch after reinjection: {temp_bsp.name}")
    if output_pak_offset + output_pak_size != temp_bsp.stat().st_size:
        raise MapBuildError(f"Rebuilt BSP is no longer EOF-only after reinjection: {temp_bsp.name}")

    zip_valid, _, zip_message = batch_scan_map_bsp._validate_zip_payload(output_pak_bytes)
    if not zip_valid:
        raise MapBuildError(f"Re-read PAK is invalid after reinjection: {zip_message}")

    output_hash = _hash_bytes(output_pak_bytes)
    if output_hash != rebuilt_hash:
        raise MapBuildError(f"Re-read PAK hash mismatch after reinjection: {temp_bsp.name}")

    header_offset = 8 + batch_scan_map_bsp.PAK_LUMP_INDEX * batch_scan_map_bsp.LUMP_SIZE
    preserved = _compare_file_ranges(
        source_bsp,
        temp_bsp,
        [
            (0, header_offset + 4),
            (header_offset + 8, max(0, pak_offset - (header_offset + 8))),
        ],
    )
    if not preserved:
        raise MapBuildError(f"Unexpected non-PAK bytes changed before reinjection point: {temp_bsp.name}")

    return temp_bsp.stat().st_size, rebuilt_hash, output_hash


def _copy_root_to_temp_output(root: Path, temp_output_dir: Path) -> None:
    if temp_output_dir.exists():
        shutil.rmtree(temp_output_dir, ignore_errors=True)
    shutil.copytree(root, temp_output_dir, copy_function=shutil.copy2)


def _promote_output(temp_output_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        raise MapBuildError(f"Final output directory already exists: {output_dir}")
    temp_output_dir.replace(output_dir)


def _revalidate_output_bsp(item: BuildItem, output_root: Path) -> None:
    final_bsp = output_root / Path(item.relative_path)
    if not final_bsp.exists():
        raise MapBuildError(f"Final output BSP missing after promotion: {final_bsp}")

    _, output_pak_offset, output_pak_size, output_pak_bytes = batch_scan_map_bsp._read_bsp_pak_payload(final_bsp)
    if output_pak_offset != item.pak_offset:
        raise MapBuildError(f"Final output BSP pak offset mismatch: {final_bsp.name}")
    if output_pak_size != item.rebuilt_pak_size:
        raise MapBuildError(f"Final output BSP pak size mismatch: {final_bsp.name}")
    if output_pak_offset + output_pak_size != final_bsp.stat().st_size:
        raise MapBuildError(f"Final output BSP is not EOF-only after promotion: {final_bsp.name}")

    zip_valid, _, zip_message = batch_scan_map_bsp._validate_zip_payload(output_pak_bytes)
    if not zip_valid:
        raise MapBuildError(f"Final output BSP pak is invalid: {zip_message}")

    output_hash = _hash_bytes(output_pak_bytes)
    if output_hash != item.rebuilt_pak_sha256:
        raise MapBuildError(f"Final output BSP pak hash mismatch: {final_bsp.name}")

    item.output_bsp_path = str(final_bsp)
    item.output_bsp_size = final_bsp.stat().st_size
    item.output_pak_sha256 = output_hash
    item.pak_hash_match = output_hash == item.rebuilt_pak_sha256
    item.pak_zip_valid_after = True


def _load_stage_opt_summary(work_dir: Path) -> dict | None:
    path = work_dir / "map_stage_optimize_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_item_from_scan(entry: dict) -> BuildItem:
    relative_path = str(entry.get("relative_path", "(unknown)"))
    blockers = [str(value) for value in entry.get("phase2_blockers", []) if str(value).strip()]
    eligible = bool(entry.get("phase2_candidate", False))

    if eligible:
        status = "pending"
        reason = "pending"
    else:
        status = "unsupported"
        reason = ", ".join(blockers) if blockers else str(entry.get("message", "not_eligible"))

    return BuildItem(
        relative_path=relative_path,
        source_bsp_path=str(entry.get("bsp_path", "")),
        status=status,
        reason=reason,
        eligible=eligible,
        source_bsp_size=int(entry.get("bsp_size", 0) or 0),
        pak_offset=int(entry.get("pak_offset", 0) or 0),
        original_pak_size=int(entry.get("pak_size", 0) or 0),
        stage_dir=str(entry.get("staging_dir", "")),
        phase2_blockers=blockers,
    )


def _write_summary(summary_path: Path, summary: dict) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _scan_directory_bytes(root: Path) -> int:
    total, _ = batch_scan_map_bsp._scan_total_bytes(root, None)
    return total


def _build_summary(
    config: RunConfig,
    started_at: str,
    exit_code: int,
    cancelled: bool,
    scan_summary_path: Path,
    output_created: bool,
    output_temp_dir: Path,
    output_dir: Path,
    items: list[BuildItem],
) -> dict:
    finished_at = _iso_now()
    reinjected_items = [item for item in items if item.reinjected]
    output_exists = output_dir.exists()
    input_total = _scan_directory_bytes(config.root)
    output_total = _scan_directory_bytes(output_dir) if output_exists else 0
    original_bsp_total = sum(item.source_bsp_size for item in reinjected_items)
    output_bsp_total = sum(item.output_bsp_size for item in reinjected_items)
    original_pak_total = sum(item.original_pak_size for item in reinjected_items)
    rebuilt_pak_total = sum(item.rebuilt_pak_size for item in reinjected_items)
    addon_delta = output_total - input_total if output_exists else 0
    addon_delta_percent = round((addon_delta / input_total) * 100.0, 2) if output_exists and input_total > 0 else None

    return {
        "summary_version": 1,
        "run": {
            "root": str(config.root),
            "work_dir": str(config.work_dir),
            "scan_summary_path": str(scan_summary_path),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "cancelled": cancelled,
            "phase": "rebuild_and_reinject_eof_only",
            "output_created": output_created,
            "output_dir": str(output_dir) if output_exists else "",
            "temp_output_dir": str(output_temp_dir),
            "mode": "EOF-only",
        },
        "counts": {
            "bsp_total": len(items),
            "eligible_total": sum(1 for item in items if item.eligible),
            "reinjected_total": len(reinjected_items),
            "unsupported_total": sum(1 for item in items if item.status == "unsupported"),
            "failed_total": sum(1 for item in items if item.status == "failed"),
            "pending_total": sum(1 for item in items if item.status == "pending"),
        },
        "sizes": {
            "input_addon_total_bytes": input_total,
            "output_addon_total_bytes": output_total,
            "addon_delta_bytes": addon_delta,
            "addon_delta_percent": addon_delta_percent,
            "reinjected_source_bsp_total_bytes": original_bsp_total,
            "reinjected_output_bsp_total_bytes": output_bsp_total,
            "reinjected_source_pak_total_bytes": original_pak_total,
            "reinjected_output_pak_total_bytes": rebuilt_pak_total,
        },
        "items": [
            {
                "relative_path": item.relative_path,
                "source_bsp_path": item.source_bsp_path,
                "output_bsp_path": item.output_bsp_path,
                "status": item.status,
                "reason": item.reason,
                "eligible": item.eligible,
                "reinjected": item.reinjected,
                "source_bsp_size": item.source_bsp_size,
                "output_bsp_size": item.output_bsp_size,
                "pak_offset": item.pak_offset,
                "original_pak_size": item.original_pak_size,
                "rebuilt_pak_size": item.rebuilt_pak_size,
                "rebuilt_pak_path": item.rebuilt_pak_path,
                "rebuilt_pak_sha256": item.rebuilt_pak_sha256,
                "output_pak_sha256": item.output_pak_sha256,
                "pak_hash_match": item.pak_hash_match,
                "pak_zip_valid_after": item.pak_zip_valid_after,
                "stage_dir": item.stage_dir,
                "phase2_blockers": list(item.phase2_blockers or []),
            }
            for item in items
        ],
    }


def run(config: RunConfig) -> int:
    started_at = _iso_now()
    scan_summary_path = config.work_dir / batch_scan_map_bsp.SUMMARY_NAME
    stage_opt_summary = _load_stage_opt_summary(config.work_dir)
    temp_output_dir = Path(str(config.output_dir) + ".__tmp")
    summary_path = config.work_dir / SUMMARY_NAME
    items: list[BuildItem] = []
    output_created = False

    print(f"MAPBUILD_WORK_DIR: {config.work_dir}")
    print(f"MAPBUILD_OUTPUT_DIR: {config.output_dir}")
    config.work_dir.mkdir(parents=True, exist_ok=True)

    try:
        print("== Step 1/6: Load summaries ==")
        scan_summary = _load_scan_summary(config)
        if stage_opt_summary is None:
            raise MapBuildError("Missing stage optimization summary. Run Optimize Staging first.")

        items = [_build_item_from_scan(entry) for entry in scan_summary.get("items", [])]
        eligible_items = [item for item in items if item.eligible]
        print(f"Eligible BSPs: {len(eligible_items)} / {len(items)}")
        if not eligible_items:
            summary = _build_summary(
                config=config,
                started_at=started_at,
                exit_code=0,
                cancelled=False,
                scan_summary_path=scan_summary_path,
                output_created=False,
                output_temp_dir=temp_output_dir,
                output_dir=config.output_dir,
                items=items,
            )
            _write_summary(summary_path, summary)
            print(f"MAPBUILD_SUMMARY: {summary_path}")
            return 0

        print("== Step 2/6: Prepare output copy ==")
        if temp_output_dir.exists():
            shutil.rmtree(temp_output_dir, ignore_errors=True)
        _copy_root_to_temp_output(config.root, temp_output_dir)

        print("== Step 3/6: Rebuild pak ZIPs and reinject EOF-only ==")
        total = len(eligible_items)
        for index, item in enumerate(eligible_items, start=1):
            _raise_if_cancelled(config.cancel_file, context="BSP rebuild")
            print(f"=== ({index}/{total}) BSP: {item.relative_path}")

            source_bsp = Path(item.source_bsp_path)
            if not source_bsp.exists():
                item.status = "failed"
                item.reason = "source_bsp_missing"
                print(f"[BUILD][FAIL] {item.relative_path}: {item.reason}")
                continue

            stage_dir = Path(item.stage_dir)
            if not stage_dir.exists():
                item.status = "failed"
                item.reason = "staging_missing"
                print(f"[BUILD][FAIL] {item.relative_path}: {item.reason}")
                continue

            try:
                safe_name = _sanitize_name(item.relative_path)
                rebuilt_dir = config.work_dir / "rebuilt_paks" / f"{index:03d}_{safe_name}"
                rebuilt_zip = rebuilt_dir / "rebuilt_pak.zip"
                _entry_count, rebuilt_zip_size = _build_rebuilt_zip(stage_dir, rebuilt_zip, config.cancel_file)

                temp_bsp = config.work_dir / "rebuilt_bsps" / f"{index:03d}_{safe_name}" / source_bsp.name
                output_bsp = temp_output_dir / Path(item.relative_path)
                output_bsp.parent.mkdir(parents=True, exist_ok=True)

                output_bsp_size, rebuilt_hash, output_hash = _reinject_pak_eof_only(
                    source_bsp=source_bsp,
                    rebuilt_zip=rebuilt_zip,
                    temp_bsp=temp_bsp,
                    expected_offset=item.pak_offset,
                    expected_old_size=item.original_pak_size,
                    cancel_file=config.cancel_file,
                )

                shutil.copy2(temp_bsp, output_bsp)

                item.status = "reinjected"
                item.reason = "reinjected_eof_only"
                item.reinjected = True
                item.rebuilt_pak_path = str(rebuilt_zip)
                item.rebuilt_pak_size = rebuilt_zip_size
                item.rebuilt_pak_sha256 = rebuilt_hash
                item.output_pak_sha256 = output_hash
                item.pak_hash_match = rebuilt_hash == output_hash
                item.pak_zip_valid_after = True
                item.output_bsp_path = str(output_bsp)
                item.output_bsp_size = output_bsp_size

                print(
                    f"[BUILD][OK] {item.relative_path} | "
                    f"BSP {item.source_bsp_size} -> {item.output_bsp_size} | "
                    f"PAK {item.original_pak_size} -> {item.rebuilt_pak_size}"
                )
            except Exception as ex:
                item.status = "failed"
                item.reason = f"{type(ex).__name__}: {ex}"
                item.output_bsp_path = str(temp_output_dir / Path(item.relative_path))
                print(f"[BUILD][FAIL] {item.relative_path}: {item.reason}")

        print("== Step 4/6: Finalize output ==")
        if any(item.reinjected for item in items):
            if config.output_dir.exists():
                raise MapBuildError(f"Output directory already exists: {config.output_dir}")
            _promote_output(temp_output_dir, config.output_dir)
            output_created = True
            for item in items:
                if item.reinjected:
                    _revalidate_output_bsp(item, config.output_dir)
        else:
            shutil.rmtree(temp_output_dir, ignore_errors=True)

        print("== Step 5/6: Write summary ==")
        summary = _build_summary(
            config=config,
            started_at=started_at,
            exit_code=0,
            cancelled=False,
            scan_summary_path=scan_summary_path,
            output_created=output_created,
            output_temp_dir=temp_output_dir,
            output_dir=config.output_dir,
            items=items,
        )
        _write_summary(summary_path, summary)

        print("== Step 6/6: Complete ==")
        print(f"Reinjected BSPs: {summary['counts']['reinjected_total']}")
        print(f"Unsupported BSPs: {summary['counts']['unsupported_total']}")
        print(f"Failed BSPs: {summary['counts']['failed_total']}")
        print(f"MAPBUILD_SUMMARY: {summary_path}")
        return 0
    except CancelRequested:
        if temp_output_dir.exists():
            shutil.rmtree(temp_output_dir, ignore_errors=True)
        summary = _build_summary(
            config=config,
            started_at=started_at,
            exit_code=130,
            cancelled=True,
            scan_summary_path=scan_summary_path,
            output_created=False,
            output_temp_dir=temp_output_dir,
            output_dir=config.output_dir,
            items=items,
        )
        _write_summary(summary_path, summary)
        print(f"MAPBUILD_SUMMARY: {summary_path}")
        return 130
    except Exception as ex:
        if temp_output_dir.exists():
            shutil.rmtree(temp_output_dir, ignore_errors=True)
        summary = _build_summary(
            config=config,
            started_at=started_at,
            exit_code=1,
            cancelled=False,
            scan_summary_path=scan_summary_path,
            output_created=False,
            output_temp_dir=temp_output_dir,
            output_dir=config.output_dir,
            items=items,
        )
        summary["run"]["fatal_error"] = f"{type(ex).__name__}: {ex}"
        _write_summary(summary_path, summary)
        print(f"MAPBUILD_SUMMARY: {summary_path}")
        print(f"[ERROR] {type(ex).__name__}: {ex}")
        return 1
    finally:
        _cleanup_cancel_file(config.cancel_file)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild staged BSP pak ZIPs and reinject them back into BSPs (EOF-only).")
    ap.add_argument("root", help="Root folder that already contains extracted addon content")
    ap.add_argument("--work", required=True, help="Work dir that already contains map_bsp_scan_summary.json and staging")
    ap.add_argument("--out", default=None, help="Output addon folder (new folder, original is preserved)")
    ap.add_argument("--cancel-file", default=None, help="Optional cancel token file path")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Root folder not found: {root}")
        return 2

    work_dir = Path(args.work).expanduser().resolve()
    output_dir = Path(args.out).expanduser().resolve() if args.out else _default_output_dir(root)
    cancel_file = Path(args.cancel_file).expanduser().resolve() if args.cancel_file else None
    config = RunConfig(root=root, work_dir=work_dir, output_dir=output_dir, cancel_file=cancel_file)
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
