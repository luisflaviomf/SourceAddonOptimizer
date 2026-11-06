"""Candidate builders: write a modified copy of an addon tree."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from .vtf import TEXTUREFLAGS_ENVMAP, read_vtf, truncate_mips


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        raise SystemExit(f"refusing to overwrite {dst}")
    shutil.copytree(src, dst)


def apply_truncation(root: Path, plan: Callable[[str, object], int], report_path: Path | None = None) -> dict:
    """For every VTF under root/materials, drop plan(ref, vtf) top mip levels (exact block copy)."""
    root = Path(root)
    mat = root / "materials"
    rows = []
    before = after = 0
    for p in sorted(mat.rglob("*.vtf")):
        ref = p.relative_to(mat).as_posix()[:-4].lower()
        v = read_vtf(p)
        size = p.stat().st_size
        drop = 0
        if not v.flags & TEXTUREFLAGS_ENVMAP and v.depth == 1:
            drop = max(0, min(int(plan(ref, v)), v.mip_count - 1))
        if drop:
            data = truncate_mips(v, drop)
            p.write_bytes(data)
        new = p.stat().st_size
        before += size
        after += new
        rows.append({"ref": ref, "w": v.width, "h": v.height, "fmt": v.fmt_name, "mips": v.mip_count,
                     "drop": drop, "before": size, "after": new})
    report = {"vtf_before": before, "vtf_after": after, "rows": rows}
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=1))
    return report
