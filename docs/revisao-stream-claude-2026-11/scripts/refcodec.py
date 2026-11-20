"""Reference ruler (review/claude lab): bytes a state-of-the-art still-image codec needs for the same error
as GMOT's wavelet reconstruction (before DXT), per colour texture without alpha.

For each sampled colour texture: W0 = PSNR (RGB) of codec.decode(blob) against the encoder input; then the
smallest JPEG 2000 codestream (OpenJPEG through Pillow, 9/7, 5 levels, binary search on the rate) and AVIF
(libavif through Pillow, 4:2:0 and 4:4:4, speed 4, binary search on quality) whose decode reaches W0.
python refcodec.py <sample> -> out/refcodec_<sample>.json
"""
import io
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
LAB = Path(r"C:/GModLab/revisao/claude")


def psnr(a, b):
    mse = float(np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2))
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def j2k(img, ratio):
    buf = io.BytesIO()
    img.save(buf, format="JPEG2000", irreversible=True, quality_mode="rates", quality_layers=[ratio],
             num_resolutions=6, no_jp2=True)
    data = buf.getvalue()
    return data, np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(np.float64)


def avif(img, q, sub):
    buf = io.BytesIO()
    img.save(buf, format="AVIF", quality=q, subsampling=sub, speed=4)
    data = buf.getvalue()
    return data, np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(np.float64)


def search_j2k(img, ref, target):
    lo, hi = 2.0, 2000.0          # compression ratios: lo always reaches (near lossless)
    best = None
    for _ in range(14):
        mid = (lo * hi) ** 0.5
        data, dec = j2k(img, mid)
        if psnr(ref, dec) >= target:
            best, lo = data, mid
        else:
            hi = mid
    return len(best) if best else None


def search_avif(img, ref, target, sub):
    lo, hi = 0, 100               # quality: hi best
    best = None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        data, dec = avif(img, mid, sub)
        if psnr(ref, dec) >= target:
            best, hi = data, mid
        else:
            lo = mid
    if best is None:
        data, dec = avif(img, 100, sub)
        best = data if psnr(ref, dec) >= target else None
    return len(best) if best else None


def one(task):
    png, blob = task
    from texture_stream.codec import decode
    ref = np.asarray(Image.open(png).convert("RGB")).astype(np.float64)
    data = Path(blob).read_bytes()
    wav, _h, _f, _m = decode(data)
    W0 = psnr(ref, wav[..., :3])
    img = Image.fromarray(ref.astype(np.uint8), "RGB")
    return {"png": png, "w": ref.shape[1], "h": ref.shape[0], "gmot": len(data), "W0": W0,
            "j2k": search_j2k(img, ref, W0), "avif420": search_avif(img, ref, W0, "4:2:0"),
            "avif444": search_avif(img, ref, W0, "4:4:4")}


def main():
    import texeval
    sample = int(sys.argv[1])
    rows = []
    for g in texeval.GROUPS:
        rows += [(png, blob) for png, blob, _rel, _d, r in texeval.rows_of(g) if not r["normal"] and not r["alpha"]]
    rows.sort()
    rows = rows[::max(1, len(rows) // sample)][:sample]
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "16"))) as ex:
        res = list(ex.map(one, rows, chunksize=1))
    (LAB / "out" / f"refcodec_{sample}.json").write_text(json.dumps(res, indent=1))
    ok = [r for r in res if all(r[k] for k in ("j2k", "avif420", "avif444"))]
    G = sum(r["gmot"] for r in ok)
    print(f"{len(ok)}/{len(res)} textures, GMOT {G / 2**20:.2f} MiB at median W0 {np.median([r['W0'] for r in ok]):.2f} dB")
    for k in ("j2k", "avif420", "avif444"):
        s = sum(r[k] for r in ok)
        print(f"  {k:8} {s / 2**20:.2f} MiB ({100 * (s / G - 1):+.1f}% vs GMOT)")


if __name__ == "__main__":
    main()
