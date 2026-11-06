"""Build a candidate where chosen textures are reduced 2x with the reconstruction-aware solver.

Only the new top level is encoded (VTFCmd, same encoder as the product baseline).
Every lower mip level is the original encoded data, copied exactly (or losslessly
transcoded DXT5 -> DXT1 when the target format is DXT1).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .downscale import solve, solve_factor
from .vtf import (TEXTUREFLAGS_ENVMAP, bc3_blocks_to_bc1, decode_level, read_vtf, truncate_mips,
                  write_vtf)

VTFCMD = Path(os.environ.get("LOCALAPPDATA", "")) / "GmodAddonOptimizer/tools/VTFEdit/1/VTFEdit/VTFCmd.exe"
FMT_CODE = {"DXT1": 13, "DXT5": 15}
CLAMP_S, CLAMP_T = 0x4, 0x8
EIGHTBITALPHA = 0x2000


def baseline_decisions(log_path: Path) -> dict[str, str]:
    """ref -> target format, parsed from the product baseline log."""
    out = {}
    pat = re.compile(r"Optimized (materials[\\/].+?\.vtf):.*?target=(DXT1|DXT5)")
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if m:
            ref = m.group(1).replace("\\", "/").lower()[len("materials/"):-4]
            out[ref] = m.group(2)
    return out


def _encode_top(pngs: list[tuple[Path, str]], workdir: Path) -> dict[Path, bytes]:
    """Encode PNGs with VTFCmd (no mips); return png -> top-level block bytes."""
    result = {}
    for fmt in ("DXT1", "DXT5"):
        group = [p for p, f in pngs if f == fmt]
        for start in range(0, len(group), 40):
            chunk = group[start:start + 40]
            outdir = workdir / f"enc_{fmt}_{start}"
            outdir.mkdir()
            cmd = [str(VTFCMD)]
            for p in chunk:
                cmd += ["-file", str(p)]
            cmd += ["-output", str(outdir), "-format", fmt, "-alphaformat", fmt, "-nomipmaps",
                    "-nothumbnail", "-noreflectivity", "-silent"]
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            for p in chunk:
                v = read_vtf(outdir / (p.stem + ".vtf"))
                result[p] = v.level_data(0)
    return result


def build(original_root: Path, out_root: Path, decisions: dict, alpha: float = 1.0,
          beta: float = 1.0) -> dict:
    """decisions: ref -> "DXT1"/"DXT5" (drop 1 level) or ref -> (drop, "DXT1"/"DXT5")."""
    original_root, out_root = Path(original_root), Path(out_root)
    if out_root.exists():
        raise SystemExit(f"refusing to overwrite {out_root}")
    shutil.copytree(original_root, out_root)
    mat = out_root / "materials"
    report = {"rows": [], "before": 0, "after": 0}
    with tempfile.TemporaryDirectory(prefix="texlab_opt_") as tmp:
        tmp = Path(tmp)
        jobs = []
        for i, p in enumerate(sorted(mat.rglob("*.vtf"))):
            ref = p.relative_to(mat).as_posix()[:-4].lower()
            v = read_vtf(p)
            report["before"] += len(v.raw)
            target = decisions.get(ref)
            drop = 1
            if isinstance(target, (tuple, list)):
                drop, target = int(target[0]), target[1]
            if target is not None and drop == 0:
                if target == "DXT1" and v.fmt == 15 and v.frames == 1 and v.faces == 1:
                    lv = [bc3_blocks_to_bc1(v.level_data(l)) for l in range(v.mip_count)]
                    data = write_vtf(v, 13, v.width, v.height, lv, v.flags & ~EIGHTBITALPHA)
                    p.write_bytes(data)
                    report["rows"].append({"ref": ref, "action": "transcode", "before": len(v.raw), "after": len(data)})
                    continue
                target = None
            if target is None or v.mip_count <= drop:
                report["rows"].append({"ref": ref, "action": "keep", "before": len(v.raw), "after": len(v.raw)})
                continue
            simple = v.frames == 1 and v.faces == 1 and v.depth == 1 and not v.flags & TEXTUREFLAGS_ENVMAP
            if not simple or v.fmt not in (13, 15) or (target == "DXT5" and v.fmt == 13):
                data = truncate_mips(v, drop)
                p.write_bytes(data)
                report["rows"].append({"ref": ref, "action": "truncate", "before": len(v.raw), "after": len(data)})
                continue
            H = decode_level(v, 0).astype(np.float32) / 255.0
            Mk = decode_level(v, drop).astype(np.float32) / 255.0
            L = solve_factor(H, Mk, 1 << drop, alpha * 4.0, beta, wrap_s=not v.flags & CLAMP_S,
                             wrap_t=not v.flags & CLAMP_T)
            png = tmp / f"t{i:05d}.png"
            Image.fromarray(np.round(L * 255.0).astype(np.uint8), "RGBA").save(png)
            jobs.append((p, v, ref, target, png, drop))
        encoded = _encode_top([(png, target) for _, _, _, target, png, _ in jobs], tmp)
        for p, v, ref, target, png, drop in jobs:
            fmt = FMT_CODE[target]
            lower = [v.level_data(l) for l in range(drop + 1, v.mip_count)]
            if fmt == 13 and v.fmt == 15:
                lower = [bc3_blocks_to_bc1(b) for b in lower]
            flags = v.flags & ~EIGHTBITALPHA if fmt == 13 else v.flags
            data = write_vtf(v, fmt, max(1, v.width >> drop), max(1, v.height >> drop), [encoded[png]] + lower, flags)
            check = read_vtf(data)
            assert check.mip_count == v.mip_count - drop and check.width == max(1, v.width >> drop)
            p.write_bytes(data)
            report["rows"].append({"ref": ref, "action": f"opt{1 << drop}x->{target}", "before": len(v.raw),
                                   "after": len(data)})
    report["after"] = sum(r["after"] for r in report["rows"])
    return report
