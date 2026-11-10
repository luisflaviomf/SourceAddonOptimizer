"""Worker command `pnglossless`: lossless PNG recompression of an addon folder (see the package docstring).

    pnglossless <addon> [--work DIR] [--report FILE] [--threads N] [--files-from LIST] [--keep-format]

Exit codes: 0 done (files that could not be improved are left as they were), 2 bad arguments or oxipng
missing from the tool package.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import find_oxipng, optimize_tree, summary_line, write_report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pnglossless", description="Lossless PNG recompression of an addon folder.")
    ap.add_argument("addon", help="addon folder (changed in place, only through atomic replaces)")
    ap.add_argument("--work", default=None, help="folder for temporary copies (never inside the addon)")
    ap.add_argument("--report", default=None, help="JSON report path")
    ap.add_argument("--threads", type=int, default=0, help="oxipng threads (default: half the logical CPUs)")
    ap.add_argument("--keep-format", action="store_true",
                    help="keep the colour type and bit depth of every file (only recompress)")
    ap.add_argument("--files-from", default=None,
                    help="UTF-8 text file with one PNG path per line (relative to the addon or absolute); "
                         "default: every PNG of the addon")
    ap.add_argument("--oxipng", default=None, help="oxipng.exe (default: the bundled one)")
    args = ap.parse_args(argv)

    root = Path(args.addon).expanduser()
    if not root.is_dir():
        print(f"[ERROR] Addon folder not found: {root}", flush=True)
        return 2
    oxipng = Path(args.oxipng) if args.oxipng else find_oxipng()
    if oxipng is None or not oxipng.is_file():
        print("[ERROR] oxipng.exe not found in the tool package; PNG files were not changed.", flush=True)
        return 2
    print(f"== PNG: lossless recompression ({oxipng.name}) ==", flush=True)
    files = None
    if args.files_from:
        try:
            lines = Path(args.files_from).read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            print(f"[ERROR] Cannot read the file list: {exc}", flush=True)
            return 2
        files = [line.strip() for line in lines if line.strip()]
    report = optimize_tree(root, oxipng=oxipng, work=args.work, threads=args.threads or None,
                           keep_format=args.keep_format, files=files)
    for reason, count in sorted(report["skipped"].items()):
        print(f"[PNGLOSSLESS] left as it was: {count} ({reason})", flush=True)
    if args.report:
        write_report(report, args.report)
    print(summary_line(report), flush=True)
    return 0
