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

Profiles: q50 (Equilibrado), q75, strong (Forte), exact (Sem perda: no reduction,
only the pixel-identical DXT5 -> DXT1 transcode, nothing reported as fallback) and
max (Maximo: at least 4x per texture, 8x by detail energy).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import npsolve
from .allocate import DEFAULT_DISTANCES, DEFAULT_WEIGHTS, band_energy_maps, box_pyramid, texture_local_error
from .density import ViewModel, _model_usage, collect_usage, merge_usage, sibling_usage_tasks, usage_tasks
from .plan import alpha_coverage_refs, alpha_unused_refs, opaque_alpha
from .vtf import (image_bytes, TEXTUREFLAGS_ENVMAP, bc3_blocks_to_bc1, decode_level, read_vtf, truncate_mips, write_vtf)

ENGINE = "texscreen/v8"
PROFILES = {
    # Absolute local-loss thresholds (contrast-normalised detail loss) calibrated on the
    # 15-vehicle LVS sample (2026-11-06): q50 = the loss 2x already causes on the median texture.
    "q50": 0.00257,
    "q75": 0.00840,
    # "Forte": for a total-size target (~100 MB LVS pack). Much looser detail threshold and up to 8x by
    # detail energy alone, but textures used as $detail (LVS body AO "skin" atlases: large smooth
    # surfaces where 8x shows DXT blocks) stop at 4x. Validated in game 2026-11-08 (docs/...stream).
    "strong": 0.12,
    # "Sem perda": no texture is reduced (only the exact DXT5 -> DXT1 transcode may change a file).
    "exact": 0.0,
    # "Maximo": smallest size, visible loss up close accepted. Every texture loses at least 4x;
    # 8x by detail energy with this threshold (or when the levels are never sampled). LVS estimate
    # 27.5-30.2 MiB for thresholds inf..0.12; 0.25 (28.1 MiB) keeps the ~40 most detailed at 4x.
    "max": 0.25,
    # "Stream" (main method): Forte, then every single-frame DXT/8-bit texture becomes a GMOT blob decoded
    # by Lua in game (texture_stream package, 34 dB + noise filling). Test bench textures 837 -> ~100 MiB.
    "stream": 0.12,
    # "Stream máximo": the same at 31 dB (smallest size, a little grain lost up close)
    "stream_max": 0.12,
}
# texture stream profiles -> PSNR target of texture_stream.convert
STREAM_TARGETS = {"stream": 34.0, "stream_max": 31.0}
# Extra rules of a profile (q50/q75 have none):
# - max_energy_drop: levels a texture may lose on detail energy alone;
# - caps {param: levels}: textures used through that material parameter lose at most that many levels;
# - magnify = (distance, quantile, max_log2, from_drop): from that drop on, at a close look (distance
#   in units) the reduced texture may be stretched at most 2**max_log2 on the pixel-weighted quantile
#   of its triangles (textures that never show big on screen may still go 8x);
# - text_cap: textures whose file name says they carry lettering or graphics (TEXT_NAMES: badges,
#   gauges, liveries, decals, plates...) lose at most this many levels; normal maps are not included;
# - small_cap = (bytes, levels): a texture whose reduced chain at that many levels already fits in
#   `bytes` stops there: going further saves a few KB but erases buttons, icons and labels;
# - max_drop: every texture loses at most this many levels (0: no reduction at all);
# - min_drop: every texture loses at least this many levels where its size and mips allow (default 1);
# - fallback: False reports the files the engine does not handle (non-DXT, NOMIP, unreadable) as
#   kept, not as `fallback` (the caller sends fallback files through its regular, lossy path).
# "strong" (Forte): LVS Monaco "HIGHWAY PATROL" livery and Audi S3 console labels unreadable at 8x/4x
# (Luis, 2026-11-08); with these rules they stay at 2x. LVS textures 798 -> 75 MiB (q50: 136).
# "exact" (Sem perda): nothing reduced, nothing sent to the regular path.
# "max" (Maximo): at least 4x everywhere, $detail and lettering stop at 4x, no small/magnify guard.
TEXT_NAMES = re.compile(r"badge|gauge|tacho|speedo|sign|decal|sticker|logo|emblem|plate|cluster|dial|"
                        r"license|livery|polic|polizei|text|txt|label|font|number|digital|clock|odo|warn|"
                        r"cockpit|dash|interior|display|radio|screen",
                        re.IGNORECASE)
NORMAL_SUFFIX = re.compile(r"(_nm|_normal|_n|_bump|_ddn)$", re.IGNORECASE)
PROFILE_OPTIONS = {
    "strong": {"max_energy_drop": 3, "caps": {"$detail": 2}, "magnify": (96.0, 0.5, 1.5, 3), "text_cap": 1,
               "small_cap": (100 * 1024, 1)},
    "exact": {"max_drop": 0, "fallback": False},
    "max": {"max_energy_drop": 3, "caps": {"$detail": 2}, "text_cap": 2, "min_drop": 2},
}
PROFILE_OPTIONS["stream"] = PROFILE_OPTIONS["stream_max"] = PROFILE_OPTIONS["strong"]
# Textures the classic chain did not handle, rebuilt when the addon's models use them (2026-11-08 test
# bench: 743 MiB of weapon textures stored without mips and 264 MiB uncompressed, all left untouched):
# - "single": DXT stored without mips or flagged NOMIP: the top level is rebuilt and written alone (the
#   engine never samples anything else);
# - "convert": uncompressed RGB(A) (mostly normal maps): becomes DXT1/DXT5 (DXT1 when no material reads
#   the alpha or it is opaque) at the screen-aware size; lower mips are re-encoded from the original ones.
# Unused ones (HUD, reticles, sprites drawn by Lua) stay as they are (or go to the caller's regular path).
UNCOMPRESSED = (0, 1, 2, 3, 12, 16)
# First-person textures (texlab.firstperson): the view-model look is the closest look; no forced 2x, and
# the magnification guard of the profile applies from 4x on at that look.
FP_MAGNIFY = (1.5, 2)
FP_ENERGY_DROP = 1            # first-person levels by detail energy (2: almost no change on the bench)
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
    rebuild: str = ""   # "single" / "convert" (see UNCOMPRESSED); "" = DXT with stored mips
    first_person: bool = False


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
    fp: bool = False           # used by a first-person model (densities already scaled to that look)


def _use_payload(use, view: ViewModel) -> _Use | None:
    if use is None:
        return None
    s_eff, area = use.arrays(view.anisotropy)
    return _Use(s_eff, area, use.arrays(1.0)[0], np.concatenate(use.uv) if use.uv else None,
                bool(getattr(use, "first_person", False)))


def _plan_one(task) -> Item:
    """Decision for one texture (pure function of its file and the addon-wide usage)."""
    p, root, ref, threshold, view, use, unused_alpha, coverage, need_map = task[:9]
    opts = task[9] if len(task) > 9 else (MAX_DROP_BY_ENERGY, None, None, None)
    energy_drop, cap, magnify, small_cap = opts[:4]
    min_drop = opts[4] if len(opts) > 4 else 1
    fallback = "fallback" if len(opts) <= 5 or opts[5] else "keep"
    item = Item(p.relative_to(root).as_posix(), ref, p, before=p.stat().st_size)
    try:
        v = read_vtf(p)
    except Exception as exc:  # unreadable: leave it to the regular path
        item.action, item.reason = fallback, f"unreadable:{type(exc).__name__}"
        return item
    ok, why = _eligible(v)
    kind = ""
    # rebuilt when models use the texture, or when it is a model texture (materials/models/) no model of the
    # addon draws: those get the default reduction of unused textures (HUD, reticles, sprites and skins live
    # outside models/ and stay as they are)
    model_tex = ref.startswith("models/")
    if (not ok and why in ("non_dxt_format", "nomip") and cap != 0
            and ((use is not None and len(use.s_eff)) or (use is None and model_tex))
            and v.frames == 1 and v.faces == 1 and v.depth == 1 and not v.flags & TEXTUREFLAGS_ENVMAP
            and min(v.width, v.height) > MIN_SIDE):
        kind = "single" if v.fmt in (13, 15) else "convert" if v.fmt in UNCOMPRESSED else ""
    if not ok and not kind:
        item.action = fallback if why in ("non_dxt_format", "nomip") else "keep"
        item.reason = why
        return item
    fp = use is not None and use.fp
    item.rebuild, item.first_person = kind, fp
    if fp:
        # the view-model look is the closest look, stared at all the time and zoomed when aiming:
        # no forced reduction, the Equilibrado detail threshold, at most 2x on detail energy (more only
        # where the dropped levels are never sampled), guard from 4x at that look. With the Forte
        # threshold an M9K AUG scope label went 4x and became unreadable (bench, 2026-11-08)
        min_drop = min_drop if min_drop > 1 else 0
        threshold = min(threshold, PROFILES["q50"])
        energy_drop = min(energy_drop, FP_ENERGY_DROP)
        if magnify:
            magnify = (CLOSEST_DISTANCE, magnify[1], FP_MAGNIFY[0], FP_MAGNIFY[1])
    single_level = kind == "single" or (kind == "convert" and (v.mip_count < 2 or v.flags & NOMIP))
    nomip_cap = opts[6] if len(opts) > 6 else None
    if kind and v.flags & NOMIP and nomip_cap is not None:
        # an explicit NOMIP flag marks textures meant to stay sharp (gauges, labels): the lettering cap
        cap = min(cap if cap is not None else 99, int(nomip_cap))
    top = None
    item.coverage_alpha = coverage
    if kind == "convert":
        top = decode_level(v, 0)
        item.out_fmt = 13 if unused_alpha or bool((top[..., 3] == 255).all()) else 15
    else:
        item.out_fmt = v.fmt
        if v.fmt == 15 and v.frames == 1 and (unused_alpha or opaque_alpha(v)):
            item.out_fmt = 13
    limit = 0
    while (limit < MAX_DROP and (kind or limit + 1 < v.mip_count)
           and min(v.width, v.height) >> (limit + 1) >= MIN_SIDE):
        limit += 1
    if cap is not None:
        limit = min(limit, cap)
    if small_cap and limit > small_cap[1]:
        # small textures: going past 2x saves only a few KB but erases labels and numbers
        fmt_c = item.out_fmt if kind else v.fmt
        last = small_cap[1] + 1 if single_level else v.mip_count
        at = sum(image_bytes(fmt_c, max(1, v.width >> lv), max(1, v.height >> lv))
                 for lv in range(small_cap[1], last))
        if at * v.frames <= small_cap[0]:
            limit = small_cap[1]
    drop = min(min_drop, limit)
    s_eff = area = None
    if use is not None and v.frames == 1:
        s_eff, area = use.s_eff, use.area
        if not len(s_eff):
            s_eff = area = None
    if s_eff is not None and limit > drop:
        uvc = use.uv if use.uv is not None else np.zeros((0, 2))
        if kind and (single_level or v.mip_count <= limit):
            # no stored chain to measure against: the 2x box pyramid of the top level
            maps = band_energy_maps(p, max_levels=limit,
                                    raw=box_pyramid(top if top is not None else decode_level(v, 0), limit))
        else:
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
    elif cap == 0:
        item.reason = "lossless"
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
        opts = PROFILE_OPTIONS.get(profile)
        # no reduction allowed: the models scan (screen density) has nothing to decide
        scan = not (opts and opts.get("max_drop") == 0)
        # the models scan runs in the pool while this process reads every material
        # the addon's own models, then the models of sibling addons that draw its materials (a weapon
        # base and its packs: 230 MiB of ARC9 textures were "unused" because their models live next door)
        futures = ([pool.submit(_model_usage, t) for t in usage_tasks(root, first_person_ref=CLOSEST_DISTANCE)
                    + sibling_usage_tasks(root, first_person_ref=CLOSEST_DISTANCE)]
                   if pool and scan else None)
        unused_alpha = alpha_unused_refs(root, [])
        caps = _param_caps(root, opts.get("caps") or {}) if opts else {}
        coverage = alpha_coverage_refs(root, [])
        if not scan:
            uses = {}
        else:
            uses = (merge_usage([f.result() for f in futures]) if pool
                    else collect_usage(root, first_person_ref=CLOSEST_DISTANCE, siblings=True))
        tasks = []
        for p in sorted(mat.rglob("*")):
            if not p.is_file() or p.suffix.lower() != ".vtf":
                continue
            ref = p.relative_to(mat).as_posix()[:-4].lower()
            task = (p, root, ref, threshold, view, _use_payload(uses.get(ref), view), ref in unused_alpha,
                    ref in coverage, need_map)
            if opts:
                cap = caps.get(ref)
                stem = ref.rsplit("/", 1)[-1]
                if opts.get("text_cap") is not None and TEXT_NAMES.search(stem) and not NORMAL_SUFFIX.search(stem):
                    cap = min(cap if cap is not None else 99, int(opts["text_cap"]))
                if opts.get("max_drop") is not None:
                    cap = min(cap if cap is not None else 99, int(opts["max_drop"]))
                task += ((opts.get("max_energy_drop", MAX_DROP_BY_ENERGY), cap, opts.get("magnify"), opts.get("small_cap"),
                          opts.get("min_drop", 1), opts.get("fallback", True), opts.get("text_cap")),)
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


def _vtfcmd_encode_chain(vtfcmd: Path, pngs: list[tuple[Path, str]], workdir: Path, jobs: int = 1) -> dict[Path, list[bytes]]:
    """Like _vtfcmd_encode, but VTFCmd also builds the mip chain (box filter, no sharpening) from the
    image: {png: [level 0, level 1, ...]}. For textures whose stored chain is not DXT ("convert")."""
    chunks = []
    for fmt in ("DXT1", "DXT5"):
        group = [p for p, f in pngs if f == fmt]
        size = 32 if jobs <= 1 else max(4, min(32, -(-len(group) // (jobs * 2))))
        for start in range(0, len(group), size):
            chunks.append((fmt, start, group[start:start + size]))

    def encode(job):
        fmt, start, chunk = job
        outdir = workdir / f"chain_{fmt}_{start}"
        outdir.mkdir()
        cmd = [str(vtfcmd)]
        for p in chunk:
            cmd += ["-file", str(p)]
        cmd += ["-output", str(outdir), "-format", fmt, "-alphaformat", fmt, "-mfilter", "BOX",
                "-msharpen", "NONE", "-nothumbnail", "-noreflectivity", "-silent"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        res = {}
        for p in chunk:
            vv = read_vtf(outdir / (p.stem + ".vtf"))
            res[p] = [vv.level_data(l) for l in range(vv.mip_count)]
        return res

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
            if item.rebuild == "convert" or (item.rebuild == "single" and item.action == "reduce"):
                # rebuilt from the decoded top level: written alone (no mips / NOMIP) or with a chain
                # VTFCmd builds from it (uncompressed textures that had mips)
                H = decode_level(v, 0).astype(np.float32) / 255.0
                L = lanczos_down(H, 1 << item.drop) if item.drop else H
                chain = item.rebuild == "convert" and v.mip_count > 1 and not v.flags & NOMIP
                if item.coverage_alpha and chain and item.drop:
                    L[..., 3] = decode_level(v, item.drop)[..., 3].astype(np.float32) / 255.0
                png = tmp / f"r{abs(hash(item.ref)) % 10**12:012d}_{item.drop}.png"
                Image.fromarray(np.round(L * 255.0).astype(np.uint8), "RGBA").save(png, compress_level=1)
                with lock:
                    counter[0] += 1
                    _emit("PROGRESS", "rebuild", counter[0], len(work), item.rel)
                return item, v, ("chain" if chain else "alone", png)
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
                                          if isinstance(png, Path)]
                                 + [(png[1], fmt_name[item.out_fmt]) for item, _, png in prepared
                                    if isinstance(png, tuple) and png[0] == "alone"], tmp, encode_jobs)
        chains = _vtfcmd_encode_chain(vtfcmd, [(png[1], fmt_name[item.out_fmt]) for item, _, png in prepared
                                               if isinstance(png, tuple) and png[0] == "chain"], tmp, encode_jobs)
        written = [0]

        def write(entry):
            item, v, png = entry
            if isinstance(png, tuple):
                levels = chains[png[1]] if png[0] == "chain" else [encoded[png[1]]]
                flags = v.flags & ~EIGHTBITALPHA if item.out_fmt == 13 else v.flags
                data = write_vtf(v, item.out_fmt, max(1, v.width >> item.drop), max(1, v.height >> item.drop),
                                 levels, flags)
            elif item.action == "transcode":
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
    stream = None
    if profile in STREAM_TARGETS:
        import texture_stream
        stream = texture_stream.convert(root, jobs=texture_stream.auto_jobs(jobs), target=STREAM_TARGETS[profile],
                                        emit=_emit)
        if stream["streamed"] and not stream.get("packed", True):
            from texlab.packing import IGNORED_WARNING
            print(IGNORED_WARNING.format(folder=texture_stream.BLOB_DIR), flush=True)
        streamed = {r["path"].lower() for r in stream["rows"]}
        for item in items:
            if item.rel.replace("\\", "/").lower() in streamed:
                item.action, item.reason = "stream", "stream"
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
    if stream is not None:
        # blobs and runtime are new files of the addon: counted in "after"
        report["after"] += stream["blobs"] + stream["runtime"]
        report["stream"] = stream
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
