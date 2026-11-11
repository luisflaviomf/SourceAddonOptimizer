"""Compute a per-texture reduction plan (levels to drop + output format) for an addon sample."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .allocate import (TextureCost, band_energy_maps, solve_for_error, texture_error_curve,
                       texture_local_error, totals)
from .density import ViewModel, collect_usage
from .materials import MaterialResolver, alpha_required
from .vtf import TEXTUREFLAGS_ENVMAP, decode_level, image_bytes, read_vtf

MIN_SIDE = 8


def alpha_unused_refs(root: Path, extra_roots: list[Path]) -> set[str]:
    """Textures whose alpha no referencing material reads (every VMT in the addon is scanned)."""
    res = MaterialResolver([root] + list(extra_roots))
    verdict: dict[str, bool] = {}
    for p in (root / "materials").rglob("*.vmt"):
        m = res.material(p.relative_to(root / "materials").as_posix()[:-4])
        if not m:
            continue
        for param, ref in m.textures.items():
            verdict[ref] = verdict.get(ref, False) or alpha_required(m, param)
        for _key, ref in m.other:       # named through a parameter the analysis does not model: keep it
            verdict[ref] = True
    return {ref for ref, needed in verdict.items() if not needed}


def opaque_alpha(v) -> bool:
    return all(decode_level(v, l)[..., 3].min() == 255 for l in range(min(v.mip_count, 3)))


def bytes_after(v, drop: int, fmt: int) -> int:
    header = len(v.raw) - v.highres_bytes
    total = header
    for l in range(drop, v.mip_count):
        w, h = v.mip_size(l)
        total += image_bytes(fmt, w, h) * v.frames * v.faces
    return total


def make_plan(root: Path, extra_roots: list[Path], view: ViewModel, target: str = "all2x",
              max_drop: int = 3, distances=None, weights=None) -> dict:
    root = Path(root)
    uses = collect_usage(root, extra_roots)
    unused_alpha = alpha_unused_refs(root, extra_roots)
    costs, info = [], {}
    for p in sorted((root / "materials").rglob("*.vtf")):
        ref = p.relative_to(root / "materials").as_posix()[:-4].lower()
        v = read_vtf(p)
        fmt = v.fmt
        if v.fmt == 15 and v.frames == 1 and v.faces == 1 and (ref in unused_alpha or opaque_alpha(v)):
            fmt = 13
        reducible = (not v.flags & TEXTUREFLAGS_ENVMAP and v.depth == 1 and v.mip_count > 1
                     and v.fmt in (13, 15) and min(v.width, v.height) > MIN_SIDE)
        limit = 0
        if reducible:
            while limit < max_drop and limit + 1 < v.mip_count and min(v.width, v.height) >> (limit + 1) >= MIN_SIDE:
                limit += 1
        use = uses.get(ref)
        info[ref] = {"w": v.width, "h": v.height, "fmt": v.fmt, "out_fmt": fmt, "limit": limit,
                     "analyzed": use is not None, "bytes": len(v.raw)}
        sizes = [bytes_after(v, k, fmt) for k in range(limit + 1)]
        if use is None or limit == 0:
            # Not seen through any model UV (HUD, Lua, warps) or not reducible: never reduce it.
            costs.append(TextureCost(ref, sizes[:1], [0.0]))
            continue
        s_eff, area = use.arrays(view.anisotropy)
        uvc = np.concatenate(use.uv)
        maps = band_energy_maps(p, max_levels=limit)
        kwargs = {}
        if distances:
            kwargs = {"distances": distances, "weights": weights}
        errors = texture_error_curve(s_eff, area, uvc, maps, view.focal_px, min(limit, len(maps)), **kwargs)
        info[ref]["local_error"] = texture_local_error(s_eff, area, uvc, maps, view.focal_px,
                                                       min(limit, len(maps)), **kwargs)
        costs.append(TextureCost(ref, sizes[:len(errors)], errors))
    # reference: what "every reducible, analysed texture 2x" costs in visible error
    ref_plan = {c.ref: (1 if len(c.bytes_by_drop) > 1 else 0) for c in costs}
    ref_bytes, ref_error = totals(costs, ref_plan)
    mu, plan = solve_for_error(costs, ref_error)
    plan_bytes, plan_error = totals(costs, plan)
    # Per-texture floor: every reducible texture drops at least 1 level (the product's 2x) and keeps
    # dropping while its local visible loss stays below the loss 2x already causes on a typical
    # (median) texture. No texture ends up worse than that typical baseline degradation.
    k1 = [i["local_error"][1] for i in info.values() if len(i.get("local_error", [])) > 1]
    threshold = float(np.median(k1)) if k1 else 0.0
    floor_plan = {}
    for c in costs:
        le = info[c.ref].get("local_error", [0.0])
        k = 1 if len(c.bytes_by_drop) > 1 else 0
        while k + 1 < len(c.bytes_by_drop) and k + 1 < len(le) and le[k + 1] <= threshold:
            k += 1
        floor_plan[c.ref] = k
    floor_bytes, floor_error = totals(costs, floor_plan)
    return {"plan": floor_plan, "global_plan": plan, "threshold": threshold, "info": info, "mu": mu,
            "floor": {"bytes": floor_bytes, "error": floor_error},
            "reference": {"bytes": ref_bytes, "error": ref_error},
            "result": {"bytes": plan_bytes, "error": plan_error},
            "curves": {c.ref: {"bytes": c.bytes_by_drop, "error": c.error_by_drop} for c in costs}}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--extra", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-drop", type=int, default=3)
    args = ap.parse_args(argv)
    r = make_plan(Path(args.root), [Path(e) for e in args.extra], ViewModel(screen_height=1080), max_drop=args.max_drop)
    Path(args.out).write_text(json.dumps(r, indent=1))
    from collections import Counter
    print("drops:", Counter(r["plan"].values()))
    print("reference (all 2x):", r["reference"])
    print("global RD plan:", r["result"])
    print("floor plan:", r["floor"], "threshold", r["threshold"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


_COVERAGE_FLAGS = ("$alphatest", "$translucent", "$allowalphatocoverage", "$additive", "$distancealpha")


def alpha_coverage_refs(root: Path, extra_roots: list[Path]) -> set[str]:
    """Base textures whose alpha decides coverage/opacity (cutouts, glass): never reshape that alpha."""
    res = MaterialResolver([root] + list(extra_roots))
    out: set[str] = set()
    for p in (root / "materials").rglob("*.vmt"):
        m = res.material(p.relative_to(root / "materials").as_posix()[:-4])
        if m and any(m.flag(f) for f in _COVERAGE_FLAGS):
            for param in ("$basetexture", "$basetexture2"):
                if param in m.textures:
                    out.add(m.textures[param])
    return out
