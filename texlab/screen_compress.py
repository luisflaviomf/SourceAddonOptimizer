"""Screen-aware VTF compression engine used by the worker command `texscreen`.

Per texture it decides how many mip levels to drop (2x, 4x or 8x) from the
texel density the addon's own models give it on screen, rebuilds the new top
level with reconstruction-aware downscaling, encodes it with VTFCmd and keeps
every lower mip level as the original encoded blocks. DXT5 becomes DXT1 only
when no material reads the alpha (or it is constant 255).

Files the engine does not handle (non-DXT formats, NOMIP, cubemaps...) are
reported as `fallback`, so the caller can route them through the regular path.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import npsolve
from .allocate import DEFAULT_DISTANCES, DEFAULT_WEIGHTS, band_energy_maps, texture_local_error
from .density import ViewModel, collect_usage
from .plan import alpha_coverage_refs, alpha_unused_refs, opaque_alpha
from .vtf import (TEXTUREFLAGS_ENVMAP, bc3_blocks_to_bc1, decode_level, read_vtf, truncate_mips, write_vtf)

ENGINE = "texscreen/v7"
PROFILES = {
    # Absolute local-loss thresholds (contrast-normalised detail loss) calibrated on the
    # 15-vehicle LVS sample (2026-11-06): q50 = the loss 2x already causes on the median texture.
    "q50": 0.00257,
    "q75": 0.00840,
}
MAX_DROP = 3
# Beyond this many levels a texture is only reduced when the dropped levels are provably never
# sampled, even from the closest distance (regular high-contrast patterns such as meshes,
# grilles or text show their loss more than their band energy suggests).
MAX_DROP_BY_ENERGY = 2
CLOSEST_DISTANCE = 24.0
MIN_SIDE = 8
EXACT_MIP_BELOW = 0.02   # below this magnified share the original encoded mip is reused as-is
CLAMP_S, CLAMP_T = 0x4, 0x8
EIGHTBITALPHA = 0x2000
NOMIP = 0x100


@dataclass
class Item:
    rel: str            # path relative to the addon root (as on disk)
    ref: str            # materials-relative, lower case, no extension
    path: Path
    drop: int = 0
    out_fmt: int = 0
    action: str = "keep"
    reason: str = ""
    magnified: float = -1.0   # share of screen pixels that see the new top level magnified (-1 unknown)
    coverage_alpha: bool = False   # alpha drives cutout/opacity: keep the original mip alpha exactly
    magnified_map: object = None   # per-texel share at the reduced resolution (atlases mix regimes)
    before: int = 0
    after: int = 0


def _emit(kind: str, *fields) -> None:
    print(f"TEXSCREEN_{kind}: " + "|".join(str(f) for f in fields), flush=True)


def _eligible(v) -> tuple[bool, str]:
    if v.fmt not in (13, 15):
        return False, "non_dxt_format"
    if v.flags & TEXTUREFLAGS_ENVMAP or v.faces != 1:
        return False, "cubemap"
    if v.depth != 1:
        return False, "volume"
    if v.mip_count < 2 or v.flags & NOMIP:
        return False, "nomip"
    if min(v.width, v.height) <= MIN_SIDE:
        return False, "too_small"
    return True, ""


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".texscreen.tmp")
    tmp.write_bytes(data)
    check = read_vtf(tmp)
    if check.highres_offset + check.highres_bytes != len(data):
        tmp.unlink(missing_ok=True)
        raise ValueError(f"rebuilt VTF failed validation: {path}")
    os.replace(tmp, path)


def plan_addon(root: Path, profile: str = "q50", view: ViewModel | None = None) -> list[Item]:
    root = Path(root)
    view = view or ViewModel(screen_height=1080)
    threshold = PROFILES[profile]
    mat = next((p for p in root.iterdir() if p.is_dir() and p.name.lower() == "materials"), None)
    if mat is None:
        return []
    uses = collect_usage(root)
    unused_alpha = alpha_unused_refs(root, [])
    coverage = alpha_coverage_refs(root, [])
    items = []
    for p in sorted(mat.rglob("*")):
        if not p.is_file() or p.suffix.lower() != ".vtf":
            continue
        ref = p.relative_to(mat).as_posix()[:-4].lower()
        item = Item(p.relative_to(root).as_posix(), ref, p, before=p.stat().st_size)
        items.append(item)
        try:
            v = read_vtf(p)
        except Exception as exc:  # unreadable: leave it to the regular path
            item.action, item.reason = "fallback", f"unreadable:{type(exc).__name__}"
            continue
        ok, why = _eligible(v)
        if not ok:
            item.action = "fallback" if why in ("non_dxt_format", "nomip") else "keep"
            item.reason = why
            continue
        item.out_fmt = v.fmt
        item.coverage_alpha = ref in coverage
        if v.fmt == 15 and v.frames == 1 and (ref in unused_alpha or opaque_alpha(v)):
            item.out_fmt = 13
        limit = 0
        while limit < MAX_DROP and limit + 1 < v.mip_count and min(v.width, v.height) >> (limit + 1) >= MIN_SIDE:
            limit += 1
        drop = 1 if limit >= 1 else 0
        use = uses.get(ref)
        s_eff = area = None
        if use is not None and v.frames == 1:
            s_eff, area = use.arrays(view.anisotropy)
            if not len(s_eff):
                s_eff = area = None
        if s_eff is not None and limit > 1:
            uvc = np.concatenate(use.uv) if use.uv else np.zeros((0, 2))
            maps = band_energy_maps(p, max_levels=limit)
            local = texture_local_error(s_eff, area, uvc, maps, view.focal_px, min(limit, len(maps)))
            lam = np.log2(np.maximum(s_eff, 1e-12) * CLOSEST_DISTANCE / view.focal_px)
            order = np.argsort(lam)
            cum = np.cumsum(area[order]) / max(float(area.sum()), 1e-12)
            lam_q01 = float(lam[order][min(int(np.searchsorted(cum, 0.01)), len(order) - 1)])
            while drop + 1 <= min(limit, len(local) - 1):
                k = drop + 1
                invisible = lam_q01 >= k
                if invisible or (k <= MAX_DROP_BY_ENERGY and local[k] <= threshold):
                    drop = k
                else:
                    break
            item.reason = "screen_density"
        else:
            item.reason = "not_seen_by_models" if use is None else ("single_step" if s_eff is not None
                                                                     else "no_visible_triangles")
        if s_eff is not None and drop:
            pair = (s_eff, use.arrays(1.0)[0])
            item.magnified = magnified_share(pair, area, drop, view)
            uvc_all = np.concatenate(use.uv) if use.uv else None
            if uvc_all is not None and len(uvc_all) == len(s_eff):
                item.magnified_map = magnified_map(pair, area, uvc_all, drop, view,
                                                   max(1, v.height >> drop), max(1, v.width >> drop),
                                                   item.magnified)
        item.drop = drop
        item.action = "reduce" if drop else ("transcode" if item.out_fmt != v.fmt else "keep")
    return items


def _magnified_weight(s_eff, d: float, drop: int, view: ViewModel) -> np.ndarray:
    """Probability that a triangle is magnified at distance d, half the players with 16x anisotropic
    filtering (finest mip from the least stretched axis) and half without (most stretched axis)."""
    s_af, s_iso = s_eff
    lam_af = np.log2(np.maximum(s_af, 1e-12) * d / view.focal_px)
    lam_iso = np.log2(np.maximum(s_iso, 1e-12) * d / view.focal_px)
    return 0.5 * (lam_af < drop) + 0.5 * (lam_iso < drop)


def magnified_share(s_eff, area, drop: int, view: ViewModel) -> float:
    """Share of expected screen pixels where the original samples a level finer than `drop`.

    There the reduced texture is magnified (only the top level can supply detail); everywhere
    else the GPU shows the new top level where the original showed mip `drop`.
    """
    mag = tot = 0.0
    for d, w in zip(DEFAULT_DISTANCES, DEFAULT_WEIGHTS):
        px = w * area * (view.focal_px / d) ** 2
        mag += float((px * _magnified_weight(s_eff, d, drop, view)).sum())
        tot += float(px.sum())
    return mag / tot if tot > 0 else 0.0


def magnified_map(s_eff, area, uv, drop: int, view: ViewModel, h: int, w: int, fallback: float):
    """Per-texel magnified share: triangle statistics splatted at their UV centroids, smoothed."""
    mag = np.zeros(h * w, np.float64)
    tot = np.zeros(h * w, np.float64)
    m_tri = np.zeros(len(area))
    px_tri = np.zeros(len(area))
    for d, wgt in zip(DEFAULT_DISTANCES, DEFAULT_WEIGHTS):
        px = wgt * area * (view.focal_px / d) ** 2
        m_tri += px * _magnified_weight(s_eff, d, drop, view)
        px_tri += px
    x = np.mod(np.floor(uv[:, 0] * w).astype(np.int64), w)
    y = np.mod(np.floor(uv[:, 1] * h).astype(np.int64), h)
    idx = y * w + x
    np.add.at(mag, idx, m_tri)
    np.add.at(tot, idx, px_tri)
    r = max(2, min(h, w) // 48)
    scale = float(tot.max()) or 1.0
    mag2, tot2 = (mag / scale).reshape(h, w), (tot / scale).reshape(h, w)

    def blur(a):
        # separable box filter by summing wrapped shifts: exact, no cumulative-sum cancellation
        out = np.zeros_like(a)
        for k in range(-r, r + 1):
            out += np.roll(a, k, axis=0)
        res = np.zeros_like(a)
        for k in range(-r, r + 1):
            res += np.roll(out, k, axis=1)
        return res

    bm, bt = blur(mag2), blur(tot2)
    share = np.where(bt > 1e-12, bm / np.maximum(bt, 1e-12), fallback)
    return np.clip(share, 0.0, 1.0).astype(np.float32)


def _vtfcmd_encode(vtfcmd: Path, pngs: list[tuple[Path, str]], workdir: Path) -> dict[Path, bytes]:
    out = {}
    for fmt in ("DXT1", "DXT5"):
        group = [p for p, f in pngs if f == fmt]
        for start in range(0, len(group), 32):
            chunk = group[start:start + 32]
            outdir = workdir / f"enc_{fmt}_{start}"
            outdir.mkdir()
            cmd = [str(vtfcmd)]
            for p in chunk:
                cmd += ["-file", str(p)]
            cmd += ["-output", str(outdir), "-format", fmt, "-alphaformat", fmt, "-nomipmaps",
                    "-nothumbnail", "-noreflectivity", "-silent"]
            subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
            for p in chunk:
                out[p] = read_vtf(outdir / (p.stem + ".vtf")).level_data(0)
    return out


def lanczos_down(H: np.ndarray, factor: int) -> np.ndarray:
    """Per-channel Lanczos reduction of the level-0 image (low-pass: no aliasing of fine patterns)."""
    h, w = H.shape[0] // factor, H.shape[1] // factor
    chans = [np.asarray(Image.fromarray(np.ascontiguousarray(H[..., c]), "F").resize((w, h), Image.LANCZOS))
             for c in range(H.shape[2])]
    return np.clip(np.stack(chans, axis=2), 0.0, 1.0)


def run(root: Path, vtfcmd: Path, profile: str = "q50", jobs: int = 4, report_path: Path | None = None,
        method: str = "lanczos") -> dict:
    root = Path(root)
    _emit("PROGRESS", "analyze", 0, 1, "")
    items = plan_addon(root, profile)
    work = [i for i in items if i.action in ("reduce", "transcode")]
    _emit("PROGRESS", "analyze", 1, 1, f"{len(work)} textures planned")
    lock = threading.Lock()
    counter = [0]
    fmt_name = {13: "DXT1", 15: "DXT5"}
    with tempfile.TemporaryDirectory(prefix="texscreen_") as tmp:
        tmp = Path(tmp)

        def prepare(item: Item):
            v = read_vtf(item.path)
            if item.action == "transcode":
                return item, v, None
            if v.frames > 1 or 0.0 <= item.magnified < EXACT_MIP_BELOW:
                # never (or almost never) magnified: the original mip is exactly what players see
                return item, v, "truncate"
            H = decode_level(v, 0).astype(np.float32) / 255.0
            Mk = decode_level(v, item.drop).astype(np.float32) / 255.0
            if item.magnified < 0:
                alpha, beta = 0.8, 0.2        # usage unknown (HUD, Lua): favour close-up sharpness
            elif item.magnified_map is not None:
                share = item.magnified_map    # each atlas region weighted by how it is really seen
                alpha, beta = share, np.maximum(1.0 - share, 0.05)
            else:
                alpha, beta = item.magnified, max(1.0 - item.magnified, 0.05)
            if method == "solver":
                L = npsolve.solve(H, Mk, 1 << item.drop, alpha * 4.0, beta, wrap_s=not v.flags & CLAMP_S,
                                  wrap_t=not v.flags & CLAMP_T, iters=10)
            else:
                L = lanczos_down(H, 1 << item.drop)
            if item.coverage_alpha:
                # alpha-tested/translucent: the threshold turns any reshaping of alpha into
                # moved edges, so the new top level carries the original mip's alpha as-is
                L[..., 3] = Mk[..., 3]
            png = tmp / f"t{abs(hash(item.ref)) % 10**12:012d}_{item.drop}.png"
            Image.fromarray(np.round(L * 255.0).astype(np.uint8), "RGBA").save(png)
            with lock:
                counter[0] += 1
                _emit("PROGRESS", "rebuild", counter[0], len(work), item.rel)
            return item, v, png

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            prepared = list(pool.map(prepare, work))
        encoded = _vtfcmd_encode(vtfcmd, [(png, fmt_name[item.out_fmt]) for item, _, png in prepared
                                          if isinstance(png, Path)], tmp)
        for n, (item, v, png) in enumerate(prepared, 1):
            if item.action == "transcode":
                levels = [bc3_blocks_to_bc1(v.level_data(l, f)) for l in range(v.mip_count) for f in [0]]
                data = write_vtf(v, 13, v.width, v.height, levels, v.flags & ~EIGHTBITALPHA)
            elif png == "truncate":
                levels = [v.level_data(l, f) for l in range(item.drop, v.mip_count) for f in [0]]                     if v.frames == 1 else None
                if levels is not None and item.out_fmt == 13 and v.fmt == 15:
                    data = write_vtf(v, 13, max(1, v.width >> item.drop), max(1, v.height >> item.drop),
                                     [bc3_blocks_to_bc1(b) for b in levels], v.flags & ~EIGHTBITALPHA)
                else:
                    data = truncate_mips(v, item.drop)
            else:
                lower = [v.level_data(l) for l in range(item.drop + 1, v.mip_count)]
                if item.out_fmt == 13 and v.fmt == 15:
                    lower = [bc3_blocks_to_bc1(b) for b in lower]
                flags = v.flags & ~EIGHTBITALPHA if item.out_fmt == 13 else v.flags
                data = write_vtf(v, item.out_fmt, max(1, v.width >> item.drop), max(1, v.height >> item.drop),
                                 [encoded[png]] + lower, flags)
            if len(data) < item.before:
                _write_atomic(item.path, data)
            else:
                item.action, item.reason = "keep", "no_gain"
            _emit("PROGRESS", "write", n, len(prepared), item.rel)
    for item in items:
        item.after = item.path.stat().st_size
    report = {
        "engine": ENGINE, "profile": profile, "method": method, "threshold": PROFILES[profile],
        "files": len(items), "before": sum(i.before for i in items), "after": sum(i.after for i in items),
        "fallback": [i.rel for i in items if i.action == "fallback"],
        "rows": [{"path": i.rel, "action": i.action, "reason": i.reason, "drop": i.drop,
                  "format": {13: "DXT1", 15: "DXT5"}.get(i.out_fmt, ""), "before": i.before, "after": i.after}
                 for i in items],
    }
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=1), encoding="utf-8")
        _emit("REPORT", report_path)
    return report


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="texscreen")
    ap.add_argument("addon")
    ap.add_argument("--vtfcmd", required=True)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="q50")
    ap.add_argument("--method", choices=["lanczos", "solver"], default="lanczos")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)
    root = Path(args.addon)
    if not root.is_dir():
        print(f"addon folder not found: {root}")
        return 2
    report = run(root, Path(args.vtfcmd), args.profile, args.jobs, Path(args.report), args.method)
    print(f"texscreen: {report['before']:,} -> {report['after']:,} bytes; fallback {len(report['fallback'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
