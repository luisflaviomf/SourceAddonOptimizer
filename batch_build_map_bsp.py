from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import batch_scan_map_bsp
import bsp_repack
import map_fps


SUMMARY_NAME = "map_bsp_build_summary.json"
MAP_OUTPUT_SUFFIX = "_mapassets_optimized"


@dataclass
class RunConfig:
    root: Path
    work_dir: Path
    output_dir: Path
    cancel_file: Path | None = None
    compress: bool = True               # LZMA lumps and pakfile entries (the engine's own format, lossless)
    keep_vtx_variants: bool = False     # keep .dx80/.sw/.xbox/.360.vtx (the PC game only reads .dx90.vtx)
    gmad: Path | None = None            # also pack the output into <output>.gma with this gmad.exe
    jobs: int = 0                       # worker processes for the LZMA compression (0: automatic)
    fps: str = "none"                   # frame-rate edits (map_fps): none, balanced or max


# Files the PC game never reads: model meshes for DirectX 8, software rendering and the consoles (only
# .dx90.vtx is loaded), and leftovers of the optimizers' own temporary files.
UNUSED_VTX = (".dx80.vtx", ".sw.vtx", ".xbox.vtx", ".360.vtx")
TEMP_PATTERNS = (".texscreen.tmp", ".partial", "__split_vtf_tmp")


def _is_temp(rel: str) -> bool:
    low = rel.lower()
    return any(p in low for p in TEMP_PATTERNS)


def _is_unused_vtx(rel: str, present: set[str]) -> bool:
    """an alternative mesh file of a model whose .dx90.vtx is there too"""
    low = rel.lower()
    for ext in UNUSED_VTX:
        if low.endswith(ext):
            return low[: -len(ext)] + ".dx90.vtx" in present
    return False


def _staged_pak(stage_dir: Path, order: list[str], keep_vtx_variants: bool) -> tuple[list[tuple[str, bytes]], int]:
    """The files of a pakfile staging folder as [(name, bytes)]: in the pakfile's original order (names as
    they were written there), then files added since, by name. Temporary files and (unless kept) the model
    mesh files the PC game never reads are left out. -> (files, files left out)."""
    files = {}
    for p in sorted(stage_dir.rglob("*"), key=lambda q: str(q).lower()):
        if p.is_file():
            files[p.relative_to(stage_dir).as_posix().lower()] = p
    present = set(files)
    out, seen, dropped = [], set(), 0
    names = [(n, n.lower()) for n in order] + [(p.relative_to(stage_dir).as_posix(), k) for k, p in files.items()]
    for name, key in names:
        if key in seen or key not in files:
            continue
        seen.add(key)
        if _is_temp(key) or (not keep_vtx_variants and _is_unused_vtx(key, present)):
            dropped += 1
            continue
        out.append((name, files[key].read_bytes()))
    return out, dropped


def _drop_unused_files(root: Path, keep_vtx_variants: bool) -> tuple[int, int]:
    """Loose files of the output addon: the same rules as the pakfile. -> (files, bytes) removed."""
    present = {p.relative_to(root).as_posix().lower() for p in root.rglob("*") if p.is_file()}
    count = size = 0
    for p in list(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix().lower()
        if _is_temp(rel) or (not keep_vtx_variants and _is_unused_vtx(rel, present)):
            size += p.stat().st_size
            count += 1
            p.unlink()
    return count, size


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
    fps: dict | None = None                 # map_fps report: what the frame-rate edits changed


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




def _sanitize_name(relative_path: str) -> str:
    return batch_scan_map_bsp._sanitize_stage_name(relative_path)












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
    if _hash_file(final_bsp) != item.rebuilt_pak_sha256:
        raise MapBuildError(f"Final output BSP differs from the rebuilt one: {final_bsp.name}")
    item.output_bsp_path = str(final_bsp)
    item.output_bsp_size = final_bsp.stat().st_size


def _addon_folders(root: Path) -> list[Path]:
    """The addon folders under the output: root itself when it holds maps/ or addon.json, else every folder
    below (up to 3 levels) that does (an unpacked .gma lands in <archive>_extraido)."""
    def is_addon(p: Path) -> bool:
        return (p / "maps").is_dir() or (p / "addon.json").is_file()
    if is_addon(root):
        return [root]
    found, level = [], [root]
    for _ in range(3):
        nxt = []
        for d in level:
            for c in sorted(p for p in d.iterdir() if p.is_dir()):
                (found if is_addon(c) else nxt).append(c)
        level = nxt
    return found


def _pack_gma(folder: Path, gmad: Path) -> Path:
    """<folder>.gma made by gmad. A folder without addon.json gets one (title = folder name, type map):
    gmad will not pack without it."""
    import subprocess
    meta = folder / "addon.json"
    if not meta.exists():
        meta.write_text(json.dumps({"title": folder.name, "type": "map", "tags": [], "ignore": []}, indent=2),
                        encoding="utf-8")
        print("[BUILD] addon.json written (title = folder name, type map)")
    _thumbs_for_gma(folder)
    gma = folder.with_suffix(".gma") if folder.suffix == "" else folder.parent / (folder.name + ".gma")
    if gma.exists():
        gma.unlink()
    # -warninvalid: a file outside gmad's whitelist is left out of the .gma (the Workshop would refuse it
    # anyway) instead of failing the whole pack; the folder result keeps it.
    cmd = [str(gmad), "create", "-folder", str(folder), "-out", str(gma), "-warninvalid"]
    print("+ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not gma.exists():
        raise MapBuildError("gmad create failed: " + (proc.stdout + proc.stderr).strip()[-800:])
    left_out = _gmad_rejected(proc.stdout)
    if left_out:
        shown = ", ".join(left_out[:10]) + (f" (+{len(left_out) - 10})" if len(left_out) > 10 else "")
        print(f"[WARN] {len(left_out)} file(s) left out of {gma.name} (not allowed by gmad): {shown}")
    print(f"MAPBUILD_GMA: {gma}")
    return gma


def _thumbs_for_gma(folder: Path) -> None:
    """maps/<map>.png (old addons, mostly .bin) is not allowed in a .gma; the game shows the map icon from
    maps/thumb/<map>.png, so a copy goes there when the map has none."""
    maps = folder / "maps"
    if not maps.is_dir():
        return
    for png in sorted(maps.glob("*.png")):
        thumb = maps / "thumb" / png.name
        if (maps / (png.stem + ".bsp")).is_file() and not thumb.exists():
            thumb.parent.mkdir(exist_ok=True)
            shutil.copy2(png, thumb)
            print(f"[BUILD] map icon copied to maps/thumb/{png.name} (maps/{png.name} cannot go into a .gma)")


def _gmad_rejected(stdout: str) -> list[str]:
    """Files gmad -warninvalid skipped: each '[Not allowed by whitelist]' line follows the file's line."""
    lines = [line.strip() for line in stdout.splitlines()]
    return [lines[i - 1] for i, line in enumerate(lines) if i > 0 and line.startswith("[Not allowed")]


def _fps_edits(src: bytes, config: RunConfig) -> tuple[bytes, dict]:
    """map_fps.apply, with its report printed."""
    out, report = map_fps.apply(src, config.fps)
    changes = ", ".join(f"{k} {v}" for k, v in report.get("entities", {}).items() if v)
    print(f"[BUILD][FPS] {config.fps}: " + (changes or "nothing to change"))
    return out, report


def _read_stage_manifest(stage_dir: Path) -> dict:
    """stage_manifest.json next to pak_contents (written by mapscan)."""
    path = stage_dir.parent / "stage_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
            "phase": "rebuild_whole_bsp",
            "output_created": output_created,
            "output_dir": str(output_dir) if output_exists else "",
            "temp_output_dir": str(output_temp_dir),
            "mode": "LZMA" if config.compress else "uncompressed",
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
                "fps": item.fps or {},
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
            print("[WARN] No stage optimization summary: the pakfiles go back with the files as they were extracted")

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

        print("== Step 3/6: Rebuild the BSPs (" + ("LZMA lumps and pakfile" if config.compress else "uncompressed") + ") ==")
        total = len(eligible_items)
        from texlab.jobs import auto_jobs, process_pool
        pool = process_pool(config.jobs or auto_jobs(0.4)) if config.compress else None
        map_fn = (lambda fn, items: pool.map(fn, items, chunksize=4)) if pool else map
        for index, item in enumerate(eligible_items, start=1):
            _raise_if_cancelled(config.cancel_file, context="BSP rebuild")
            print(f"=== ({index}/{total}) BSP: {item.relative_path}")

            source_bsp = Path(item.source_bsp_path)
            if not source_bsp.exists():
                item.status = "failed"
                item.reason = "source_bsp_missing"
                print(f"[BUILD][FAIL] {item.relative_path}: {item.reason}")
                continue

            stage_dir = Path(item.stage_dir) if item.stage_dir else None
            try:
                src_bytes = source_bsp.read_bytes()
                pak = None
                dropped = 0
                if stage_dir is not None and stage_dir.exists():
                    manifest = _read_stage_manifest(stage_dir)
                    pak, dropped = _staged_pak(stage_dir, manifest.get("pak_order", []), config.keep_vtx_variants)
                if config.fps != "none":
                    src_bytes, fps_report = _fps_edits(src_bytes, config)
                    item.fps = fps_report
                out_bytes = bsp_repack.repack(src_bytes, pak, compress=config.compress, map_fn=map_fn)
                problems = bsp_repack.verify(src_bytes, out_bytes, pak)
                if problems:
                    raise MapBuildError("rebuilt BSP does not hold the expected content: " + ", ".join(problems[:5]))

                output_bsp = temp_output_dir / Path(item.relative_path)
                output_bsp.parent.mkdir(parents=True, exist_ok=True)
                tmp_out = output_bsp.with_name(output_bsp.name + ".partial")
                tmp_out.write_bytes(out_bytes)
                tmp_out.replace(output_bsp)

                lumps_out = bsp_repack.lumps(out_bytes)
                item.status = "reinjected"
                item.reason = "rebuilt_compressed" if config.compress else "rebuilt"
                item.reinjected = True
                item.rebuilt_pak_size = lumps_out[bsp_repack.LUMP_PAK][1]
                item.rebuilt_pak_sha256 = _hash_bytes(out_bytes)
                item.output_pak_sha256 = item.rebuilt_pak_sha256
                item.pak_hash_match = True
                item.pak_zip_valid_after = True
                item.output_bsp_path = str(output_bsp)
                item.output_bsp_size = len(out_bytes)

                print(
                    f"[BUILD][OK] {item.relative_path} | "
                    f"BSP {item.source_bsp_size} -> {item.output_bsp_size} | "
                    f"PAK {item.original_pak_size} -> {item.rebuilt_pak_size}"
                    + (f" | {dropped} unused file(s) left out of the pakfile" if dropped else "")
                )
            except Exception as ex:
                item.status = "failed"
                item.reason = f"{type(ex).__name__}: {ex}"
                item.output_bsp_path = str(temp_output_dir / Path(item.relative_path))
                print(f"[BUILD][FAIL] {item.relative_path}: {item.reason}")

        if pool is not None:
            pool.shutdown()
        removed, removed_bytes = _drop_unused_files(temp_output_dir, config.keep_vtx_variants)
        if removed:
            print(f"[BUILD] {removed} file(s) the PC game never reads removed from the addon ({removed_bytes} bytes)")

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

        gma_paths = []
        if output_created and config.gmad is not None:
            for addon in _addon_folders(config.output_dir):
                try:
                    gma_paths.append(_pack_gma(addon, config.gmad))
                except Exception as ex:  # noqa: BLE001 - the folder is the result; the .gma is a convenience
                    print(f"[WARN] .gma not created for {addon.name}: {ex}")

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
        if gma_paths:
            summary["run"]["gma_paths"] = [str(p) for p in gma_paths]
            summary["sizes"]["gma_bytes"] = sum(p.stat().st_size for p in gma_paths)
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
    ap = argparse.ArgumentParser(description="Rebuild every BSP with its staged pakfile (whole file rewritten; "
                                             "LZMA lumps and pakfile entries, the engine's own lossless format).")
    ap.add_argument("root", help="Root folder that already contains extracted addon content")
    ap.add_argument("--work", required=True, help="Work dir that already contains map_bsp_scan_summary.json and staging")
    ap.add_argument("--out", default=None, help="Output addon folder (new folder, original is preserved)")
    ap.add_argument("--cancel-file", default=None, help="Optional cancel token file path")
    ap.add_argument("--no-compress", action="store_true", help="Write lumps and pakfile files uncompressed")
    ap.add_argument("--keep-vtx-variants", action="store_true",
                    help="Keep .dx80/.sw/.xbox/.360.vtx (the PC game only reads .dx90.vtx)")
    ap.add_argument("--gmad", default=None, help="Also pack the output folder into <output>.gma with this gmad.exe")
    ap.add_argument("--jobs", type=int, default=0, help="Worker processes for the compression (0: automatic)")
    ap.add_argument("--fps", choices=("none", "balanced", "max"), default="none",
                    help="Frame-rate edits of the map's entities (map_fps)")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"Root folder not found: {root}")
        return 2

    work_dir = Path(args.work).expanduser().resolve()
    output_dir = Path(args.out).expanduser().resolve() if args.out else _default_output_dir(root)
    cancel_file = Path(args.cancel_file).expanduser().resolve() if args.cancel_file else None
    config = RunConfig(root=root, work_dir=work_dir, output_dir=output_dir, cancel_file=cancel_file,
                       compress=not args.no_compress, keep_vtx_variants=args.keep_vtx_variants,
                       fps=args.fps,
                       gmad=Path(args.gmad).expanduser().resolve() if args.gmad else None, jobs=args.jobs)
    return run(config)


def pack_main(argv: list[str] | None = None) -> int:
    """worker `mappack`: every addon folder under root (root itself when it is one) packed into
    <folder>.gma next to it. The Mapas tab runs it last, after the loose files were optimized too."""
    ap = argparse.ArgumentParser(description="Pack the addon folders under a folder into .gma files with gmad.")
    ap.add_argument("root")
    ap.add_argument("--gmad", required=True)
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    gmad = Path(args.gmad).expanduser().resolve()
    if not root.is_dir() or not gmad.is_file():
        print(f"[ERROR] folder or gmad.exe not found: {root} | {gmad}")
        return 2
    folders = _addon_folders(root)
    made = 0
    for folder in folders:
        try:
            _pack_gma(folder, gmad)
            made += 1
        except Exception as ex:  # noqa: BLE001
            print(f"[WARN] .gma not created for {folder.name}: {ex}")
    print(f"MAPPACK_DONE: {made}/{len(folders)}")
    return 0 if folders and made == len(folders) else 1


if __name__ == "__main__":
    raise SystemExit(main())
