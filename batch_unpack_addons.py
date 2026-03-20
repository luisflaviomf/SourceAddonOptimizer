from __future__ import annotations

import argparse
import io
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SUMMARY_NAME = "unpack_summary.json"
EXTRACTED_SUFFIX = "_extraido"
TEMP_SUFFIX = ".partial"


@dataclass
class RunConfig:
    root: Path
    work_dir: Path
    scan_only: bool
    existing_mode: str
    gmad_exe: Path | None
    extract_map_pak: bool
    delete_map_bsp: bool
    output_root: Path | None = None
    cancel_file: Path | None = None
    gmad_error: str | None = None


@dataclass
class ArchiveCandidate:
    source_path: Path
    relative_path: str
    kind: str
    file_size: int
    source_sig: str
    payload_sig: str
    supported: bool
    support_reason: str
    planned_output_dir: Path


@dataclass
class ItemResult:
    source_path: Path
    relative_path: str
    kind: str
    status: str
    message: str
    output_dir: Path | None
    extracted_file_count: int = 0
    temp_gma_path: Path | None = None
    map_bsp_count: int = 0
    map_pak_bsp_count: int = 0
    map_pak_file_count: int = 0
    map_bsp_deleted_count: int = 0
    elapsed_seconds: float = 0.0


class UnpackError(RuntimeError):
    pass


class UnsupportedBinError(UnpackError):
    pass


class CancelRequested(UnpackError):
    def __init__(
        self,
        message: str,
        *,
        candidates: list[ArchiveCandidate] | None = None,
        scan_errors: list[str] | None = None,
    ):
        super().__init__(message)
        self.candidates = list(candidates or [])
        self.scan_errors = list(scan_errors or [])


class InvalidBspPakError(UnpackError):
    pass


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parent


def _slugify_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "root"


def _default_work_dir(root: Path) -> Path:
    base = _runtime_root() / "work"
    return base / f"{_slugify_name(root.name)}_unpack_runs" / _ts()


def _quote_cmd(cmd: list[str]) -> str:
    def q(s: str) -> str:
        if not s:
            return '""'
        if any(ch in s for ch in (' ', "\t", '"')):
            return '"' + s.replace('"', '\\"') + '"'
        return s

    return " ".join(q(c) for c in cmd)


def _steam_root_candidates() -> list[Path]:
    env_pf86 = os.environ.get("PROGRAMFILES(X86)")
    env_pf = os.environ.get("PROGRAMFILES")
    candidates = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]
    if env_pf86:
        candidates.append(Path(env_pf86) / "Steam")
    if env_pf:
        candidates.append(Path(env_pf) / "Steam")
    return [p for p in candidates if p.exists()]


def _parse_libraryfolders(vdf_path: Path) -> list[str]:
    libs: list[str] = []
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return libs

    for line in text.splitlines():
        m = re.search(r'"path"\s+"([^"]+)"', line)
        if m:
            libs.append(m.group(1).replace("\\\\", "\\"))
            continue
        m = re.match(r'\s*"\d+"\s+"([^"]+)"', line)
        if m:
            libs.append(m.group(1).replace("\\\\", "\\"))
            continue
    return libs


def _find_gmad_in_root(root: Path) -> Path | None:
    cand = root / "bin" / "gmad.exe"
    return cand if cand.exists() else None


def detect_gmad() -> Path | None:
    env_candidates = [
        os.environ.get("GMAD_EXE"),
        os.environ.get("GMAD_PATH"),
        os.environ.get("GARRYSMOD_DIR"),
    ]
    for raw in env_candidates:
        if not raw:
            continue
        p = Path(raw)
        if p.name.lower() == "gmad.exe" and p.exists():
            return p.resolve()
        cand = _find_gmad_in_root(p)
        if cand:
            return cand.resolve()

    default = Path(r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\gmad.exe")
    if default.exists():
        return default.resolve()

    for steam_root in _steam_root_candidates():
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        libs = _parse_libraryfolders(vdf) if vdf.exists() else []
        for lib in libs:
            gmod = Path(lib) / "steamapps" / "common" / "GarrysMod"
            cand = _find_gmad_in_root(gmod)
            if cand:
                return cand.resolve()

        gmod = steam_root / "steamapps" / "common" / "GarrysMod"
        cand = _find_gmad_in_root(gmod)
        if cand:
            return cand.resolve()

    return None


def _resolve_gmad(gmad_arg: str | None) -> tuple[Path | None, str | None]:
    if gmad_arg:
        p = Path(gmad_arg).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return None, f"gmad.exe not found: {p}"
        return p, None

    detected = detect_gmad()
    if detected:
        return detected, None
    return None, "gmad.exe not found. Pass --gmad or install Garry's Mod."


def _cleanup_path(path: Path | None) -> None:
    if not path or not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _cleanup_empty_parents(path: Path | None, stop_at: Path | None = None) -> None:
    if not path:
        return
    stop_resolved = stop_at.resolve(strict=False) if stop_at else None
    cur = path.parent if path.is_file() else path
    while cur.exists() and cur.is_dir():
        try:
            if stop_resolved and cur.resolve() == stop_resolved:
                break
        except Exception:
            pass
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def _cleanup_cancel_file(cancel_file: Path | None) -> None:
    if not cancel_file:
        return
    parent = cancel_file.parent
    _cleanup_path(cancel_file)
    _cleanup_empty_parents(parent, stop_at=parent.parent)


def _cancel_requested(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _raise_if_cancelled(cancel_file: Path | None, *, context: str = "") -> None:
    if _cancel_requested(cancel_file):
        msg = "Cancelled by user"
        if context:
            msg += f" during {context}"
        raise CancelRequested(msg)


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.name


def plan_output_dir(source_path: Path, root: Path, output_root: Path | None = None) -> Path:
    if output_root:
        relative_parent = Path(_safe_relative(source_path.parent, root))
        return output_root / relative_parent / f"{source_path.stem}{EXTRACTED_SUFFIX}"
    return source_path.parent / f"{source_path.stem}{EXTRACTED_SUFFIX}"


def _decode_sig(sig: bytes) -> str:
    if not sig:
        return ""
    return sig.decode("ascii", errors="replace")


def probe_gma(path: Path) -> tuple[bool, str, str]:
    with path.open("rb") as fh:
        sig = fh.read(4)
    sig_text = _decode_sig(sig)
    if sig == b"GMAD":
        return True, sig_text, "gma_header_ok"
    if len(sig) < 4:
        return False, sig_text, "gma_too_short"
    return False, sig_text, f"invalid_gma_header:{sig_text or 'unknown'}"


def probe_bin(path: Path) -> tuple[bool, str, str]:
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    sig = b""
    with path.open("rb") as fh:
        while len(sig) < 4:
            chunk = fh.read(65536)
            if not chunk:
                break
            out = dec.decompress(chunk, max_length=4 - len(sig))
            if out:
                sig += out[: 4 - len(sig)]
            if dec.eof and len(sig) >= 4:
                break

    sig_text = _decode_sig(sig)
    if len(sig) < 4:
        if dec.eof:
            return False, sig_text, "bin_payload_too_short"
        return False, sig_text, "bin_probe_incomplete"
    if sig == b"GMAD":
        return True, sig_text, "bin_payload_gmad"
    return False, sig_text, f"unsupported_bin_payload:{sig_text or 'unknown'}"


def probe_archive(path: Path, root: Path, output_root: Path | None = None) -> ArchiveCandidate:
    kind = path.suffix.lower().lstrip(".")
    file_size = path.stat().st_size
    relative_path = _safe_relative(path, root)
    planned_output_dir = plan_output_dir(path, root, output_root)

    if kind == "gma":
        supported, source_sig, reason = probe_gma(path)
        payload_sig = ""
    elif kind == "bin":
        source_sig = ""
        supported, payload_sig, reason = probe_bin(path)
    else:
        raise ValueError(f"Unsupported archive type: {path}")

    return ArchiveCandidate(
        source_path=path,
        relative_path=relative_path,
        kind=kind,
        file_size=file_size,
        source_sig=source_sig,
        payload_sig=payload_sig,
        supported=supported,
        support_reason=reason,
        planned_output_dir=planned_output_dir,
    )


def scan_archive_candidates(
    root: Path,
    cancel_file: Path | None = None,
    output_root: Path | None = None,
) -> tuple[list[ArchiveCandidate], list[str]]:
    candidates: list[ArchiveCandidate] = []
    scan_errors: list[str] = []

    def on_error(err: OSError) -> None:
        scan_errors.append(str(err))

    try:
        for cur_dir, dirnames, filenames in os.walk(root, onerror=on_error):
            _raise_if_cancelled(cancel_file, context="scan")
            dirnames.sort(key=str.lower)
            filenames.sort(key=str.lower)
            cur_path = Path(cur_dir)
            for name in filenames:
                _raise_if_cancelled(cancel_file, context="scan")
                path = cur_path / name
                ext = path.suffix.lower()
                if ext not in (".gma", ".bin"):
                    continue
                try:
                    candidates.append(probe_archive(path.resolve(), root, output_root))
                except Exception as ex:
                    candidates.append(
                        ArchiveCandidate(
                            source_path=path.resolve(),
                            relative_path=_safe_relative(path.resolve(), root),
                            kind=ext.lstrip("."),
                            file_size=path.stat().st_size if path.exists() else 0,
                            source_sig="",
                            payload_sig="",
                            supported=False,
                            support_reason=f"probe_error:{type(ex).__name__}",
                            planned_output_dir=plan_output_dir(path.resolve(), root, output_root),
                        )
                    )
                    scan_errors.append(f"{path}: {type(ex).__name__}: {ex}")
    except CancelRequested as ex:
        candidates.sort(key=lambda c: str(c.source_path).lower())
        raise CancelRequested(str(ex), candidates=candidates, scan_errors=scan_errors) from ex

    candidates.sort(key=lambda c: str(c.source_path).lower())
    return candidates, scan_errors


def build_item_results(candidates: list[ArchiveCandidate]) -> list[ItemResult]:
    results: list[ItemResult] = []
    for candidate in candidates:
        status = "pending" if candidate.supported else "unsupported"
        message = "" if candidate.supported else candidate.support_reason
        results.append(
            ItemResult(
                source_path=candidate.source_path,
                relative_path=candidate.relative_path,
                kind=candidate.kind,
                status=status,
                message=message,
                output_dir=None,
            )
        )
    return results


def _counts_from_state(
    candidates: list[ArchiveCandidate],
    results: list[ItemResult],
    scan_errors: list[str],
    cancelled: bool,
) -> dict:
    return {
        "found_total": len(candidates),
        "gma_total": sum(1 for c in candidates if c.kind == "gma"),
        "gma_supported": sum(1 for c in candidates if c.kind == "gma" and c.supported),
        "gma_invalid": sum(1 for c in candidates if c.kind == "gma" and not c.supported),
        "bin_total": sum(1 for c in candidates if c.kind == "bin"),
        "bin_supported": sum(1 for c in candidates if c.kind == "bin" and c.supported),
        "bin_unsupported": sum(1 for c in candidates if c.kind == "bin" and not c.supported),
        "supported_total": sum(1 for c in candidates if c.supported),
        "ok": sum(1 for r in results if r.status == "ok"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "unsupported": sum(1 for r in results if r.status == "unsupported"),
        "map_bsp_found": sum(r.map_bsp_count for r in results),
        "map_bsp_with_pak": sum(r.map_pak_bsp_count for r in results),
        "map_pak_files_extracted": sum(r.map_pak_file_count for r in results),
        "map_bsp_deleted": sum(r.map_bsp_deleted_count for r in results),
        "cancelled_items": sum(1 for r in results if r.status == "cancelled"),
        "pending": sum(1 for r in results if r.status == "pending"),
        "cancelled": cancelled,
        "scan_errors": len(scan_errors),
    }


def build_summary(
    config: RunConfig,
    started_at: str,
    finished_at: str,
    exit_code: int,
    candidates: list[ArchiveCandidate],
    results: list[ItemResult],
    scan_errors: list[str],
    cancelled: bool,
) -> dict:
    items = []
    for candidate, result in zip(candidates, results):
        items.append(
            {
                "relative_path": candidate.relative_path,
                "source_path": str(candidate.source_path),
                "kind": candidate.kind,
                "file_size": candidate.file_size,
                "source_sig": candidate.source_sig,
                "payload_sig": candidate.payload_sig,
                "supported": candidate.supported,
                "support_reason": candidate.support_reason,
                "planned_output_dir": str(candidate.planned_output_dir),
                "status": result.status,
                "message": result.message,
                "output_dir": str(result.output_dir) if result.output_dir else "",
                "extracted_file_count": result.extracted_file_count,
                "temp_gma_path": str(result.temp_gma_path) if result.temp_gma_path else "",
                "map_bsp_count": result.map_bsp_count,
                "map_pak_bsp_count": result.map_pak_bsp_count,
                "map_pak_file_count": result.map_pak_file_count,
                "map_bsp_deleted_count": result.map_bsp_deleted_count,
                "elapsed_seconds": round(result.elapsed_seconds, 4),
            }
        )

    return {
        "summary_version": 1,
        "run": {
            "root": str(config.root),
            "work_dir": str(config.work_dir),
            "summary_path": str(config.work_dir / SUMMARY_NAME),
            "scan_only": config.scan_only,
            "extract_map_pak": config.extract_map_pak,
            "delete_map_bsp": config.delete_map_bsp,
            "existing_mode": config.existing_mode,
            "gmad_exe": str(config.gmad_exe) if config.gmad_exe else "",
            "gmad_error": config.gmad_error or "",
            "output_root": str(config.output_root) if config.output_root else "",
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
        },
        "counts": _counts_from_state(candidates, results, scan_errors, cancelled),
        "scan_errors": scan_errors,
        "items": items,
    }


def write_summary(summary_path: Path, summary: dict) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _temp_gma_path(work_dir: Path, candidate: ArchiveCandidate) -> Path:
    rel = Path(candidate.relative_path)
    target = (work_dir / "temp" / rel).with_suffix(".gma")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def prepare_temp_gma_from_bin(bin_path: Path, target_gma: Path, cancel_file: Path | None = None) -> int:
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    payload_sig = b""
    written = 0

    target_gma.parent.mkdir(parents=True, exist_ok=True)
    with bin_path.open("rb") as src, target_gma.open("wb") as dst:
        while True:
            _raise_if_cancelled(cancel_file, context="BIN decode")
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out = dec.decompress(chunk)
            if out:
                if len(payload_sig) < 4:
                    payload_sig += out[: 4 - len(payload_sig)]
                dst.write(out)
                written += len(out)
            if dec.eof:
                break

    sig_text = _decode_sig(payload_sig)
    if payload_sig != b"GMAD":
        _cleanup_path(target_gma)
        raise UnsupportedBinError(f"BIN payload is {sig_text or 'unknown'}, expected GMAD")
    if not dec.eof:
        _cleanup_path(target_gma)
        raise UnpackError("LZMA stream did not reach EOF")
    return written


def _combined_output(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(stdout.strip())
    if stderr.strip():
        parts.append(stderr.strip())
    return "\n".join(parts)


def _safe_zip_destination(root: Path, member_name: str) -> Path:
    member = member_name.replace("\\", "/").strip("/")
    if not member:
        raise InvalidBspPakError("ZIP entry has an empty name")

    rel_path = Path(member)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise InvalidBspPakError(f"Unsafe ZIP entry path: {member_name}")

    root_resolved = root.resolve()
    destination = (root / rel_path).resolve()
    try:
        destination.relative_to(root_resolved)
    except ValueError as ex:
        raise InvalidBspPakError(f"ZIP entry escapes extraction root: {member_name}") from ex
    return destination


def find_map_bsp_files(output_dir: Path) -> list[Path]:
    maps_dir = output_dir / "maps"
    if not maps_dir.exists() or not maps_dir.is_dir():
        return []
    return sorted((p for p in maps_dir.rglob("*.bsp") if p.is_file()), key=lambda p: str(p).lower())


def extract_bsp_pak_to_dir(bsp_path: Path, destination_dir: Path, cancel_file: Path | None = None) -> int:
    with bsp_path.open("rb") as fh:
        ident = fh.read(4)
        if ident != b"VBSP":
            raise InvalidBspPakError(f"Invalid BSP header in {bsp_path.name}: {_decode_sig(ident) or 'unknown'}")

        version_bytes = fh.read(4)
        if len(version_bytes) != 4:
            raise InvalidBspPakError(f"BSP header too short: {bsp_path.name}")

        fh.seek(8 + 40 * 16)
        lump_bytes = fh.read(16)
        if len(lump_bytes) != 16:
            raise InvalidBspPakError(f"BSP pak lump header missing: {bsp_path.name}")

        file_offset, file_length, _, _ = struct.unpack("<iiii", lump_bytes)
        if file_length <= 0:
            return 0

        file_size = bsp_path.stat().st_size
        if file_offset < 0 or file_length < 0 or file_offset + file_length > file_size:
            raise InvalidBspPakError(
                f"BSP pak lump out of range in {bsp_path.name}: offset={file_offset}, length={file_length}, size={file_size}"
            )

        fh.seek(file_offset)
        pak_bytes = fh.read(file_length)

    if len(pak_bytes) != file_length:
        raise InvalidBspPakError(f"Failed to read full BSP pak lump from {bsp_path.name}")
    if not pak_bytes.startswith(b"PK\x03\x04"):
        return 0

    file_count = 0
    try:
        with zipfile.ZipFile(io.BytesIO(pak_bytes)) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise InvalidBspPakError(f"Corrupted ZIP entry inside BSP pak: {bad_member}")

            for info in archive.infolist():
                _raise_if_cancelled(cancel_file, context="BSP pak extraction")
                if info.is_dir():
                    continue

                destination = _safe_zip_destination(destination_dir, info.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                file_count += 1
    except zipfile.BadZipFile as ex:
        raise InvalidBspPakError(f"Invalid ZIP data inside BSP pak: {bsp_path.name}") from ex

    return file_count


def extract_map_content_from_output(
    output_dir: Path,
    *,
    delete_bsp: bool,
    cancel_file: Path | None = None,
) -> tuple[int, int, int, int]:
    bsp_files = find_map_bsp_files(output_dir)
    if not bsp_files:
        return 0, 0, 0, 0

    total_bsp = len(bsp_files)
    bsp_with_pak = 0
    extracted_files = 0
    deleted_bsp = 0

    print(f"[MAP] Found {total_bsp} BSP file(s) under maps/")
    for bsp_path in bsp_files:
        _raise_if_cancelled(cancel_file, context="map detection")
        pak_file_count = extract_bsp_pak_to_dir(bsp_path, output_dir, cancel_file=cancel_file)
        relative = bsp_path.relative_to(output_dir).as_posix()
        if pak_file_count > 0:
            bsp_with_pak += 1
            extracted_files += pak_file_count
            print(f"[MAP] BSP pak extracted: {relative} ({pak_file_count} files)")
            if delete_bsp:
                try:
                    bsp_path.unlink()
                    deleted_bsp += 1
                    print(f"[MAP] BSP deleted after pak extraction: {relative}")
                except Exception as ex:
                    print(f"[MAP][WARN] Failed to delete BSP after pak extraction: {relative}: {ex}")
        else:
            print(f"[MAP] No BSP pak content: {relative}")

    if extracted_files > 0:
        print(f"[MAP] Total BSP pak files extracted: {extracted_files}")
    if deleted_bsp > 0:
        print(f"[MAP] BSP files deleted after extraction: {deleted_bsp}")

    return total_bsp, bsp_with_pak, extracted_files, deleted_bsp


def _print_process_output(text: str) -> None:
    if not text:
        return
    for line in text.splitlines():
        line = line.rstrip()
        if line:
            print(f"  {line}")


def extract_with_gmad(
    gmad_exe: Path,
    archive_path: Path,
    out_dir: Path,
    cancel_file: Path | None = None,
) -> tuple[int, str]:
    cmd = [str(gmad_exe), "extract", "-file", str(archive_path), "-out", str(out_dir), "-quiet"]
    print(f"+ {_quote_cmd(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    while True:
        if _cancel_requested(cancel_file):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            out, err = proc.communicate()
            if out:
                stdout_parts.append(out)
            if err:
                stderr_parts.append(err)
            output = _combined_output("".join(stdout_parts), "".join(stderr_parts))
            _print_process_output(output)
            raise CancelRequested("Cancelled by user during gmad extraction")
        rc = proc.poll()
        if rc is not None:
            break
        time.sleep(0.1)

    out, err = proc.communicate()
    if out:
        stdout_parts.append(out)
    if err:
        stderr_parts.append(err)
    output = _combined_output("".join(stdout_parts), "".join(stderr_parts))
    _print_process_output(output)
    return proc.returncode, output


def count_extracted_files(dest: Path) -> int:
    return sum(1 for p in dest.rglob("*") if p.is_file())


def finalize_output_dir(temp_output_dir: Path, final_output_dir: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 9):
        try:
            if final_output_dir.exists():
                _cleanup_path(final_output_dir)
            temp_output_dir.rename(final_output_dir)
            return
        except Exception as ex:
            last_error = ex
            time.sleep(0.15 * attempt)
    raise UnpackError(f"Failed to finalize output folder: {last_error}")


def _mark_supported_failed(results: list[ItemResult], message: str) -> None:
    for result in results:
        if result.status == "pending":
            result.status = "failed"
            result.message = message


def _mark_pending_cancelled(results: list[ItemResult], message: str) -> None:
    for result in results:
        if result.status == "pending":
            result.status = "cancelled"
            result.message = message


def _log_scan_summary(candidates: list[ArchiveCandidate], scan_errors: list[str]) -> None:
    gma_total = sum(1 for c in candidates if c.kind == "gma")
    gma_supported = sum(1 for c in candidates if c.kind == "gma" and c.supported)
    gma_invalid = sum(1 for c in candidates if c.kind == "gma" and not c.supported)
    bin_total = sum(1 for c in candidates if c.kind == "bin")
    bin_supported = sum(1 for c in candidates if c.kind == "bin" and c.supported)
    bin_unsupported = sum(1 for c in candidates if c.kind == "bin" and not c.supported)
    supported_total = sum(1 for c in candidates if c.supported)

    print(f"Found {len(candidates)} addon archive(s)")
    print(f"GMA: {gma_supported}/{gma_total} supported")
    if gma_invalid:
        print(f"GMA invalid: {gma_invalid}")
        for candidate in candidates:
            if candidate.kind == "gma" and not candidate.supported:
                sig = candidate.source_sig or "unknown"
                print(f"  - {candidate.relative_path} [{sig}]")
    print(f"BIN: {bin_supported}/{bin_total} supported")
    if bin_unsupported:
        print(f"BIN unsupported: {bin_unsupported}")
        for candidate in candidates:
            if candidate.kind == "bin" and not candidate.supported:
                sig = candidate.payload_sig or "unknown"
                print(f"  - {candidate.relative_path} [{sig}]")
    print(f"Supported for extraction: {supported_total}")
    if scan_errors:
        print(f"Scan warnings: {len(scan_errors)}")
        for err in scan_errors:
            print(f"  - {err}")


def _finalize_run(
    config: RunConfig,
    started_at: str,
    exit_code: int,
    candidates: list[ArchiveCandidate],
    results: list[ItemResult],
    scan_errors: list[str],
    cancelled: bool,
) -> int:
    print("")
    if config.scan_only:
        print("== Step 2/2: Finalize ==")
    else:
        print("== Step 3/3: Finalize ==")

    summary_path = config.work_dir / SUMMARY_NAME
    finished_at = _iso_now()
    summary = build_summary(
        config=config,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        candidates=candidates,
        results=results,
        scan_errors=scan_errors,
        cancelled=cancelled,
    )
    write_summary(summary_path, summary)
    counts = summary["counts"]
    print(
        "Result:"
        f" ok={counts['ok']}"
        f" skipped={counts['skipped']}"
        f" failed={counts['failed']}"
        f" unsupported={counts['unsupported']}"
        f" cancelled_items={counts['cancelled_items']}"
    )
    if cancelled:
        print("[WARN] Run cancelled by user.")
    print(f"UNPACK_SUMMARY: {summary_path}")
    print(f"UNPACK_WORK_DIR: {config.work_dir}")
    _cleanup_cancel_file(config.cancel_file)
    return exit_code


def run_scan(config: RunConfig) -> int:
    started_at = _iso_now()
    config.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"Root: {config.root}")
    print(f"Work: {config.work_dir}")
    print("")
    print("== Step 1/2: Scan addons ==")
    candidates: list[ArchiveCandidate] = []
    scan_errors: list[str] = []
    results: list[ItemResult] = []
    try:
        candidates, scan_errors = scan_archive_candidates(
            config.root,
            cancel_file=config.cancel_file,
            output_root=config.output_root,
        )
        results = build_item_results(candidates)
        _log_scan_summary(candidates, scan_errors)
        return _finalize_run(config, started_at, 0, candidates, results, scan_errors, cancelled=False)
    except CancelRequested as ex:
        candidates = list(getattr(ex, "candidates", []))
        scan_errors = list(getattr(ex, "scan_errors", []))
        print(f"[WARN] {ex}")
        results = build_item_results(candidates)
        _mark_pending_cancelled(results, "cancelled_during_scan")
        if candidates or scan_errors:
            _log_scan_summary(candidates, scan_errors)
        return _finalize_run(config, started_at, 130, candidates, results, scan_errors, cancelled=True)


def run_unpack(config: RunConfig) -> int:
    started_at = _iso_now()
    config.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"Root: {config.root}")
    print(f"Work: {config.work_dir}")
    print("")
    print("== Step 1/3: Scan addons ==")
    try:
        candidates, scan_errors = scan_archive_candidates(
            config.root,
            cancel_file=config.cancel_file,
            output_root=config.output_root,
        )
    except CancelRequested as ex:
        candidates = list(getattr(ex, "candidates", []))
        scan_errors = list(getattr(ex, "scan_errors", []))
        results = build_item_results(candidates)
        _mark_pending_cancelled(results, "cancelled_during_scan")
        print(f"[WARN] {ex}")
        if candidates or scan_errors:
            _log_scan_summary(candidates, scan_errors)
        return _finalize_run(config, started_at, 130, candidates, results, scan_errors, cancelled=True)
    results = build_item_results(candidates)
    _log_scan_summary(candidates, scan_errors)

    print("")
    print("== Step 2/3: Extract addons ==")

    if config.gmad_error or not config.gmad_exe:
        msg = config.gmad_error or "gmad.exe not available."
        print(f"[ERROR] {msg}")
        _mark_supported_failed(results, msg)
        return _finalize_run(config, started_at, 2, candidates, results, scan_errors, cancelled=False)

    supported_pairs = [(c, r) for c, r in zip(candidates, results) if c.supported]
    cancelled = False

    try:
        for index, (candidate, result) in enumerate(supported_pairs, start=1):
            _raise_if_cancelled(config.cancel_file, context="extraction")
            item_started = time.perf_counter()
            temp_output_dir = Path(str(candidate.planned_output_dir) + TEMP_SUFFIX)
            temp_gma_path = None

            print(f"=== ({index}/{len(supported_pairs)}) ADDON: {candidate.source_path}")

            try:
                if candidate.planned_output_dir.exists():
                    if config.existing_mode == "skip":
                        result.status = "skipped"
                        result.output_dir = candidate.planned_output_dir
                        result.message = "output_exists"
                        result.extracted_file_count = count_extracted_files(candidate.planned_output_dir)
                        print(f"[SKIP] Output exists: {candidate.planned_output_dir}")
                        continue
                    if config.existing_mode == "fail":
                        result.status = "failed"
                        result.message = "output_exists"
                        print(f"[FAIL] Output exists: {candidate.planned_output_dir}")
                        continue
                    if config.existing_mode == "overwrite":
                        print(f"[INFO] Existing output will be replaced: {candidate.planned_output_dir}")

                _cleanup_path(temp_output_dir)

                archive_for_extract = candidate.source_path
                if candidate.kind == "bin":
                    temp_gma_path = _temp_gma_path(config.work_dir, candidate)
                    result.temp_gma_path = temp_gma_path
                    payload_size = prepare_temp_gma_from_bin(
                        candidate.source_path,
                        temp_gma_path,
                        cancel_file=config.cancel_file,
                    )
                    archive_for_extract = temp_gma_path
                    print(f"[INFO] BIN -> GMAD temp created ({payload_size} bytes): {temp_gma_path}")

                rc, output = extract_with_gmad(
                    config.gmad_exe,
                    archive_for_extract,
                    temp_output_dir,
                    cancel_file=config.cancel_file,
                )
                if rc != 0:
                    raise UnpackError(output or f"gmad failed with exit code {rc}")
                if not temp_output_dir.exists() or not temp_output_dir.is_dir():
                    raise UnpackError(f"gmad did not create output folder: {temp_output_dir}")

                if config.extract_map_pak:
                    (
                        result.map_bsp_count,
                        result.map_pak_bsp_count,
                        result.map_pak_file_count,
                        result.map_bsp_deleted_count,
                    ) = extract_map_content_from_output(
                        temp_output_dir,
                        delete_bsp=config.delete_map_bsp,
                        cancel_file=config.cancel_file,
                    )

                extracted_files = count_extracted_files(temp_output_dir)
                if extracted_files == 0:
                    print(f"[WARN] No files extracted under: {temp_output_dir}")

                finalize_output_dir(temp_output_dir, candidate.planned_output_dir)
                result.status = "ok"
                result.output_dir = candidate.planned_output_dir
                result.message = "extracted_with_map_content" if result.map_pak_file_count > 0 else "extracted"
                result.extracted_file_count = extracted_files
                print(f"[OK] Extracted -> {candidate.planned_output_dir} ({extracted_files} files)")
            except UnsupportedBinError as ex:
                result.status = "unsupported"
                result.message = str(ex)
                print(f"[UNSUPPORTED] {candidate.relative_path}: {ex}")
                _cleanup_path(temp_output_dir)
            except CancelRequested as ex:
                cancelled = True
                result.status = "cancelled"
                result.message = str(ex)
                print(f"[WARN] {ex}")
                _cleanup_path(temp_output_dir)
                if temp_gma_path:
                    _cleanup_path(temp_gma_path)
                    _cleanup_empty_parents(temp_gma_path.parent, stop_at=config.work_dir / "temp")
                _mark_pending_cancelled(results, "cancelled_before_processing")
                break
            except KeyboardInterrupt:
                cancelled = True
                result.status = "cancelled"
                result.message = "cancelled_by_user"
                print("[WARN] Cancelled by user.")
                _cleanup_path(temp_output_dir)
                if temp_gma_path:
                    _cleanup_path(temp_gma_path)
                    _cleanup_empty_parents(temp_gma_path.parent, stop_at=config.work_dir / "temp")
                _mark_pending_cancelled(results, "cancelled_before_processing")
                break
            except Exception as ex:
                result.status = "failed"
                result.message = str(ex)
                print(f"[FAIL] {candidate.relative_path}: {ex}")
                _cleanup_path(temp_output_dir)
            finally:
                result.elapsed_seconds = time.perf_counter() - item_started
                if temp_gma_path:
                    _cleanup_path(temp_gma_path)
                    _cleanup_empty_parents(temp_gma_path.parent, stop_at=config.work_dir / "temp")
        _cleanup_empty_parents(config.work_dir / "temp", stop_at=config.work_dir)
        exit_code = 0 if not any(r.status == "failed" for r in results) else 1
        if cancelled:
            exit_code = 130
        return _finalize_run(config, started_at, exit_code, candidates, results, scan_errors, cancelled=cancelled)
    except KeyboardInterrupt:
        cancelled = True
        _mark_pending_cancelled(results, "cancelled_before_processing")
        _cleanup_empty_parents(config.work_dir / "temp", stop_at=config.work_dir)
        return _finalize_run(config, started_at, 130, candidates, results, scan_errors, cancelled=cancelled)


def _build_config(args: argparse.Namespace) -> RunConfig:
    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"[ERROR] Root folder not found: {root}")

    work_dir = Path(args.work).expanduser().resolve() if args.work else _default_work_dir(root)
    gmad_exe = None
    gmad_error = None
    if not args.scan_only:
        gmad_exe, gmad_error = _resolve_gmad(args.gmad)

    return RunConfig(
        root=root,
        work_dir=work_dir,
        scan_only=bool(args.scan_only),
        existing_mode=args.existing,
        gmad_exe=gmad_exe,
        extract_map_pak=bool(args.extract_map_pak),
        delete_map_bsp=bool(args.delete_map_bsp),
        output_root=Path(args.output_root).expanduser().resolve() if args.output_root else None,
        cancel_file=Path(args.cancel_file).expanduser().resolve() if args.cancel_file else None,
        gmad_error=gmad_error,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scan and extract Garry's Mod addon archives (.gma and supported .bin payloads)."
    )
    ap.add_argument("root", help="Root folder to scan recursively")
    ap.add_argument("--gmad", default=None, help="Path to gmad.exe")
    ap.add_argument(
        "--existing",
        default="skip",
        choices=["skip", "overwrite", "fail"],
        help="What to do when the output folder already exists",
    )
    ap.add_argument(
        "--extract-map-pak",
        action="store_true",
        help="After normal extraction, detect maps/*.bsp and extract the internal BSP pakfile into the same output folder",
    )
    ap.add_argument(
        "--delete-map-bsp",
        action="store_true",
        help="After successful BSP pak extraction, delete the extracted .bsp file from the output folder",
    )
    ap.add_argument("--scan-only", action="store_true", help="Only scan and classify archives")
    ap.add_argument("--work", default=None, help="Work dir for temp files and unpack_summary.json")
    ap.add_argument("--output-root", default=None, help="Optional root directory for extracted addon folders")
    ap.add_argument("--cancel-file", default=None, help="Internal file path used to request graceful cancellation")
    args = ap.parse_args(argv)

    config = _build_config(args)
    if config.scan_only:
        return run_scan(config)
    return run_unpack(config)


if __name__ == "__main__":
    raise SystemExit(main())
