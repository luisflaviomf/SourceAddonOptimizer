"""Lossless PNG recompression of an addon folder (worker command `pnglossless`).

Every `.png` is recompressed with oxipng (tools/oxipng, bundled next to the worker) and replaced only when
the result is smaller AND decodes to exactly the same RGBA pixels as the file it replaces (checked here
with Pillow, independently of oxipng). Colour-related chunks (gAMA, cHRM, sRGB, iCCP, cICP, pHYs) stay;
editor text, EXIF and timestamps go.

oxipng may store the same pixels in a smaller colour type (opaque RGBA as RGB, up to 256 colours as a
palette, gray as gray). Only the storage formats in GAME_FORMATS are accepted: the ones the GMod client
was seen reading with identical pixels (ITexture:GetColor against the RGBA original, see
docs/arquivos-intocados-2026-11-10.md). A result in any other format is redone keeping the source format.

Left untouched: 16-bit PNGs, animated PNGs, files that are not PNG inside, anything that fails a step.
The addon files are never written in place: results are staged in the work folder and moved over the
original with os.replace, so a hard-linked source can never be modified through the copy.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

PROGRESS = "PNGLOSSLESS_PROGRESS: {i}|{n}|{path}"
SIGNATURE = b"\x89PNG\r\n\x1a\n"
BATCH = 48

# Level 4 is the measured sweet spot (level 6 and Zopfli: 7x-19x the time for under 1% more).
BASE_ARGS = ["-q", "-o", "4", "--keep", "display,gAMA,cHRM"]
KEEP_FORMAT_ARGS = ["--nx"]

# (colour type, bit depth, has tRNS) the game reads with the same pixels as the RGBA original: 48 pairs
# checked in the client on 2026-11-10, every one identical. Colour types: 0 gray, 2 RGB, 3 palette,
# 4 gray+alpha, 6 RGBA. 16 bits per channel was not checked and is never written.
GAME_FORMATS = frozenset(
    [(6, 8, False), (4, 8, False)]
    + [(2, 8, trns) for trns in (False, True)]
    + [(ctype, depth, trns) for ctype in (0, 3) for depth in (1, 2, 4, 8) for trns in (False, True)]
)


class PngInfo(tuple):
    """(colour type, bit depth, has tRNS, interlaced, animated)."""
    __slots__ = ()

    @property
    def storage(self):
        return self[0], self[1], self[2]


def describe(path) -> PngInfo | None:
    """Header facts of a PNG file, or None when the file is not a PNG."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data[:8] != SIGNATURE or len(data) < 33 or data[12:16] != b"IHDR":
        return None
    _w, _h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", data[16:29])
    trns = animated = False
    pos = 8
    while pos + 8 <= len(data):
        length, = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        if kind == b"tRNS":
            trns = True
        elif kind == b"acTL":
            animated = True
        elif kind in (b"IDAT", b"IEND"):
            break
        pos += 12 + length
    return PngInfo((ctype, depth, trns, bool(interlace), animated))


def same_pixels(a, b) -> bool:
    """True when both files decode to the same size and the same RGBA values (8 bits per channel)."""
    with Image.open(a) as ia, Image.open(b) as ib:
        if ia.size != ib.size:
            return False
        pa = np.asarray(ia.convert("RGBA"))
        pb = np.asarray(ib.convert("RGBA"))
    return bool(np.array_equal(pa, pb))


def find_oxipng() -> Path | None:
    env = os.environ.get("OXIPNG_EXE")
    if env and Path(env).is_file():
        return Path(env)
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    repo = Path(__file__).resolve().parents[1]
    roots += [repo / "tools" / "oxipng", repo]
    for r in roots:
        if (r / "oxipng.exe").is_file():
            return r / "oxipng.exe"
    return None


def _run(oxipng: Path, args: list[str], files: list[Path], threads: int) -> None:
    """oxipng on staged copies, in place. Failures of single files show up later as 'not smaller'."""
    if not files:
        return
    flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    try:
        subprocess.run([str(oxipng), *args, "-t", str(threads), *[str(f) for f in files]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800,
                       creationflags=flags, check=False)
    except subprocess.TimeoutExpired:
        pass


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _check(item):
    """(new size, storage info, same pixels) of one staged result; same pixels is None when the result is
    not worth keeping. Returns the exception when the file cannot be read."""
    staged, original, size, info = item
    try:
        new_size = staged.stat().st_size
        out = describe(staged)
        if out is None or new_size >= size:
            return new_size, out, None
        if out.storage != info.storage and out.storage not in GAME_FORMATS:
            return new_size, out, None
        return new_size, out, same_pixels(original, staged)
    except Exception as exc:  # a file Pillow cannot decode stays as it was
        return exc


def _move(staged: Path, dest: Path) -> None:
    """Put the staged file at dest without ever writing into dest's current data."""
    try:
        os.replace(staged, dest)
        return
    except OSError:
        pass                                             # another volume
    tmp = dest.with_name(dest.name + ".pnglossless.tmp")
    try:
        shutil.copyfile(staged, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def optimize_tree(root, oxipng=None, work=None, threads: int | None = None, keep_format: bool = False,
                  files=None, log=print) -> dict:
    """Recompress the PNGs under root (all of them, or only `files`: paths relative to root or absolute).
    Returns the report (also the JSON the CLI writes)."""
    root = Path(root)
    t0 = time.time()
    oxipng = Path(oxipng) if oxipng else find_oxipng()
    if oxipng is None or not oxipng.is_file():
        raise FileNotFoundError("oxipng.exe")
    threads = threads or max(1, (os.cpu_count() or 2) // 2)
    if files is None:
        files = root.rglob("*")
    else:
        files = [Path(f) if Path(f).is_absolute() else root / f for f in files]
    files = sorted({p for p in files if p.suffix.lower() == ".png" and p.is_file()},
                   key=lambda p: p.as_posix().lower())
    report = {"files": len(files), "changed": 0, "before": 0, "after": 0, "format_changed": 0,
              "kept_format": 0, "skipped": {}, "failed": [], "seconds": 0.0}

    def skip(reason):
        report["skipped"][reason] = report["skipped"].get(reason, 0) + 1

    todo = []
    for p in files:
        size = p.stat().st_size
        report["before"] += size
        report["after"] += size
        info = describe(p)
        if info is None:
            skip("not a PNG inside")
        elif info[1] > 8:
            skip("16-bit")
        elif info[4]:
            skip("animated")
        else:
            todo.append((p, size, info))

    if work:
        Path(work).mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="pnglossless_", dir=str(work) if work else None))
    args = BASE_ARGS + (KEEP_FORMAT_ARGS if keep_format else [])
    done = 0
    pool = ThreadPoolExecutor(max_workers=threads)
    try:
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            staged = []
            for k, (p, _size, _info) in enumerate(batch):
                s = stage / f"{start + k:07d}.png"
                shutil.copyfile(p, s)
                staged.append(s)
            _run(oxipng, args, staged, threads)
            # results in a storage format the game was not seen reading: redo keeping the source format
            redo = []
            for s, (p, _size, info) in zip(staged, batch):
                out = describe(s)
                if out is None or (out.storage != info.storage and out.storage not in GAME_FORMATS):
                    shutil.copyfile(p, s)
                    redo.append(s)
            if redo:
                report["kept_format"] += len(redo)
                _run(oxipng, BASE_ARGS + KEEP_FORMAT_ARGS, redo, threads)
            # decode both files of every pair (Pillow releases the GIL), then replace one by one
            checks = list(pool.map(_check, [(s, p, size, info) for s, (p, size, info) in zip(staged, batch)]))
            for s, (p, size, info), res in zip(staged, batch, checks):
                rel = _rel(p, root)
                try:
                    if isinstance(res, Exception):
                        raise res
                    new_size, out, same = res
                    if same is None:
                        skip("not smaller" if out is None or new_size >= size else "format not accepted")
                    elif not same:
                        report["failed"].append(rel)
                        log(f"[WARN] PNG kept as it was (pixels would change): {rel}", flush=True)
                    else:
                        _move(s, p)
                        report["changed"] += 1
                        report["after"] += new_size - size
                        if out.storage != info.storage:
                            report["format_changed"] += 1
                except Exception as exc:  # one bad file never stops the addon
                    report["failed"].append(rel)
                    log(f"[WARN] PNG kept as it was ({type(exc).__name__}: {exc}): {rel}", flush=True)
                finally:
                    if s.exists():
                        s.unlink()
                done += 1
            log(PROGRESS.format(i=done, n=len(todo), path=_rel(batch[-1][0], root)), flush=True)
    finally:
        pool.shutdown()
        shutil.rmtree(stage, ignore_errors=True)
    report["seconds"] = round(time.time() - t0, 1)
    return report


def summary_line(report: dict) -> str:
    return (f"[PNGLOSSLESS] files={report['files']} changed={report['changed']} "
            f"bytes={report['before']}->{report['after']} failed={len(report['failed'])} "
            f"seconds={report['seconds']}")


def write_report(report: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
