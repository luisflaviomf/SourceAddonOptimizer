"""Lossy GMOT encoder variants on the dumped encoder inputs (review/claude lab).

Each variant re-runs the product's choose() + encode() (numpy reference path: texlab.native off) with one
part changed, and is measured by size and by the quality of what the game shows (codec.lua_vtf = the Lua DXT
encoder, decoded back) against the encoder input:
  prod     : unchanged (checks the numpy path against the stored blob)
  steps    : step grid 2..66 in ratios of 2^(1/8) instead of the 19 product steps
  recon    : decoder puts non-zero coefficients at (|q| - 0.12) * step (one line in decoder.lua)
  dz30     : encoder dead zone rounding offset 0.30 instead of 0.40
  iso      : encoder only: isolated +-1 coefficients coded as 0
  w97      : CDF 9/7 lifting wavelet instead of 5/3 (decoder synthesis changes)
  fin      : the step chosen against the FINAL image (after the Lua DXT) with the target = the product's own
             final PSNR of that texture (same quality as today by construction)
  fin_dxt  : fin with a better DXT1 encoder in the runtime (PCA axis + 3 least-squares passes + nearest selectors)
python texexp.py <variant,...> <sample> [groups] -> out/texexp_<variant>_<sample>.json
"""
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


# ------------------------------------------------------------------ 9/7 lifting (CDF, JPEG 2000 irreversible)
A1, A2, A3, A4 = -1.586134342059924, -0.052980118572961, 0.882911075530934, 0.443506852043971
K97 = 1.230174104914001


def fwd97(x):
    s, d = x[..., 0::2].copy(), x[..., 1::2].copy()

    def nxt(a):
        return np.concatenate([a[..., 1:], a[..., -1:]], -1)

    def prv(a):
        return np.concatenate([a[..., :1], a[..., :-1]], -1)
    d += A1 * (s + nxt(s))
    s += A2 * (d + prv(d))
    d += A3 * (s + nxt(s))
    s += A4 * (d + prv(d))
    # scaled so that one step size suits every band, like the product's sqrt(2) scaling of the 5/3
    # orthonormal scaling (DC gain sqrt 2, Nyquist gain sqrt 2), like the product's 5/3 scaled by sqrt 2
    return s * (np.sqrt(2) / K97), d * (K97 / np.sqrt(2))


def inv97(s, d):
    def nxt(a):
        return np.concatenate([a[..., 1:], a[..., -1:]], -1)

    def prv(a):
        return np.concatenate([a[..., :1], a[..., :-1]], -1)
    s = s * (K97 / np.sqrt(2))
    d = d * (np.sqrt(2) / K97)
    s = s - A4 * (d + prv(d))
    d = d - A3 * (s + nxt(s))
    s = s - A2 * (d + prv(d))
    d = d - A1 * (s + nxt(s))
    x = np.empty(s.shape[:-1] + (s.shape[-1] * 2,), np.float64)
    x[..., 0::2], x[..., 1::2] = s, d
    return x


def dwt97(p, levels):
    bands = []
    ll = np.asarray(p, np.float64)
    for _ in range(levels):
        lo, hi = fwd97(ll)
        lolo, lohi = fwd97(lo.swapaxes(0, 1))
        hilo, hihi = fwd97(hi.swapaxes(0, 1))
        bands.append((lohi.swapaxes(0, 1), hilo.swapaxes(0, 1), hihi.swapaxes(0, 1)))
        ll = lolo.swapaxes(0, 1)
    return ll, bands[::-1]


def idwt97(ll, bands):
    for lh, hl, hh in bands:
        lo = inv97(ll.swapaxes(0, 1), lh.swapaxes(0, 1)).swapaxes(0, 1)
        hi = inv97(hl.swapaxes(0, 1), hh.swapaxes(0, 1)).swapaxes(0, 1)
        ll = inv97(lo, hi)
    return ll


# ------------------------------------------------------------------ better DXT1 (runtime candidate)
def better_dxt1(px):
    """px (nb,16,3) -> (nb,8) uint8: PCA axis endpoints, 3 least-squares passes, selectors by nearest palette
    colour. Same block layout as codec.lua_dxt1."""
    from texture_stream.codec import _q5, _q6
    nb = px.shape[0]
    m = px.mean(1)
    d = px - m[:, None, :]
    cov = np.einsum("nki,nkj->nij", d, d)
    v = np.ones((nb, 3)) / np.sqrt(3)
    for _ in range(6):
        v = np.einsum("nij,nj->ni", cov, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    t = np.einsum("nki,ni->nk", d, v)
    e0 = m + v * t.max(1, keepdims=True)
    e1 = m + v * t.min(1, keepdims=True)

    def quant565(e):
        return _q5(e[:, 0]) * 2048 + _q6(e[:, 1]) * 32 + _q5(e[:, 2])

    def expand(c):
        r, g, b = np.floor(c / 2048), np.floor(c / 32) % 64, c % 32
        return np.stack([r * 8 + np.floor(r / 4), g * 4 + np.floor(g / 16), b * 8 + np.floor(b / 4)], -1)
    best = None
    for it in range(4):
        c0, c1 = quant565(e0), quant565(e1)
        sw = c0 < c1
        c0, c1 = np.where(sw, c1, c0), np.where(sw, c0, c1)
        p0, p1 = expand(c0), expand(c1)
        pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)          # selector codes 0,1,2,3
        dist = ((px[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)              # nb,16,4
        sel = dist.argmin(2)
        err = dist.min(2).sum(1)
        flat = c0 == c1
        sel[flat] = 0
        err = np.where(flat, ((px - p0[:, None, :]) ** 2).sum((1, 2)), err)
        cand = (c0, c1, sel, err)
        if best is None:
            best = cand
        else:
            keep = err < best[3]
            best = tuple(np.where(keep[:, None] if a.ndim == 2 else keep, a, b) for a, b in zip(cand, best))
        # least squares endpoints for these selectors (weights of p0: 1, 0, 2/3, 1/3)
        wt = np.choose(sel, [1.0, 0.0, 2 / 3, 1 / 3])
        wu = 1 - wt
        a11, a22, a12 = (wt * wt).sum(1), (wu * wu).sum(1), (wt * wu).sum(1)
        x1 = (wt[..., None] * px).sum(1)
        x2 = (wu[..., None] * px).sum(1)
        det = a11 * a22 - a12 * a12
        good = np.abs(det) > 1e-6
        ds = np.where(good, det, 1)
        n0 = (a22[:, None] * x1 - a12[:, None] * x2) / ds[:, None]
        n1 = (a11[:, None] * x2 - a12[:, None] * x1) / ds[:, None]
        e0 = np.where(good[:, None], n0, e0)
        e1 = np.where(good[:, None], n1, e1)
    c0, c1, sel, _e = best
    c0, c1 = c0.astype(np.int64), c1.astype(np.int64)
    out = np.zeros((nb, 8), np.uint8)
    out[:, 0], out[:, 1], out[:, 2], out[:, 3] = c0 & 255, c0 >> 8, c1 & 255, c1 >> 8
    pw = np.array([1, 4, 16, 64])
    for row in range(4):
        out[:, 4 + row] = (sel[:, 4 * row:4 * row + 4] * pw).sum(1)
    return out


# ------------------------------------------------------------------ variant setup
def setup(variant):
    from texlab import native
    native.available = lambda: False
    from texture_stream import choose, codec
    if variant == "steps":
        choose.STEPS = [round(float(x), 4) for x in 2 * 2 ** (np.arange(0, 48) / 8)]
    if variant == "dz30":
        def quant(c, st):
            return (np.sign(c) * np.floor(np.abs(c) / st + 0.30)).astype(np.int64)
        codec.quant = choose.quant = quant
    if variant.startswith("anf"):
        # alpha plane without noise filling (encoder writes no noise map for it: decoder unchanged)
        import inspect
        src = inspect.getsource(codec.encode).replace("fills.append(fill_levels)", "fills.append(0)")
        exec(compile(src, "codec_encode_anf", "exec"), codec.__dict__)
    if variant == "alpha3" or (variant.startswith("anf") and variant[3:]):
        # alpha plane target N dB lower (alpha3: 3; anfN: N, e.g. anf1.5); colour/normal planes unchanged
        off = 3.0 if variant == "alpha3" else float(variant[3:])
        old = choose._search

        def _search(fn, target):
            return old(fn, target - off if getattr(fn, "__name__", "") == "q_a" else target)
        choose._search = _search
    if variant == "iso":
        # encoder only: an isolated +-1 (all 8 neighbours zero) is coded as 0 (the decoder is unchanged)
        def quant(c, st):
            q = (np.sign(c) * np.floor(np.abs(c) / st + 0.4)).astype(np.int64)
            if q.ndim != 2 or min(q.shape) < 3:
                return q
            nz = (q != 0).astype(np.int8)
            p = np.pad(nz, 1)
            nb = sum(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx]
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
            q[(np.abs(q) == 1) & (nb == 0)] = 0
            return q
        codec.quant = choose.quant = quant
    if variant == "recon":
        D = 0.12

        def _recon(ll, bands, st, drop, L):
            rb = []
            for li, trio in enumerate(bands):
                fine = li >= L - drop
                rb.append(tuple(np.zeros_like(b) if fine else rq(choose.quant(b, st), st) for b in trio))
            return codec.idwt(np.round(ll / st) * st, rb)

        def rq(q, st):
            return np.where(q == 0, 0.0, (q - D * np.sign(q)) * st)
        choose._recon = _recon
        old_read = codec.read_band

        def read_band(buf, pos, shape, st, index):
            q, pos2 = old_read(buf, pos, shape, st, index)
            t = buf[pos] & 15
            if t and shape[0] * shape[1] > 1:
                # non-zero stored values (not noise) move towards zero; noise fill only replaced zeros
                n = shape[0] * shape[1]
                start = pos + 1 + ((max(1, shape[0] // 8) * max(1, shape[1] // 8)) if buf[pos] & 16 else 0)
                raw = np.frombuffer(buf, np.int8 if t == 1 else "<i2", n, start).reshape(shape)
                q = np.where(raw != 0, (raw - D * np.sign(raw)) * st, q)
            return q, pos2
        codec.read_band = read_band
    if variant == "w97":
        codec.dwt = choose.dwt = dwt97
        codec.idwt = choose.idwt = idwt97
        codec.dwt_numpy = dwt97
    if variant == "fin_dxt":
        codec.lua_dxt1 = better_dxt1
    return choose, codec


def final_rgba(codec, data):
    from texlab.vtf import decode_level, read_vtf
    return decode_level(read_vtf(codec.lua_vtf(data)), 0).astype(np.float64)


def run_one(args):
    variant, png, js, blob = args
    from texlab.vtf import read_vtf
    import texeval
    choose, codec = setup(variant)
    d = json.loads(Path(js).read_text(encoding="utf-8"))
    rgba = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64)
    data0 = Path(blob).read_bytes()
    # the VTF the encoder wrote its header from: rebuilt from the blob's stored header
    from gmotlib import parse
    g = parse(data0)
    v = _template(g, d)
    use = None if d["use"] is None else {"w": d["use"]["w"], "roles": set(d["use"]["roles"])}
    normal = d["choice"]["normal"]
    alpha = d["choice"]["alpha"]
    ref_final = None
    res = {"rel": Path(d["path"]).name, "w": d["w"], "h": d["h"], "normal": normal, "alpha": alpha,
           "size0": len(data0), "step0": d["choice"]["step"]}
    if variant.startswith("fin") and variant[3:].isdigit():
        # uniform floor on what the game shows: final PSNR >= N dB in every texture
        data = _search_final(choose, codec, v, rgba, use, d, float(variant[3:]), normal, alpha, plateau=True)
        res["target_final"] = float(variant[3:])
    elif variant in ("fin", "fin_dxt"):
        # product's own final quality of this texture (Lua DXT as today)
        import importlib
        from texture_stream import codec as c0
        importlib.reload(c0)
        m0 = texeval.metrics(rgba, final_rgba(c0, data0), normal, alpha)
        ref_final = m0["psnr"]
        choose, codec = setup(variant)
        data = _search_final(choose, codec, v, rgba, use, d, ref_final, normal, alpha)
    else:
        tr = []
        c = choose.choose(v, rgba, use, d["target"], tr)
        data = codec.encode(v, rgba, c["step"], alpha_step=c["alpha_step"], chroma_drop=c["drop"], normal=c["normal"],
                            keep_alpha=c["alpha"], fill_levels=d["fill"], preview_side=512, transforms=tr)
        res["step"] = c["step"]
    fin = final_rgba(codec, data)
    wav, _h, _f, _m = codec.decode(data)
    res["size"] = len(data)
    keep = LAB / "out" / "texexp_blobs" / variant
    keep.mkdir(parents=True, exist_ok=True)
    (keep / (Path(png).stem + ".gmot")).write_bytes(data)
    res["png"], res["blob0"] = png, blob
    for k, val in texeval.metrics(rgba, fin, normal, alpha).items():
        res["fin_" + k] = val
    for k, val in texeval.metrics(rgba, wav, normal, alpha).items():
        res["wav_" + k] = val
    if ref_final is not None:
        res["ref_final"] = ref_final
    return res


def _template(g, d):
    """a texlab.vtf.Vtf whose header is the one stored in the blob (what encode copies)"""
    from texlab.vtf import read_vtf, image_bytes
    hdr = bytes(g["header"])
    fmt, w, h, mips = g["out_fmt"], g["w"], g["h"], g["mips"]
    body = b"".join(b"\0" * image_bytes(fmt, max(1, w >> i), max(1, h >> i)) for i in range(mips))
    v = read_vtf(hdr + body)
    v.fmt = d["fmt"]       # the source format decides the output format (RAW_ORDER / DXT)
    return v


def _search_final(choose, codec, v, rgba, use, d, target_final, normal, alpha, plateau=False):
    """largest product-search step whose FINAL PSNR (after the runtime's DXT) reaches target_final; chroma drop
    and alpha as the product chose them (alpha step kept)"""
    c = d["choice"]
    L = codec.levels_for(v.width, v.height)
    import texeval

    def enc(st):
        return codec.encode(v, rgba, st, alpha_step=c["alpha_step"], chroma_drop=c["drop"], normal=normal,
                            keep_alpha=alpha, fill_levels=d["fill"], preview_side=512)
    steps = [round(float(x), 4) for x in 2 * 2 ** (np.arange(0, 48) / 8)]
    lo, hi = -1, len(steps)
    cache = {}
    if plateau:
        # when even the smallest step cannot reach the floor (the DXT1 limit), aim 0.3 dB under the best
        data = enc(steps[0])
        best = texeval.metrics(rgba, final_rgba(codec, data), normal, False)["psnr"]
        cache[0] = data
        if best < target_final:
            target_final = best - 0.3
    while hi - lo > 1:
        mid = (lo + hi) // 2
        data = enc(steps[mid])
        q = texeval.metrics(rgba, final_rgba(codec, data), normal, False)["psnr"]
        cache[mid] = data
        if q >= target_final - 0.02:
            lo = mid
        else:
            hi = mid
    return cache.get(max(lo, 0)) or enc(steps[max(lo, 0)])


def tasks_for(groups, sample):
    import texeval
    rows = []
    for g in groups:
        rows += texeval.rows_of(g)
    rows.sort(key=lambda r: r[0])
    rows = rows[::max(1, len(rows) // sample)][:sample] if sample else rows
    return [(png, png[:-4] + ".json", blob) for png, blob, _rel, _d, _r in rows]


def main():
    variants = sys.argv[1].split(",")
    sample = int(sys.argv[2])
    groups = sys.argv[3].split(",") if len(sys.argv) > 3 else ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]
    tasks = tasks_for(groups, sample)
    for variant in variants:
        with ProcessPoolExecutor(int(os.environ.get("NPROC", "16"))) as ex:
            rows = list(ex.map(run_one, [(variant,) + t for t in tasks], chunksize=1))
        (LAB / "out" / f"texexp_{variant}_{sample}.json").write_text(json.dumps(rows, indent=1))
        s0 = sum(r["size0"] for r in rows)
        s1 = sum(r["size"] for r in rows)
        col = [r for r in rows if not r["normal"]]
        nrm = [r for r in rows if r["normal"]]
        msg = f"{variant}: n={len(rows)} size {s0 / 2**20:.2f} -> {s1 / 2**20:.2f} MiB ({100 * (s1 / s0 - 1):+.1f}%)"
        for name, rs in (("colour", col), ("normal", nrm)):
            if rs:
                msg += (f"; {name} final PSNR median {np.median([r['fin_psnr'] for r in rs]):.2f}"
                        f" (min {min(r['fin_psnr'] for r in rs):.2f})")
        if col:
            msg += f"; SSIM median {np.median([r['fin_ssim'] for r in col]):.4f}"
        print(msg, flush=True)


if __name__ == "__main__":
    main()
