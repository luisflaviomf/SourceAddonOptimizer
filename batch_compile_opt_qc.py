#!/usr/bin/env python3
# Batch compile *_OPT.qc files with studiomdl.exe into a local compiled/models folder,
# then restore the original .phy from a backup (original/models).

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import time
import hashlib
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
from _thread import LockType

DEFAULT_ROOT = r"C:\Users\luisf\OneDrive\Documentos\lvscrowbar"
DEFAULT_STUDIOMDL = r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe"
MAX_AUTO_COMPILE_JOBS = 4

MODELNAME_RE = re.compile(r'^\s*\$modelname\s+(".*?"|\S+)', re.IGNORECASE)
ERROR_RE = re.compile(r"(error|fatal|cannot|can't|could not|failed|missing)", re.IGNORECASE)
DIRECTIVE_RE = re.compile(r'^\s*\$(animation|sequence)\s+(".*?"|\S+)', re.IGNORECASE)
QUOTED_RE = re.compile(r'"([^"]+)"')
DUP_ANIM_RE = re.compile(r"Duplicate animation name", re.IGNORECASE)
DUP_SEQ_RE = re.compile(r"Duplicate sequence name", re.IGNORECASE)

GAMEINFO_TEMPLATE = """\
"GameInfo"
{
    game    "Temp Compile"
    title   ""
    type    multiplayer_only

    FileSystem
    {
        SearchPaths
        {
            mod+mod_write+default_write_path    |gameinfo_path|.
            game+game_write                     |gameinfo_path|.
            game                                |gameinfo_path|.
        }
    }
}
"""

MDL_MAGIC = b"IDST"
MDL_VERSIONS = {48, 49}
TEXTURE_STRUCT_SIZE = 64


def _read_mdl_header(data: bytes):
    # Header parsing below reads past the first 128 bytes (ints block),
    # so we need a bit more data available.
    if len(data) < 256:
        return None, "mdl_too_short"
    if data[:4] != MDL_MAGIC:
        return None, "mdl_bad_magic"
    try:
        off = 4
        version = struct.unpack_from("<i", data, off)[0]
        off += 4
        if version not in MDL_VERSIONS:
            return None, f"mdl_version_{version}"
        _checksum = struct.unpack_from("<i", data, off)[0]
        off += 4
        off += 64  # name
        _length = struct.unpack_from("<i", data, off)[0]
        off += 4
        # 6 vectors of 3 floats each
        off += 6 * 3 * 4
        _flags = struct.unpack_from("<i", data, off)[0]
        off += 4
        # fields up to skin table
        ints = struct.unpack_from("<24i", data, off)
        off += 24 * 4
        header = {
            "version": version,
            "numtextures": ints[12],
            "textureindex": ints[13],
            "numskinref": ints[16],
            "numskinfamilies": ints[17],
            "skinindex": ints[18],
        }
        return header, None
    except Exception:
        return None, "mdl_parse_error"


def _read_mdl_textures(data: bytes, header: dict):
    num = int(header.get("numtextures", 0))
    tex_index = int(header.get("textureindex", 0))
    if num <= 0:
        return []
    if tex_index <= 0 or tex_index >= len(data):
        return None
    names = []
    for i in range(num):
        base = tex_index + i * TEXTURE_STRUCT_SIZE
        if base + 4 > len(data):
            return None
        name_index = struct.unpack_from("<i", data, base)[0]
        if name_index <= 0:
            names.append("")
            continue
        name_off = base + name_index
        if name_off < 0 or name_off >= len(data):
            names.append("")
            continue
        end = data.find(b"\x00", name_off)
        if end == -1:
            end = len(data)
        names.append(data[name_off:end].decode("ascii", "ignore"))
    return names


def _read_mdl_skin_table(data: bytes, header: dict):
    numref = int(header.get("numskinref", 0))
    numfam = int(header.get("numskinfamilies", 0))
    skinindex = int(header.get("skinindex", 0))
    if numref <= 0 or numfam <= 0:
        return []
    count = numref * numfam
    needed = skinindex + (count * 2)
    if skinindex <= 0 or needed > len(data):
        return None
    fmt = "<" + ("H" * count)
    vals = list(struct.unpack_from(fmt, data, skinindex))
    return [vals[i * numref : (i + 1) * numref] for i in range(numfam)]


def _read_mdl_header_from_file(path: Path):
    try:
        with path.open("rb") as f:
            data = f.read(512)
    except Exception:
        return None, "mdl_read_error"
    return _read_mdl_header(data)


def _read_cstring_ascii(f, offset: int, *, max_len: int = 4096) -> str:
    if offset < 0:
        return ""
    try:
        f.seek(offset)
        data = f.read(max_len)
    except Exception:
        return ""
    if not data:
        return ""
    end = data.find(b"\x00")
    if end == -1:
        end = len(data)
    return data[:end].decode("ascii", "ignore")


def _read_mdl_textures_from_file(path: Path, header: dict):
    num = int(header.get("numtextures", 0))
    tex_index = int(header.get("textureindex", 0))
    if num <= 0:
        return []
    if tex_index <= 0:
        return None
    try:
        with path.open("rb") as f:
            names = []
            for i in range(num):
                base = tex_index + i * TEXTURE_STRUCT_SIZE
                f.seek(base)
                block = f.read(TEXTURE_STRUCT_SIZE)
                if len(block) < 4:
                    return None
                name_index = struct.unpack_from("<i", block, 0)[0]
                if name_index <= 0:
                    names.append("")
                    continue
                name_off = base + name_index
                names.append(_read_cstring_ascii(f, int(name_off)))
            return names
    except Exception:
        return None


def _read_mdl_skin_table_bytes_from_file(path: Path, header: dict):
    numref = int(header.get("numskinref", 0))
    numfam = int(header.get("numskinfamilies", 0))
    skinindex = int(header.get("skinindex", 0))
    if numref <= 0 or numfam <= 0:
        return b""
    count = numref * numfam
    length = count * 2
    if skinindex <= 0 or length <= 0:
        return None
    try:
        with path.open("rb") as f:
            f.seek(int(skinindex))
            data = f.read(int(length))
            if len(data) != length:
                return None
            return data
    except Exception:
        return None


def restore_skin_table(orig_mdl: Path, out_mdl: Path):
    if not orig_mdl.exists():
        return {"status": "skipped", "reason": "missing_original_mdl"}
    if not out_mdl.exists():
        return {"status": "skipped", "reason": "missing_output_mdl"}
    header_o, err_o = _read_mdl_header_from_file(orig_mdl)
    header_out, err_out = _read_mdl_header_from_file(out_mdl)
    if err_o:
        return {"status": "error", "reason": f"orig_{err_o}"}
    if err_out:
        return {"status": "error", "reason": f"out_{err_out}"}

    if (
        header_o["numskinref"] != header_out["numskinref"]
        or header_o["numskinfamilies"] != header_out["numskinfamilies"]
    ):
        return {"status": "skipped", "reason": "skinref_mismatch"}

    tex_o = _read_mdl_textures_from_file(orig_mdl, header_o)
    tex_out = _read_mdl_textures_from_file(out_mdl, header_out)
    if tex_o is None or tex_out is None:
        return {"status": "error", "reason": "texture_parse_error"}
    if len(tex_o) != len(tex_out):
        return {"status": "skipped", "reason": "texture_count_mismatch"}
    if [t.lower() for t in tex_o] != [t.lower() for t in tex_out]:
        return {"status": "skipped", "reason": "texture_list_mismatch"}

    skin_o = _read_mdl_skin_table_bytes_from_file(orig_mdl, header_o)
    skin_out = _read_mdl_skin_table_bytes_from_file(out_mdl, header_out)
    if skin_o is None or skin_out is None:
        return {"status": "error", "reason": "skin_parse_error"}
    if skin_o == skin_out:
        return {"status": "skipped", "reason": "already_match"}

    try:
        with out_mdl.open("r+b") as f:
            f.seek(int(header_out["skinindex"]))
            f.write(skin_o)
    except Exception:
        return {"status": "error", "reason": "skin_write_error"}

    return {"status": "applied", "changed": True}


def strip_line_comments(line: str) -> str:
    out = []
    in_quote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        if not in_quote and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def parse_modelname(qc_path: Path):
    try:
        text = qc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    for raw_line in text.splitlines():
        line = strip_line_comments(raw_line)
        if not line:
            continue
        m = MODELNAME_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def normalize_modelname(raw: str):
    if not raw:
        return None, "missing_modelname"
    name = raw.strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]
    name = name.strip().replace("\\", "/")

    while name.startswith("./"):
        name = name[2:]
    if name.startswith("/"):
        name = name[1:]

    lower = name.lower()
    idx = lower.find("models/")
    if idx != -1:
        name = name[idx + len("models/") :]
        if name.startswith("/"):
            name = name[1:]

    if re.match(r"^[a-zA-Z]:", name):
        return None, "absolute_modelname"
    if not name:
        return None, "empty_modelname"
    if not name.lower().endswith(".mdl"):
        name += ".mdl"
    return name, None


def is_in_output_dir(path: Path) -> bool:
    return any(part.lower() == "output" for part in path.parts)


def find_opt_qcs(root: Path):
    qcs = []
    for p in root.rglob("*.qc"):
        if is_in_output_dir(p):
            continue
        # Case-sensitive match for _OPT.qc
        if p.name.endswith("_OPT.qc"):
            qcs.append(p)
    return sorted(qcs, key=lambda x: str(x).lower())


def gather_compiled_files(compiled_dir: Path, base: str):
    if not compiled_dir.exists():
        return []
    base_lower = base.lower()
    allowed_ext = {".mdl", ".vvd", ".vtx", ".phy", ".ani"}
    files = []
    for p in compiled_dir.iterdir():
        if not p.is_file():
            continue
        name_lower = p.name.lower()
        if not name_lower.startswith(base_lower + "."):
            continue
        if Path(name_lower).suffix not in allowed_ext:
            continue
        files.append(p)
    return sorted(files, key=lambda x: x.name.lower())


def ensure_gameinfo(game_dir: Path):
    gameinfo = game_dir / "gameinfo.txt"
    if gameinfo.exists():
        return False, gameinfo
    game_dir.mkdir(parents=True, exist_ok=True)
    gameinfo.write_text(GAMEINFO_TEMPLATE, encoding="ascii", errors="replace")
    return True, gameinfo


def _resolve_compile_jobs(value: int) -> int:
    if value <= 0:
        cpu = os.cpu_count() or 1
        return max(1, min(cpu, MAX_AUTO_COMPILE_JOBS))
    return max(1, value)


def _prepare_job_dirs(out_dir: Path, jobs: int):
    compile_tmp_dir = out_dir / "compile_tmp"
    job_dirs = []
    for i in range(jobs):
        job_dir = compile_tmp_dir / f"job_{i + 1:02d}"
        ensure_gameinfo(job_dir)
        (job_dir / "models").mkdir(parents=True, exist_ok=True)
        job_dirs.append(job_dir)
    return compile_tmp_dir, job_dirs


def _safe_log_id(qc: Path, idx: int, suffix: str) -> str:
    # Keep paths short to avoid Windows MAX_PATH issues; include a hash for uniqueness.
    stem = qc.stem
    short = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:48]
    h = hashlib.sha1(str(qc).encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{idx:05d}_{short}_{h}{suffix}"


def _merge_job_outputs(job_dirs: list[Path], out_models_dir: Path):
    conflicts = []
    out_models_dir.mkdir(parents=True, exist_ok=True)
    for job_dir in job_dirs:
        job_models_dir = job_dir / "models"
        if not job_models_dir.exists():
            continue
        for src in job_models_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(job_models_dir)
            dst = out_models_dir / rel
            if dst.exists():
                conflicts.append(str(rel))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return conflicts


def run_studiomdl(
    qc_path: Path,
    studiomdl: Path,
    game_dir: Path,
    log_path: Path,
    *,
    verbose: bool,
    log_detail: str,
):
    cmd = [
        str(studiomdl),
        "-game",
        str(game_dir),
        "-nop4",
        str(qc_path.name),
    ]
    if verbose:
        cmd.insert(-1, "-verbose")
    last_lines = deque(maxlen=60)
    error_lines = []

    proc = subprocess.Popen(
        cmd,
        cwd=str(qc_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None

    if log_detail == "full":
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"QC: {qc_path}\n")
            log.write(f"CWD: {qc_path.parent}\n")
            log.write("CMD: " + " ".join(cmd) + "\n\n")

            for line in proc.stdout:
                log.write(line)
                last_lines.append(line.rstrip("\n"))
                if ERROR_RE.search(line):
                    err = re.sub(r"\s+", " ", line.strip())
                    if err:
                        error_lines.append(err)
            rc = proc.wait()
            log.write(f"\n[EXIT] returncode={rc}\n")
    else:
        for line in proc.stdout:
            last_lines.append(line.rstrip("\n"))
            if ERROR_RE.search(line):
                err = re.sub(r"\s+", " ", line.strip())
                if err:
                    error_lines.append(err)
        rc = proc.wait()

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write(f"QC: {qc_path}\n")
            log.write(f"CWD: {qc_path.parent}\n")
            log.write("CMD: " + " ".join(cmd) + "\n\n")
            if error_lines:
                log.write("[ERROR_LINES]\n")
                for e in error_lines:
                    log.write(e + "\n")
                log.write("\n")
            log.write("[LAST_LINES]\n")
            for l in last_lines:
                log.write(l + "\n")
            log.write(f"\n[EXIT] returncode={rc}\n")

    return rc, list(last_lines), error_lines


def _count_braces(line: str) -> int:
    count = 0
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "{":
            count += 1
        elif ch == "}":
            count -= 1
    return count


def _block_score(lines: list[str]) -> tuple[int, int]:
    nonempty = 0
    chars = 0
    for line in lines:
        s = strip_line_comments(line).strip()
        if not s:
            continue
        nonempty += 1
        chars += len(s)
    return nonempty, chars


def _extract_name_and_file(line: str) -> tuple[str | None, str | None]:
    stripped = strip_line_comments(line)
    m = DIRECTIVE_RE.match(stripped)
    if not m:
        return None, None
    name = m.group(2).strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]

    quoted = QUOTED_RE.findall(stripped)
    file_tok = None
    if len(quoted) >= 2:
        file_tok = quoted[1]
    elif len(quoted) == 1:
        if quoted[0] != name:
            file_tok = quoted[0]
    else:
        toks = stripped.split()
        if len(toks) >= 3:
            file_tok = toks[2].strip('"')
    return name, file_tok


def _scan_directive_blocks(lines: list[str]):
    blocks = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = strip_line_comments(raw)
        m = DIRECTIVE_RE.match(stripped)
        if not m:
            i += 1
            continue

        kind = m.group(1).lower()
        name, file_tok = _extract_name_and_file(stripped)
        if not name:
            i += 1
            continue

        start = i
        block_lines = [raw]
        depth = _count_braces(stripped)
        j = i + 1

        if depth == 0 and "{" not in stripped:
            # Look ahead for brace on next non-empty line (common QC style).
            k = j
            pending = []
            found_brace = False
            while k < len(lines):
                s = strip_line_comments(lines[k])
                if not s.strip():
                    pending.append(lines[k])
                    k += 1
                    continue
                if "{" in s:
                    found_brace = True
                    block_lines.extend(pending)
                    block_lines.append(lines[k])
                    depth += _count_braces(s)
                    k += 1
                break
            if found_brace:
                j = k
            else:
                end = start
                blocks.append(
                    {
                        "kind": kind,
                        "name": name,
                        "file": file_tok,
                        "start": start,
                        "end": end,
                        "lines": block_lines,
                    }
                )
                i = end + 1
                continue

        while depth > 0 and j < len(lines):
            line = lines[j]
            block_lines.append(line)
            depth += _count_braces(strip_line_comments(line))
            j += 1

        end = j - 1
        blocks.append(
            {
                "kind": kind,
                "name": name,
                "file": file_tok,
                "start": start,
                "end": end,
                "lines": block_lines,
            }
        )
        i = end + 1
    return blocks


def sanitize_qc_duplicates(qc_path: Path, out_path: Path):
    try:
        lines = qc_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except Exception:
        return False, []

    blocks = _scan_directive_blocks(lines)
    by_key = {}
    for blk in blocks:
        key = (blk["kind"], blk["name"])
        by_key.setdefault(key, []).append(blk)

    skip_ranges = []
    report = []

    for (kind, name), items in by_key.items():
        if len(items) <= 1:
            continue
        scored = sorted(
            items, key=lambda b: (_block_score(b["lines"]), -b["start"]), reverse=True
        )
        keep = scored[0]
        removed = scored[1:]
        for blk in removed:
            skip_ranges.append((blk["start"], blk["end"]))
        files = sorted({b.get("file") or "" for b in items if b.get("file") is not None})
        report.append(
            f"duplicate ${kind} '{name}': kept line {keep['start']+1}, removed {len(removed)}"
            + (f", files={files}" if files else "")
        )

    if not skip_ranges:
        return False, []

    skip = set()
    for start, end in skip_ranges:
        for idx in range(start, end + 1):
            skip.add(idx)

    new_lines = [line for idx, line in enumerate(lines) if idx not in skip]
    out_path.write_text("".join(new_lines), encoding="utf-8", errors="replace")
    return True, report


def _has_duplicate_name_error(error_lines: list[str]) -> bool:
    for line in error_lines:
        if DUP_ANIM_RE.search(line) or DUP_SEQ_RE.search(line):
            return True
    return False


def _print_locked(lock: LockType | None, text: str) -> None:
    if lock:
        with lock:
            print(text)
    else:
        print(text)


def _exception_result(entry: dict, exc: Exception, *, log_path: Path | None = None):
    msg = f"exception={type(exc).__name__}: {exc}"
    qc = entry.get("qc")
    qc_dir = entry.get("qc_dir")
    return {
        "index": entry.get("index"),
        "qc_path": str(qc) if qc else None,
        "qc_dir": str(qc_dir) if qc_dir else None,
        "log_path": str(log_path) if log_path else None,
        "log_path_initial": None,
        "log_path_autofix": None,
        "status": "fail",
        "returncode": 1,
        "duration_sec": 0.0,
        "modelname_raw": entry.get("modelname_raw"),
        "model_rel": entry.get("model_rel"),
        "expected_mdl": None,
        "compiled_dir": None,
        "compiled_files": [],
        "autofix_applied": False,
        "autofix_retry": False,
        "autofix_qc_path": None,
        "autofix_details": [],
        "phy_restore_requested": None,
        "phy_restore_attempted": None,
        "phy_backup_path": None,
        "phy_backup_found": None,
        "phy_out_path": None,
        "phy_restored_out": None,
        "phy_restore_errors": [],
        "skin_restore_attempted": None,
        "skin_restore_applied": None,
        "skin_restore_reason": None,
        "skin_restore_error": None,
        "last_lines": [],
        "error_lines": [msg],
        "message": msg,
    }


def _compile_one(
    entry: dict,
    *,
    total: int,
    studiomdl: Path,
    compile_dir: Path,
    compile_models_dir: Path,
    out_dir: Path,
    log_detail: str,
    verbose: bool,
    restore_phy: bool,
    phy_backup_models_dir: Path,
    require_phy_backup: bool,
    skin_backup_models_dir: Path | None,
    print_lock: LockType | None,
):
    qc = entry["qc"]
    idx = entry["index"]
    qc_dir = entry["qc_dir"]

    _print_locked(print_lock, f"\n=== ({idx}/{total}) QC: {qc} ===")

    log_id = _safe_log_id(qc, int(idx), "")
    log_path = qc_dir / "output" / f"compile_studiomdl_{log_id}.log"
    log_path_initial = None
    log_path_autofix = qc_dir / "output" / f"compile_studiomdl_{log_id}_autofix.log"
    autofix_applied = False
    autofix_details = []
    autofix_qc_path = None
    autofix_retry = False

    modelname_raw = entry["modelname_raw"]
    model_rel = entry["model_rel"]
    model_err = entry["model_err"]

    compile_start = time.monotonic()
    rc, last_lines, error_lines = run_studiomdl(
        qc,
        studiomdl,
        compile_dir,
        log_path,
        verbose=verbose,
        log_detail=log_detail,
    )
    if rc != 0 and _has_duplicate_name_error(error_lines):
        autofix_qc_path = qc.with_name(qc.stem + "_AUTOFIX.qc")
        changed, details = sanitize_qc_duplicates(qc, autofix_qc_path)
        if changed:
            autofix_applied = True
            autofix_details = details
            log_path_initial = log_path
            rc, last_lines, error_lines = run_studiomdl(
                autofix_qc_path,
                studiomdl,
                compile_dir,
                log_path_autofix,
                verbose=verbose,
                log_detail=log_detail,
            )
            autofix_retry = True
            log_path = log_path_autofix
        else:
            autofix_details = ["duplicate error detected, but no duplicates found to fix"]
    duration = time.monotonic() - compile_start

    expected_mdl = None
    compiled_dir = None
    compiled_files = []
    message = ""
    status = "fail"

    if model_rel:
        model_rel_path = Path(*model_rel.split("/"))
        expected_mdl = (compile_models_dir / model_rel_path).resolve()
        compiled_dir = expected_mdl.parent
        compiled_files = gather_compiled_files(compiled_dir, expected_mdl.stem)
    else:
        message = f"modelname_error={model_err or 'unknown'}"

    if expected_mdl and expected_mdl.exists():
        status = "ok"
    else:
        if not message:
            message = "missing_expected_mdl"

    phy_restore_requested = bool(restore_phy and model_rel)
    phy_restore_attempted = False
    phy_backup_path = None
    phy_backup_found = None
    phy_out_path = None
    phy_restored_out = False
    phy_restore_errors = []
    skin_restore_attempted = False
    skin_restore_applied = False
    skin_restore_reason = None
    skin_restore_error = None

    if status == "ok":
        out_models = compile_models_dir
        out_models.mkdir(parents=True, exist_ok=True)

        if phy_restore_requested:
            phy_restore_attempted = True
            phy_backup_path = (phy_backup_models_dir / model_rel_path).with_suffix(".phy")
            phy_out_path = (out_models / model_rel_path).with_suffix(".phy")

            if phy_backup_path.exists():
                phy_backup_found = True
                try:
                    phy_out_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(phy_backup_path, phy_out_path)
                    phy_restored_out = True
                except Exception as e:
                    phy_restore_errors.append(f"out: {phy_backup_path} -> {phy_out_path}: {e}")
            else:
                phy_backup_found = False

        if skin_backup_models_dir and model_rel:
            skin_restore_attempted = True
            orig_mdl = skin_backup_models_dir / model_rel_path
            out_mdl = out_models / model_rel_path
            res = restore_skin_table(orig_mdl, out_mdl)
            if res.get("status") == "applied":
                skin_restore_applied = True
                _print_locked(print_lock, f"[SKINFIX] Restored skinfamilies: {model_rel}")
            elif res.get("status") == "skipped":
                skin_restore_reason = res.get("reason")
            else:
                skin_restore_error = res.get("reason")

    if phy_restore_requested and phy_restore_attempted and not phy_backup_found:
        message = (message + "; " if message else "") + "phy_backup_missing"
        if require_phy_backup:
            status = "fail"
    if phy_restore_attempted and phy_restore_errors:
        message = (message + "; " if message else "") + "phy_restore_failed"
        if require_phy_backup:
            status = "fail"
    if rc != 0:
        message = (message + "; " if message else "") + f"studiomdl_rc={rc}"

    result = {
        "index": idx,
        "qc_path": str(qc),
        "qc_dir": str(qc_dir),
        "log_path": str(log_path),
        "log_path_initial": str(log_path_initial) if log_path_initial else None,
        "log_path_autofix": str(log_path_autofix) if autofix_retry else None,
        "status": status,
        "returncode": rc,
        "duration_sec": round(duration, 3),
        "modelname_raw": modelname_raw,
        "model_rel": model_rel,
        "expected_mdl": str(expected_mdl) if expected_mdl else None,
        "compiled_dir": str(compiled_dir) if compiled_dir else None,
        "compiled_files": [str(p) for p in compiled_files],
        "autofix_applied": autofix_applied,
        "autofix_retry": autofix_retry,
        "autofix_qc_path": str(autofix_qc_path) if autofix_qc_path else None,
        "autofix_details": autofix_details,
        "phy_restore_requested": phy_restore_requested,
        "phy_restore_attempted": phy_restore_attempted if phy_restore_requested else None,
        "phy_backup_path": str(phy_backup_path) if phy_backup_path else None,
        "phy_backup_found": phy_backup_found if phy_restore_attempted else None,
        "phy_out_path": str(phy_out_path) if phy_out_path else None,
        "phy_restored_out": phy_restored_out if phy_restore_attempted else None,
        "phy_restore_errors": phy_restore_errors,
        "skin_restore_attempted": skin_restore_attempted if skin_backup_models_dir else None,
        "skin_restore_applied": skin_restore_applied if skin_backup_models_dir else None,
        "skin_restore_reason": skin_restore_reason if skin_backup_models_dir else None,
        "skin_restore_error": skin_restore_error if skin_backup_models_dir else None,
        "last_lines": last_lines,
        "error_lines": error_lines,
        "message": message,
    }

    _print_locked(print_lock, f"Status: {status.upper()} | Time: {duration:.2f}s | Log: {log_path}")
    if message:
        _print_locked(print_lock, f"Note: {message}")

    return result


def write_summary_txt(out_dir: Path, summary: dict):
    lines = []
    lines.append("Compile summary")
    lines.append(f"Root: {summary['root']}")
    lines.append(f"StudioMDL: {summary['studiomdl']}")
    lines.append(f"Output dir: {summary['out_dir']}")
    if summary.get("compile_dir"):
        lines.append(f"Compile dir: {summary['compile_dir']}")
    if "compile_jobs" in summary:
        lines.append(f"Compile jobs: {summary.get('compile_jobs')}")
    if summary.get("collision_count"):
        lines.append(f"Modelname collisions: {summary.get('collision_count')}")
        for name in summary.get("collision_models", [])[:5]:
            lines.append(f"- collision: {name}")
    if summary.get("merge_conflict_count"):
        lines.append(f"Merge conflicts: {summary.get('merge_conflict_count')}")
        for name in summary.get("merge_conflicts", [])[:5]:
            lines.append(f"- merge: {name}")
    lines.append(f"Total QCs: {summary['total']}")
    lines.append(f"OK: {summary['ok']}  FAIL: {summary['fail']}")
    if "phy_restore" in summary:
        pr = summary["phy_restore"]
        lines.append(
            "PHY restore: "
            f"enabled={'ON' if pr.get('enabled') else 'OFF'} "
            f"restored_out={pr.get('restored_out', 0)} "
            f"backup_missing={pr.get('backup_missing', 0)} "
            f"errors={pr.get('errors', 0)}"
        )
    if "skin_restore" in summary:
        sr = summary["skin_restore"]
        lines.append(
            "SKIN restore: "
            f"enabled={'ON' if sr.get('enabled') else 'OFF'} "
            f"restored={sr.get('restored', 0)} "
            f"skipped={sr.get('skipped', 0)} "
            f"errors={sr.get('errors', 0)}"
        )
    lines.append(f"Duration sec: {summary['duration_sec']:.2f}")
    lines.append("")

    lines.append("Top errors:")
    if summary["top_errors"]:
        for item in summary["top_errors"]:
            lines.append(f"- ({item['count']}) {item['line']}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("Per QC:")
    for item in summary["results"]:
        status = item["status"].upper()
        qc_path = item["qc_path"]
        model_rel = item.get("model_rel") or "-"
        msg = item.get("message") or ""
        line = f"- {status}: {qc_path} -> {model_rel}"
        if msg:
            line += f" | {msg}"
        lines.append(line)

    out_txt = out_dir / "compile_summary.txt"
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(lines), encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(
        description="Batch compile *_OPT.qc using studiomdl.exe into <out>/models and restore original .phy from backup."
    )
    ap.add_argument("root", nargs="?", default=DEFAULT_ROOT, help="Root folder to scan for _OPT.qc")
    ap.add_argument("--studiomdl", default=DEFAULT_STUDIOMDL, help="Path to studiomdl.exe")
    ap.add_argument("--out", default=None, help="Output folder (default: <root>\\compiled)")
    ap.add_argument(
        "--no-restore-phy",
        dest="restore_phy",
        action="store_false",
        help="Disable restoring original .phy from backup after compile",
    )
    ap.add_argument(
        "--restore-phy-from",
        default=None,
        help="Folder that contains a 'models' tree with backed up .phy (default: <root>\\original\\models)",
    )
    ap.add_argument(
        "--restore-skin-from",
        default=None,
        help="Folder that contains original .mdl to restore skinfamilies table (default: disabled)",
    )
    ap.add_argument(
        "--require-phy-backup",
        action="store_true",
        help="Fail a QC if .phy backup is missing when restore is enabled",
    )
    ap.add_argument(
        "--studiomdl-verbose",
        action="store_true",
        help="Pass -verbose to studiomdl.exe (slower, larger output).",
    )
    ap.add_argument(
        "--log-detail",
        choices=["minimal", "full"],
        default="minimal",
        help="Compile log detail per QC (default: minimal).",
    )
    ap.add_argument(
        "--compile-jobs",
        type=int,
        default=1,
        help="Parallel compile jobs for studiomdl (default: 1, 0 = auto).",
    )
    ap.set_defaults(restore_phy=True)
    args = ap.parse_args()

    results = []
    compile_jobs = 1
    compile_tmp_dir = None
    collision_keys = set()
    merge_conflicts = []

    try:
        root = Path(args.root).expanduser().resolve()
        studiomdl = Path(args.studiomdl).expanduser().resolve()
        out_dir = Path(args.out).expanduser().resolve() if args.out else (root / "compiled")
        compile_dir = out_dir
        compile_models_dir = compile_dir / "models"
        phy_backup_models_dir = (
            Path(args.restore_phy_from).expanduser().resolve()
            if args.restore_phy_from
            else (root / "original" / "models")
        )
        skin_backup_models_dir = (
            Path(args.restore_skin_from).expanduser().resolve() if args.restore_skin_from else None
        )

        if not root.exists():
            print(f"[ERROR] Root does not exist: {root}")
            return 2
        if not studiomdl.exists():
            print(f"[ERROR] studiomdl.exe not found: {studiomdl}")
            return 2

        created_gameinfo, gameinfo_path = ensure_gameinfo(compile_dir)
        compile_models_dir.mkdir(parents=True, exist_ok=True)

        qcs = find_opt_qcs(root)
        print(f"Root: {root}")
        print(f"Found {len(qcs)} _OPT.qc files (excluding any 'output' folders).")
        print(f"Output: {out_dir}")
        if created_gameinfo:
            print(f"[INFO] Created minimal gameinfo.txt for studiomdl: {gameinfo_path}")
        if args.restore_phy:
            print(f"PHY restore: ON | Backup root: {phy_backup_models_dir}")
        else:
            print("PHY restore: OFF")
        if skin_backup_models_dir:
            if not skin_backup_models_dir.exists():
                print(f"[WARN] SKIN restore disabled; path not found: {skin_backup_models_dir}")
                skin_backup_models_dir = None
            else:
                print(f"SKIN restore: ON | Backup root: {skin_backup_models_dir}")
        else:
            print("SKIN restore: OFF")

        start_all = time.monotonic()

        qc_entries = []
        model_groups = {}
        for idx, qc in enumerate(qcs, start=1):
            modelname_raw = parse_modelname(qc)
            model_rel, model_err = normalize_modelname(modelname_raw)
            model_key = model_rel.lower() if model_rel else None
            entry = {
                "qc": qc,
                "qc_dir": qc.parent,
                "index": idx,
                "modelname_raw": modelname_raw,
                "model_rel": model_rel,
                "model_err": model_err,
                "model_rel_key": model_key,
            }
            qc_entries.append(entry)
            if model_key:
                model_groups.setdefault(model_key, []).append(entry)

        collision_keys = {k for k, v in model_groups.items() if len(v) > 1}
        serial_entries = [e for e in qc_entries if e.get("model_rel_key") in collision_keys]
        parallel_entries = [e for e in qc_entries if e.get("model_rel_key") not in collision_keys]

        if collision_keys:
            print(f"[WARN] Modelname collisions detected: {len(collision_keys)}. These QCs will compile serially.")
            for key in sorted(list(collision_keys))[:5]:
                print(f"[WARN] Collision: {key}")

        compile_jobs = _resolve_compile_jobs(int(args.compile_jobs))
        if len(parallel_entries) <= 1:
            compile_jobs = 1
        elif compile_jobs > len(parallel_entries):
            compile_jobs = len(parallel_entries)

        if compile_jobs <= 1:
            for entry in qc_entries:
                try:
                    results.append(
                        _compile_one(
                            entry,
                            total=len(qc_entries),
                            studiomdl=studiomdl,
                            compile_dir=compile_dir,
                            compile_models_dir=compile_models_dir,
                            out_dir=out_dir,
                            log_detail=str(args.log_detail),
                            verbose=bool(args.studiomdl_verbose),
                            restore_phy=bool(args.restore_phy),
                            phy_backup_models_dir=phy_backup_models_dir,
                            require_phy_backup=bool(args.require_phy_backup),
                            skin_backup_models_dir=skin_backup_models_dir,
                            print_lock=None,
                        )
                    )
                except Exception as exc:
                    _print_locked(None, f"[ERROR] QC exception: {entry.get('qc')} | {exc}")
                    results.append(_exception_result(entry, exc))
        else:
            compile_tmp_dir, job_dirs = _prepare_job_dirs(out_dir, compile_jobs)
            print(
                f"[INFO] Parallel compile enabled: jobs={compile_jobs} "
                f"parallel={len(parallel_entries)} serial={len(serial_entries)}"
            )
            print_lock = threading.Lock()

            with ThreadPoolExecutor(max_workers=compile_jobs) as executor:
                future_to_entry = {}
                for i, entry in enumerate(parallel_entries):
                    job_dir = job_dirs[i % compile_jobs]
                    future = executor.submit(
                        _compile_one,
                        entry,
                        total=len(qc_entries),
                        studiomdl=studiomdl,
                        compile_dir=job_dir,
                        compile_models_dir=job_dir / "models",
                        out_dir=out_dir,
                        log_detail=str(args.log_detail),
                        verbose=bool(args.studiomdl_verbose),
                        restore_phy=bool(args.restore_phy),
                        phy_backup_models_dir=phy_backup_models_dir,
                        require_phy_backup=bool(args.require_phy_backup),
                        skin_backup_models_dir=skin_backup_models_dir,
                        print_lock=print_lock,
                    )
                    future_to_entry[future] = entry

                for future in as_completed(list(future_to_entry.keys())):
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        entry = future_to_entry.get(future, {"index": None})
                        _print_locked(print_lock, f"[ERROR] QC exception: {entry.get('qc')} | {exc}")
                        results.append(_exception_result(entry, exc))

            for entry in serial_entries:
                try:
                    results.append(
                        _compile_one(
                            entry,
                            total=len(qc_entries),
                            studiomdl=studiomdl,
                            compile_dir=compile_dir,
                            compile_models_dir=compile_models_dir,
                            out_dir=out_dir,
                            log_detail=str(args.log_detail),
                            verbose=bool(args.studiomdl_verbose),
                            restore_phy=bool(args.restore_phy),
                            phy_backup_models_dir=phy_backup_models_dir,
                            require_phy_backup=bool(args.require_phy_backup),
                            skin_backup_models_dir=skin_backup_models_dir,
                            print_lock=None,
                        )
                    )
                except Exception as exc:
                    _print_locked(None, f"[ERROR] QC exception: {entry.get('qc')} | {exc}")
                    results.append(_exception_result(entry, exc))

            try:
                merge_conflicts = _merge_job_outputs(job_dirs, compile_models_dir)
            except Exception as exc:
                print(f"[ERROR] Merge failed: {exc}")
                merge_conflicts = merge_conflicts or []
                merge_conflicts.append(f"exception: {type(exc).__name__}: {exc}")

            # Only delete tmp outputs if merge succeeded.
            if compile_tmp_dir and not merge_conflicts:
                shutil.rmtree(compile_tmp_dir, ignore_errors=True)

        results.sort(key=lambda r: int(r.get("index") or 0))
        duration_all = time.monotonic() - start_all
        ok_count = sum(1 for r in results if r["status"] == "ok")
        fail_count = len(results) - ok_count
        phy_enabled = bool(args.restore_phy)
        phy_restored_out_count = sum(1 for r in results if r.get("phy_restored_out"))
        phy_backup_missing_count = sum(
            1 for r in results if r.get("phy_restore_requested") and not r.get("phy_backup_found")
        )
        phy_error_count = sum(1 for r in results if r.get("phy_restore_errors"))

        error_counts = {}
        for r in results:
            for line in r.get("error_lines") or []:
                error_counts[line] = error_counts.get(line, 0) + 1

        skin_restored_count = sum(1 for r in results if r.get("skin_restore_applied"))
        skin_skipped_count = sum(
            1
            for r in results
            if r.get("skin_restore_attempted")
            and not r.get("skin_restore_applied")
            and r.get("skin_restore_reason")
        )
        skin_error_count = sum(1 for r in results if r.get("skin_restore_error"))

        top_errors = sorted(
            [{"line": k, "count": v} for k, v in error_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        summary = {
            "root": str(root),
            "studiomdl": str(studiomdl),
            "out_dir": str(out_dir),
            "compile_dir": str(compile_dir),
            "compile_jobs": compile_jobs,
            "compile_tmp_dir": str(compile_tmp_dir) if compile_tmp_dir else None,
            "collision_count": len(collision_keys),
            "collision_models": sorted(list(collision_keys))[:20],
            "merge_conflict_count": len(merge_conflicts),
            "merge_conflicts": merge_conflicts[:50],
            "total": len(results),
            "ok": ok_count,
            "fail": fail_count,
            "duration_sec": round(duration_all, 3),
            "phy_restore": {
                "enabled": phy_enabled,
                "backup_root": str(phy_backup_models_dir) if phy_enabled else None,
                "restored_out": phy_restored_out_count if phy_enabled else 0,
                "backup_missing": phy_backup_missing_count if phy_enabled else 0,
                "errors": phy_error_count if phy_enabled else 0,
            },
            "skin_restore": {
                "enabled": bool(skin_backup_models_dir),
                "backup_root": str(skin_backup_models_dir) if skin_backup_models_dir else None,
                "restored": skin_restored_count if skin_backup_models_dir else 0,
                "skipped": skin_skipped_count if skin_backup_models_dir else 0,
                "errors": skin_error_count if skin_backup_models_dir else 0,
            },
            "top_errors": top_errors,
            "results": results,
        }

        out_dir.mkdir(parents=True, exist_ok=True)
        summary_json = out_dir / "compile_summary.json"
        summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8", errors="replace")
        write_summary_txt(out_dir, summary)

        print("\nDone.")
        print(f"Summary: {summary_json}")
        return 0
    except Exception:
        tb = traceback.format_exc()
        try:
            out_dir = None
            try:
                root = Path(args.root).expanduser().resolve()
                out_dir = Path(args.out).expanduser().resolve() if args.out else (root / "compiled")
            except Exception:
                out_dir = Path.cwd() / "compiled"
            out_dir.mkdir(parents=True, exist_ok=True)
            summary_json = out_dir / "compile_summary.json"
            crash_summary = {
                "crash": True,
                "exception": tb,
                "compile_jobs": compile_jobs,
                "collision_count": len(collision_keys) if collision_keys else 0,
                "merge_conflicts": merge_conflicts,
                "results": results,
            }
            summary_json.write_text(json.dumps(crash_summary, indent=2), encoding="utf-8", errors="replace")
        except Exception:
            pass
        print("[ERROR] batch_compile_opt_qc crashed. See compile_summary.json for details.")
        print(tb)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
