"""Screen-space quality evaluation of texture variants against the original addon.

For every evaluated model and standard view we render the original and each
variant with identical geometry and compare the images with NVIDIA FLIP
(perceptual, 0 = identical) and PSNR. Only pixels covered by the model count.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import flip_evaluator
import numpy as np

from .materials import MaterialResolver
from .render import TexturePack, build_scene, render, standard_views
from .source_model import load_model

FRAMEWORK = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS/lvs_framework")
WIDTH, HEIGHT, FOCAL = 1920, 1080, 938.0     # 1080p, GMod default FOV 75 (4:3) on 16:9
BACKGROUND = (0.18, 0.2, 0.22)                # must match render()'s default background


def _flip(ref: np.ndarray, test: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    ys, xs = np.where(mask)
    if not len(ys):
        return 0.0, 0.0
    y0, y1 = max(ys.min() - 16, 0), min(ys.max() + 17, mask.shape[0])
    x0, x1 = max(xs.min() - 16, 0), min(xs.max() + 17, mask.shape[1])
    ref, test, mask = ref[y0:y1, x0:x1], test[y0:y1, x0:x1], mask[y0:y1, x0:x1]
    err, _, _ = flip_evaluator.evaluate(np.ascontiguousarray(ref), np.ascontiguousarray(test), "LDR", inputsRGB=True, applyMagma=False,
                                        computeMeanError=False, parameters={"ppd": 67.0})
    err = np.asarray(err)
    if err.ndim == 3:
        err = err[..., 0]
    vals = err[mask]
    if not len(vals):
        return 0.0, 0.0, 0.0
    # worst local region: mean FLIP over 64x64 windows that are mostly model pixels
    win = 64
    e = np.where(mask, err, 0.0)
    c = np.pad(e, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    m = np.pad(mask.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    local = 0.0
    if e.shape[0] > win and e.shape[1] > win:
        se = c[win:, win:] - c[:-win, win:] - c[win:, :-win] + c[:-win, :-win]
        sm = m[win:, win:] - m[:-win, win:] - m[win:, :-win] + m[:-win, :-win]
        ok = sm >= 0.75 * win * win
        if ok.any():
            local = float((se[ok] / sm[ok]).max())
    return float(vals.mean()), float(np.percentile(vals, 99)), local


def _psnr(ref: np.ndarray, test: np.ndarray, mask: np.ndarray) -> float:
    d = (ref[mask] - test[mask]) ** 2
    mse = float(d.mean()) if d.size else 0.0
    return 99.0 if mse <= 1e-12 else 10.0 * math.log10(1.0 / mse)


def evaluate(original_root: Path, variants: dict[str, Path], models: list[str], out_json: Path,
             distances=(24.0, 96.0, 256.0), directions=8, save_png_dir: Path | None = None) -> dict:
    results = {"variants": list(variants), "models": {}}
    roots = {"original": Path(original_root), **{k: Path(v) for k, v in variants.items()}}
    resolvers = {k: MaterialResolver([v, FRAMEWORK]) for k, v in roots.items()}
    for rel in models:
        t0 = time.time()
        model = load_model(roots["original"] / rel)
        scenes = {}
        for name, root in roots.items():
            res = resolvers[name]
            pack = TexturePack()
            scene = build_scene(model, res, pack, lambda ref, r=res: r.find(ref, "vtf"))
            scenes[name] = (scene, pack.arrays())
        views = standard_views(scenes["original"][0], distances=distances, directions=directions)
        per_view = {k: [] for k in variants}
        for vname, (eye, right, up, fwd) in views:
            ref_img = render(*scenes["original"], eye, right, up, fwd, WIDTH, HEIGHT, FOCAL)
            mask = np.abs(ref_img - np.array(BACKGROUND, np.float32)).sum(axis=2) > 1e-6
            if mask.sum() < 500:
                continue
            for k in variants:
                img = render(*scenes[k], eye, right, up, fwd, WIDTH, HEIGHT, FOCAL)
                fmean, fp99, flocal = _flip(ref_img, img, mask)
                per_view[k].append({"view": vname, "flip_mean": fmean, "flip_p99": fp99, "flip_local": flocal,
                                    "psnr": _psnr(ref_img, img, mask), "pixels": int(mask.sum())})
                if save_png_dir is not None:
                    from PIL import Image
                    d = Path(save_png_dir) / Path(rel).stem
                    d.mkdir(parents=True, exist_ok=True)
                    Image.fromarray((img * 255).astype(np.uint8)).save(d / f"{vname}_{k}.png")
            if save_png_dir is not None:
                from PIL import Image
                d = Path(save_png_dir) / Path(rel).stem
                Image.fromarray((ref_img * 255).astype(np.uint8)).save(d / f"{vname}_original.png")
        summary = {}
        for k, rows in per_view.items():
            if rows:
                w = np.array([r["pixels"] for r in rows], float)
                summary[k] = {
                    "flip_mean": float(np.average([r["flip_mean"] for r in rows], weights=w)),
                    "flip_p99_worst": float(max(r["flip_p99"] for r in rows)),
                    "flip_mean_worst_view": float(max(r["flip_mean"] for r in rows)),
                    "flip_local_worst": float(max(r["flip_local"] for r in rows)),
                    "flip_local_mean": float(np.mean([r["flip_local"] for r in rows])),
                    "psnr_mean": float(np.mean([r["psnr"] for r in rows])),
                    "views": len(rows),
                }
        results["models"][rel] = {"summary": summary, "views": per_view, "seconds": time.time() - t0}
        print(rel, {k: (round(v["flip_mean"], 4), round(v["flip_local_mean"], 3)) for k, v in summary.items()},
              f"{time.time() - t0:.0f}s", flush=True)
    agg = {}
    for k in variants:
        vals = [m["summary"][k]["flip_mean"] for m in results["models"].values() if k in m["summary"]]
        worst = [m["summary"][k]["flip_mean_worst_view"] for m in results["models"].values() if k in m["summary"]]
        loc = [m["summary"][k]["flip_local_mean"] for m in results["models"].values() if k in m["summary"]]
        locw = [m["summary"][k]["flip_local_worst"] for m in results["models"].values() if k in m["summary"]]
        agg[k] = {"flip_mean_avg_models": float(np.mean(vals)) if vals else None,
                  "flip_worst_view": float(max(worst)) if worst else None,
                  "flip_local_avg": float(np.mean(loc)) if loc else None,
                  "flip_local_worst": float(max(locw)) if locw else None}
    results["aggregate"] = agg
    Path(out_json).write_text(json.dumps(results, indent=1))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--variant", action="append", required=True, help="name=path")
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", default=None)
    ap.add_argument("--directions", type=int, default=8)
    args = ap.parse_args(argv)
    variants = dict(v.split("=", 1) for v in args.variant)
    r = evaluate(Path(args.original), variants, args.model, Path(args.out),
                 directions=args.directions, save_png_dir=Path(args.png) if args.png else None)
    print(json.dumps(r["aggregate"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
