"""Quality of what the game shows, per streamed texture (review/claude lab).

For every dumped encoder input (lab/dumpimg/<g>/<key>.png + .json) and its blob in lab/dump/<g>/...:
- wav : the wavelet reconstruction (codec.decode, before DXT) against the input
- fin : the VTF the Lua runtime builds (codec.lua_vtf, the Lua DXT encoder) decoded back, against the input
- for colour textures PSNR over RGB, SSIM over luma (Gaussian 1.5), FLIP (LDR, 67 ppd) when asked;
  normal maps: PSNR of the unit vectors (as choose.py measures them)
Also usable as a library: metrics(ref_rgba, test_rgba, normal).
python texeval.py <group|all> [sample] [flip] -> out/texeval_<g>.json
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO))
LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def psnr(a, b):
    mse = float(np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def ssim(a, b):
    """SSIM of two luma planes 0..255 (Wang et al., Gaussian sigma 1.5)"""
    a, b = a.astype(np.float64), b.astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu1, mu2 = gaussian_filter(a, 1.5), gaussian_filter(b, 1.5)
    s11 = gaussian_filter(a * a, 1.5) - mu1 * mu1
    s22 = gaussian_filter(b * b, 1.5) - mu2 * mu2
    s12 = gaussian_filter(a * b, 1.5) - mu1 * mu2
    m = ((2 * mu1 * mu2 + C1) * (2 * s12 + C2)) / ((mu1 * mu1 + mu2 * mu2 + C1) * (s11 + s22 + C2))
    return float(m.mean())


def luma(rgb):
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def unit(rgb):
    from texture_stream.codec import unit_normals_numpy
    return unit_normals_numpy(np.asarray(rgb, np.float64))


def flip(a, b):
    import flip_evaluator as fe
    _m, mean, _p = fe.evaluate(np.clip(a / 255.0, 0, 1).astype(np.float32), np.clip(b / 255.0, 0, 1).astype(np.float32),
                               "LDR", applyMagma=False)
    return float(mean)


def metrics(ref, test, normal, alpha=False, do_flip=False):
    ref, test = np.asarray(ref, np.float64), np.asarray(test, np.float64)
    out = {}
    if normal:
        out["psnr"] = psnr(unit(ref[..., :3]), unit(test[..., :3]))
    else:
        out["psnr"] = psnr(ref[..., :3], test[..., :3])
        out["ssim"] = ssim(luma(ref), luma(test))
        if do_flip:
            out["flip"] = flip(ref[..., :3], test[..., :3])
    if alpha:
        out["a_psnr"] = psnr(ref[..., 3], test[..., 3])
    return out


def vtf_rgba(vtf_bytes):
    from texlab.vtf import decode_level, read_vtf
    v = read_vtf(vtf_bytes)
    return decode_level(v, 0).astype(np.float64)


def rows_of(group):
    root = LAB / "dump" / group / f"{group}_merged_optimized"
    rep = json.loads((LAB / "out" / f"texscreen_dump_{group}.json").read_text(encoding="utf-8"))["stream"]
    slug, ext = rep["slug"], rep["blob_ext"]
    by_path = {r["path"].lower(): r for r in rep["rows"]}
    out = []
    for js in sorted((LAB / "dumpimg" / group).glob("*.json")):
        d = json.loads(js.read_text(encoding="utf-8"))
        rel = Path(d["path"]).relative_to(root).as_posix().lower()
        r = by_path.get(rel)
        if r is None:
            continue    # kept as VTF (no gain / error)
        out.append((str(js.with_suffix(".png")), str(root / "data_static" / "gmot" / slug / f"{r['i']}{ext}"),
                    rel, d, r))
    return out


def one(task):
    png, blob, rel, d, r, do_flip = task
    from texture_stream.codec import decode, lua_vtf
    ref = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64)
    data = Path(blob).read_bytes()
    wav, _h, _f, _m = decode(data)
    fin = vtf_rgba(lua_vtf(data))
    normal, alpha = bool(r["normal"]), bool(r["alpha"])
    res = {"rel": rel, "w": d["w"], "h": d["h"], "normal": normal, "alpha": alpha, "step": r["step"], "drop": r["drop"],
           "blob": r["blob"], "before": r["before"], "rgb_db": r["rgb_db"], "class": r["class"], "direct": d["direct"]}
    for k, v in metrics(ref, wav, normal, alpha, do_flip).items():
        res["wav_" + k] = v
    for k, v in metrics(ref, fin, normal, alpha, do_flip).items():
        res["fin_" + k] = v
    return res


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    do_flip = len(sys.argv) > 3 and sys.argv[3] == "flip"
    groups = GROUPS if which == "all" else which.split(",")
    tasks = []
    for g in groups:
        tasks += [t + (do_flip,) for t in rows_of(g)]
    if sample:
        tasks = tasks[::max(1, len(tasks) // sample)][:sample]
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "16"))) as ex:
        rows = list(ex.map(one, tasks, chunksize=4))
    (LAB / "out" / f"texeval_{which.replace(',', '_')}{'_' + str(sample) if sample else ''}.json").write_text(
        json.dumps(rows, indent=1))
    for kind, sel in (("colour", lambda r: not r["normal"]), ("normal", lambda r: r["normal"])):
        rs = [r for r in rows if sel(r)]
        if not rs:
            continue
        w = np.array([r["w"] * r["h"] for r in rs], np.float64)
        wav = np.array([r["wav_psnr"] for r in rs])
        fin = np.array([r["fin_psnr"] for r in rs])
        print(f"{kind}: n={len(rs)} wavelet PSNR median {np.median(wav):.2f} (min {wav.min():.2f}); "
              f"final (after Lua DXT) median {np.median(fin):.2f} (min {fin.min():.2f}, p10 {np.percentile(fin, 10):.2f}); "
              f"DXT costs median {np.median(wav - fin):.2f} dB")
        if not rs[0]["normal"]:
            s1 = np.array([r["wav_ssim"] for r in rs]); s2 = np.array([r["fin_ssim"] for r in rs])
            print(f"   SSIM wavelet median {np.median(s1):.4f}, final median {np.median(s2):.4f} (p10 {np.percentile(s2, 10):.4f})")
            if do_flip:
                f1 = np.array([r["wav_flip"] for r in rs]); f2 = np.array([r["fin_flip"] for r in rs])
                print(f"   FLIP wavelet median {np.median(f1):.4f}, final median {np.median(f2):.4f} (p90 {np.percentile(f2, 90):.4f})")


if __name__ == "__main__":
    main()
