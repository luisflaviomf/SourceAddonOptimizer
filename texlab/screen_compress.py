"""Screen-aware VTF compression engine used by the worker command `texscreen`.

Per texture it decides how many mip levels to drop (2x, 4x or 8x) from the
texel density the addon's own models give it on screen, rebuilds the new top
level as a Lanczos reduction of level 0 (method "lanczos", the default; the
reconstruction-aware solver stays available as method "solver" but aliased fine
meshes in game), encodes it with VTFCmd and keeps every lower mip level as the
original encoded blocks. DXT5 becomes DXT1 only
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
from .density import ViewModel, _model_usage, collect_usage, merge_usage, usage_tasks
from .plan import alpha_coverage_refs, alpha_unused_refs, opaque_alpha
from .vtf import (TEXTUREFLAGS_ENVMAP, bc3_blocks_to_bc1, decode_level, read_vtf, truncate_mips, write_vtf)

ENGINE = "texscreen/v7"
PROFILES = {
    # Absolute local-loss thresholds (contrast-normalised detail loss) calibrated on the
    # 15-vehicle LVS sample (2026-11-06): q50 = the loss 2x already causes on the median texture.
    "q50": 0.00257,
    "q75": 0.00840,
    # "Forte": for a total-size target (~100 MB LVS pack). Much looser detail threshold and up to 8x by
    # detail energy alone, but textures used as $detail (LVS body AO "skin" atlases: large smooth
    # surfaces where 8x shows DXT blocks) stop at 4x. Validated in game 2026-11-08 (docs/...stream).
    "strong": 0.12,
}
# magnify = (distance, quantile, max_log2, from_drop): from that drop on, at a close look (distance in
# units) the reduced texture may be stretched at most 2**max_log2 on the pixel-weighted quantile of
# its triangles. "strong" keeps 8x for textures that never show big on screen; liveries with text
# (LVS Monaco "HIGHWAY PATROL", 2026-11-08) stop at 4x, where the text stays legible.
PROFILE_OPTIONS = {
    "strong": {"max_energy_drop": 3, "caps": {"$detail": 2}, "magnify": (96.0, 0.5, 1.5, 3)},
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


def _param_caps(root: Path, caps: dict) -> dict:
    """{texture ref: max levels} for textures used through the given material parameters."""
    if not caps:
        return {}
    from .materials import MaterialResolver, normalize_texture_ref
    res = MaterialResolver([root])
    out = {}
    mat = next((q for q in root.iterdir() if q.is_dir() and q.name.lower() == "materials"), None)
    if mat is None:
        return out
    for vmt in mat.rglob("*.vmt"):
        m = res.material(vmt.relative_to(mat).with_suffix("").as_posix().lower())
        if m is None:
            continue
        for k, v in m.params.items():
            if k in caps and isinstance(v, str):
                ref = normalize_texture_ref(v)
                out[ref] = min(out.get(ref, 99), int(caps[k]))
    return out


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
    # validate the exact bytes before they touch the disk (reading the temp file back only
    # re-parsed these same bytes, and on Windows every read-back waits for the AV scan)
    check = read_vtf(data)
    if check.highres_offset + check.highres_bytes != len(data):
        raise ValueError(f"rebuilt VTF failed validation: {path}")
    tmp = path.with_name(path.name + ".texscreen.tmp")
    tmp.write_bytes(data)
    if tmp.stat().st_size != len(data):
        tmp.unlink(missing_ok=True)
        raise ValueError(f"rebuilt VTF was not fully written: {path}")
    os.replace(tmp, path)


@dataclass
class _Use:
    """What plan_one needs from a TextureUse (picklable, arrays already concatenated)."""
    s_eff: np.ndarray          # anisotropic effective density
    area: np.ndarray
    s_iso: np.ndarray          # isotropic (anisotropy 1) density
    uv: np.ndarray | None      # per-triangle UV centroids, None when the use has none


def _use_payload(use, view: ViewModel) -> _Use | None:
    if use is None:
        return None
    s_eff, area = use.arrays(view.anisotropy)
    return _Use(s_eff, area, use.arrays(1.0)[0], np.concatenate(use.uv) if use.uv else None)


def _plan_one(task) -> Item:
    """Decision for one texture (pure function of its file and the addon-wide usage)."""
    p, root, ref, threshold, view, use, unused_alpha, coverage, need_map = task[:9]
    energy_drop, cap, magnify = task[9] if len(task) > 9 else (MAX_DROP_BY_ENERGY, None, None)
    item = Item(p.relative_to(root).as_posix(), ref, p, before=p.stat().st_size)
    try:
        v = read_vtf(p)
    except Exception as exc:  # unreadable: leave it to the regular path
        item.action, item.reason = "fallback", f"unreadable:{type(exc).__name__}"
        return item
    ok, why = _eligible(v)
    if not ok:
        item.action = "fallback" if why in ("non_dxt_format", "nomip") else "keep"
        item.reason = why
        return item
    item.out_fmt = v.fmt
    item.coverage_alpha = coverage
    if v.fmt == 15 and v.frames == 1 and (unused_alpha or opaque_alpha(v)):
        item.out_fmt = 13
    limit = 0
    while limit < MAX_DROP and limit + 1 < v.mip_count and min(v.width, v.height) >> (limit + 1) >= MIN_SIDE:
        limit += 1
    if cap is not None:
        limit = min(limit, cap)
    drop = 1 if limit >= 1 else 0
    s_eff = area = None
    if use is not None and v.frames == 1:
        s_eff, area = use.s_eff, use.area
        if not len(s_eff):
            s_eff = area = None
    if s_eff is not None and limit > 1:
        uvc = use.uv if use.uv is not None else np.zeros((0, 2))
        maps = band_energy_maps(p, max_levels=limit, vtf=v)
        local = texture_local_error(s_eff, area, uvc, maps, view.focal_px, min(limit, len(maps)))
        lam = np.log2(np.maximum(s_eff, 1e-12) * CLOSEST_DISTANCE / view.focal_px)
        order = np.argsort(lam)
        cum = np.cumsum(area[order]) / max(float(area.sum()), 1e-12)
        lam_q01 = float(lam[order][min(int(np.searchsorted(cum, 0.01)), len(order) - 1)])
        mag_lam = mag_cum = None
        if magnify:
            # magnification guard: at a close look (distance d) the reduced texture may be stretched
            # at most 2**max_log2 over the pixel-weighted quantile q (livery text, logos, gauges)
            mag_lam = np.log2(np.maximum(s_eff, 1e-12) * magnify[0] / view.focal_px)
            mo = np.argsort(-mag_lam)
            mag_lam = mag_lam[mo]
            mag_cum = np.cumsum(area[mo]) / max(float(area.sum()), 1e-12)
        while drop + 1 <= min(limit, len(local) - 1):
            k = drop + 1
            invisible = lam_q01 >= k
            if magnify and not invisible and k >= (magnify[3] if len(magnify) > 3 else 1):
                # stretch k - lam at the q-quantile of pixels (sorted by decreasing lam)
                lam_q = float(mag_lam[min(int(np.searchsorted(mag_cum, magnify[1])), len(mag_lam) - 1)])
                if k - lam_q > magnify[2]:
                    break
            if invisible or (k <= energy_drop and local[k] <= threshold):
                drop = k
            else:
                break
        item.reason = "screen_density"
    else:
        item.reason = "not_seen_by_models" if use is None else ("single_step" if s_eff is not None
                                                                 else "no_visible_triangles")
    if s_eff is not None and drop:
        pair = (s_eff, use.s_iso)
        item.magnified = magnified_share(pair, area, drop, view)
        uvc_all = use.uv
        # the per-texel map only steers the "solver" method; Lanczos never reads it
        if need_map and uvc_all is not None and len(uvc_all) == len(s_eff):
            item.magnified_map = magnified_map(pair, area, uvc_all, drop, view,
                                               max(1, v.height >> drop), max(1, v.width >> drop),
                                               item.magnified)
    item.drop = drop
    item.action = "reduce" if drop else ("transcode" if item.out_fmt != v.fmt else "keep")
    return item


def plan_addon(root: Path, profile: str = "q50", view: ViewModel | None = None, jobs: int = 1,
               need_map: bool = True) -> list[Item]:
    """Per-texture plan. jobs > 1 spreads the models scan and the textures over processes;
    the result is identical to the sequential plan (same order, same decisions)."""
    root = Path(root)
    view = view or ViewModel(screen_height=1080)
    threshold = PROFILES[profile]
    mat = next((p for p in root.iterdir() if p.is_dir() and p.name.lower() == "materials"), None)
    if mat is None:
        return []
    pool = None
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        pool = ProcessPoolExecutor(max_workers=jobs)
    try:
        # the models scan runs in the pool while this process reads every material
        futures = [pool.submit(_model_usage, t) for t in usage_tasks(root)] if pool else None
        unused_alpha = alpha_unused_refs(root, [])
        opts = PROFILE_OPTIONS.get(profile)
        caps = _param_caps(root, opts.get("caps") or {}) if opts else {}
        coverage = alpha_coverage_refs(root, [])
        uses = merge_usage([f.result() for f in futures]) if pool else collect_usage(root)
        tasks = []
        for p in sorted(mat.rglob("*")):
            if not p.is_file() or p.suffix.lower() != ".vtf":
                continue
            ref = p.relative_to(mat).as_posix()[:-4].lower()
            task = (p, root, ref, threshold, view, _use_payload(uses.get(ref), view), ref in unused_alpha,
                    ref in coverage, need_map)
            if opts:
                task += ((opts.get("max_energy_drop", MAX_DROP_BY_ENERGY), caps.get(ref), opts.get("magnify")),)
            tasks.append(task)
        if pool is not None and len(tasks) > 1:
            return list(pool.map(_plan_one, tasks, chunksize=1))
    finally:
        if pool is not None:
            pool.shutdown()
    return [_plan_one(t) for t in tasks]


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


def _vtfcmd_encode(vtfcmd: Path, pngs: list[tuple[Path, str]], workdir: Path, jobs: int = 1) -> dict[Path, bytes]:
    """Encode every PNG with VTFCmd, chunks of up to 32 files, `jobs` VTFCmd processes at a time
    (each chunk is an independent process with its own output folder: same bytes as one by one)."""
    chunks = []
    for fmt in ("DXT1", "DXT5"):
        group = [p for p, f in pngs if f == fmt]
        # smaller chunks keep every VTFCmd busy until the end (each image is encoded on its own)
        size = 32 if jobs <= 1 else max(4, min(32, -(-len(group) // (jobs * 2))))
        for start in range(0, len(group), size):
            chunks.append((fmt, start, group[start:start + size]))

    def encode(job):
        fmt, start, chunk = job
        outdir = workdir / f"enc_{fmt}_{start}"
        outdir.mkdir()
        cmd = [str(vtfcmd)]
        for p in chunk:
            cmd += ["-file", str(p)]
        cmd += ["-output", str(outdir), "-format", fmt, "-alphaformat", fmt, "-nomipmaps",
                "-nothumbnail", "-noreflectivity", "-silent"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        return {p: read_vtf(outdir / (p.stem + ".vtf")).level_data(0) for p in chunk}

    out = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for part in pool.map(encode, chunks):
            out.update(part)
    return out


def lanczos_down(H: np.ndarray, factor: int) -> np.ndarray:
    """Per-channel Lanczos reduction of the level-0 image (low-pass: no aliasing of fine patterns)."""
    h, w = H.shape[0] // factor, H.shape[1] // factor
    chans = [np.asarray(Image.fromarray(np.ascontiguousarray(H[..., c]), "F").resize((w, h), Image.LANCZOS))
             for c in range(H.shape[2])]
    return np.clip(np.stack(chans, axis=2), 0.0, 1.0)


def run(root: Path, vtfcmd: Path, profile: str = "q50", jobs: int = 4, report_path: Path | None = None,
        method: str = "lanczos", encode_jobs: int | None = None) -> dict:
    """`jobs`: analysis processes and image threads. `encode_jobs`: VTFCmd processes at a time
    (default: every logical CPU but two; each VTFCmd is single-threaded and light on memory)."""
    root = Path(root)
    if encode_jobs is None:
        encode_jobs = max(1, jobs, (os.cpu_count() or 2) - 2)
    _emit("PROGRESS", "analyze", 0, 1, "")
    items = plan_addon(root, profile, jobs=jobs, need_map=(method == "solver"))
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
            # lossless either way; level 1 only trades PNG size for speed (VTFCmd reads the same pixels)
            Image.fromarray(np.round(L * 255.0).astype(np.uint8), "RGBA").save(png, compress_level=1)
            with lock:
                counter[0] += 1
                _emit("PROGRESS", "rebuild", counter[0], len(work), item.rel)
            return item, v, png

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            prepared = list(pool.map(prepare, work))
        encoded = _vtfcmd_encode(vtfcmd, [(png, fmt_name[item.out_fmt]) for item, _, png in prepared
                                          if isinstance(png, Path)], tmp, encode_jobs)
        written = [0]

        def write(entry):
            item, v, png = entry
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
            with lock:
                written[0] += 1
                _emit("PROGRESS", "write", written[0], len(prepared), item.rel)

        # each entry writes its own file; threads overlap the disk/antivirus latency
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            list(pool.map(write, prepared))
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
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
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
