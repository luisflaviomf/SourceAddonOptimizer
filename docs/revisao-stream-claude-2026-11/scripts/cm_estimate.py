"""Lossless re-coding estimate of the GMOT bands (same coefficients, same image in game).

For every blob of lab/<tag>: exact LZMA size of every chunk (as stored) and the ideal adaptive code length
(Krichevsky-Trofimov estimator: exact for an adaptive arithmetic coder that counts, in closed form from the
counts) of several context models, one model per chunk (chunks stay independently decodable):
  o0   : order 0 over the clipped coefficient (|q| <= 14 + sign, escape Exp-Golomb), no context
  b_wn : binary decomposition (zero?, sign, |q|>1, >2, ... unary to 14, escape) with the context of the
         left and upper neighbours' magnitudes
  b_n6 : the same with left, up, up-left, up-right, two-left, two-up
  b_n6p: b_n6 + the parent coefficient (same orientation, one level coarser; needs that band in the decoder)
Noise maps (3-bit tile codes) and the LL band (coded after a LOCO-I/MED prediction) are added to every model.
python cm_estimate.py <tag> [max_blobs]  -> out/cm_<tag>.json
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.special import gammaln

sys.path.insert(0, str(Path(__file__).parent))
import gmotlib  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
LN2 = np.log(2.0)
CAP = 14  # unary magnitude bins


def kt_binary(keys, bits):
    """sum over contexts of the KT code length of the bit sequence of each context (bits)."""
    if len(keys) == 0:
        return 0.0
    k, inv = np.unique(keys, return_inverse=True)
    n1 = np.bincount(inv, weights=bits, minlength=len(k))
    n = np.bincount(inv, minlength=len(k)).astype(np.float64)
    n0 = n - n1
    L = -(gammaln(n0 + 0.5) + gammaln(n1 + 0.5) - gammaln(n + 1.0) - np.log(np.pi))
    return float(L.sum() / LN2)


def kt_multi(keys, syms, K):
    if len(syms) == 0:
        return 0.0
    kk = keys.astype(np.int64) * K + syms
    u, cnt = np.unique(kk, return_counts=True)
    ctx = u // K
    cu, ci = np.unique(ctx, return_inverse=True)
    N = np.bincount(ci, weights=cnt, minlength=len(cu))
    L = (gammaln(N + K / 2) - gammaln(K / 2)).sum() - (gammaln(cnt + 0.5) - gammaln(0.5)).sum()
    return float(L / LN2)


def shift(a, dy, dx):
    """a shifted so that out[y, x] = a[y - dy, x - dx] (zero outside)"""
    h, w = a.shape
    out = np.zeros_like(a)
    ys, xs = slice(max(dy, 0), h + min(dy, 0)), slice(max(dx, 0), w + min(dx, 0))
    yd, xd = slice(max(-dy, 0), h + min(-dy, 0)), slice(max(-dx, 0), w + min(-dx, 0))
    out[ys, xs] = a[yd, xd]
    return out


def qbin(s):
    """neighbour magnitude sum -> 0..7"""
    return np.minimum(np.where(s < 3, s, np.where(s < 5, 3, np.where(s < 8, 4, np.where(s < 13, 5, np.where(s < 21, 6, 7))))), 7)


def escape_bits(m):
    """Exp-Golomb (k=0) bits of m - CAP for m > CAP"""
    e = m[m > CAP] - CAP
    return float((2 * np.floor(np.log2(e)) + 1).sum()) if len(e) else 0.0


def binary_model(q, ctx):
    """code length (bits) of q with the binary decomposition, contexts ctx (int array, same shape)."""
    m = np.abs(q).ravel()
    c = ctx.ravel().astype(np.int64)
    bits = kt_binary(c, (m > 0).astype(np.float64))
    nz = m > 0
    # signs: context of the signs of the upper and left neighbours (computed by caller in ctx high bits? keep raw)
    bits += float(nz.sum())  # one raw bit per sign (contexts gain little on wavelet signs)
    mm, cc = m[nz], c[nz]
    for j in range(1, CAP + 1):
        sel = mm >= j
        if not sel.any():
            break
        bits += kt_binary(cc[sel] * 16 + min(j, 15), (mm[sel] > j).astype(np.float64))
    return bits + escape_bits(m)


def ctx_wn(m):
    return qbin(shift(m, 0, 1) + shift(m, 1, 0))


def ctx_n6(m):
    s = 2 * shift(m, 0, 1) + 2 * shift(m, 1, 0) + shift(m, 1, 1) + shift(m, 1, -1) + shift(m, 0, 2) + shift(m, 2, 0)
    return qbin((s + 1) // 2)


def med_residual(q):
    """LOCO-I / MED prediction residual of the LL band (raster order)."""
    a, b, c = shift(q, 0, 1), shift(q, 1, 0), shift(q, 1, 1)
    # first row / column: plain left / up
    pred = np.where(c >= np.maximum(a, b), np.minimum(a, b), np.where(c <= np.minimum(a, b), np.maximum(a, b), a + b - c))
    pred[0, 1:] = q[0, :-1]
    pred[1:, 0] = q[:-1, 0]
    pred[0, 0] = 0
    return q - pred


def blob_estimate(path):
    data = Path(path).read_bytes()
    g = gmotlib.parse(data)
    res = {"file": len(data), "container": 6 + 4 * len(g["sizes"]), "lzma": float(sum(g["sizes"])),
           "o0": 0.0, "b_wn": 0.0, "b_n6": 0.0, "b_n6p": 0.0, "chunks": len(g["sizes"]),
           "raw_coef": 0, "noise_lzma_share": 0.0}
    by_key = {(b["plane"], b["lvl"], b["k"]): b for b in g["bands"]}
    # per chunk fixed costs common to every model: chunk 0 header (VTF header etc.), ~4 bytes of
    # arithmetic coder flush per chunk, the band type byte
    head_extra = 9 + 4 * g["n"] + 4 + len(g["header"]) + 2
    hb = gmotlib.lzma_chunk(bytes(head_extra)) if False else None  # noqa: F841 (kept as reference)
    for name in ("o0", "b_wn", "b_n6", "b_n6p"):
        res[name] += 8.0 * (head_extra + 5 * len(g["sizes"]))
    for b in g["bands"]:
        q = b["q"]
        if b["codes"] is not None:
            cb = b["codes"].astype(np.int64).ravel()
            prev = np.concatenate([[0], cb[:-1]])
            nb = kt_multi(prev, cb, 8)
            for name in ("o0", "b_wn", "b_n6", "b_n6p"):
                res[name] += nb
        if b["kind"] == 0 and b["codes"] is None:
            for name in ("o0", "b_wn", "b_n6", "b_n6p"):
                res[name] += 8
            continue
        if b["k"] == 0:
            q = med_residual(q)
        m = np.abs(q)
        res["raw_coef"] += q.size
        sym = np.clip(q, -CAP - 1, CAP + 1) + CAP + 1
        res["o0"] += kt_multi(np.zeros(q.size, np.int64), sym.ravel(), 2 * CAP + 3) + escape_bits(m.ravel())
        res["b_wn"] += binary_model(q, ctx_wn(m))
        c6 = ctx_n6(m)
        res["b_n6"] += binary_model(q, c6)
        par = by_key.get((b["plane"], b["lvl"] + 1, b["k"])) if b["k"] else None
        if par is not None and par["q"].shape[0] * 2 == q.shape[0] and par["q"].shape[1] * 2 == q.shape[1]:
            pm = np.abs(par["q"]).repeat(2, 0).repeat(2, 1)
            pc = np.minimum(pm, 2)
            res["b_n6p"] += binary_model(q, c6 * 3 + pc)
        else:
            res["b_n6p"] += binary_model(q, c6)
    for name in ("o0", "b_wn", "b_n6", "b_n6p"):
        res[name] = res[name] / 8.0 + res["container"]
    res["lzma"] += res["container"]
    res["mode"] = g["mode"]
    res["n"] = g["n"]
    res["side"] = max(g["w"], g["h"])
    return res


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "cb"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    blobs = sorted(p for p in (LAB / tag).rglob("data_static/gmot/*/*") if p.is_file())
    if limit:
        blobs = blobs[::max(1, len(blobs) // limit)][:limit]
    with ProcessPoolExecutor(int(__import__("os").environ.get("NPROC", "8"))) as ex:
        rows = list(ex.map(blob_estimate, map(str, blobs), chunksize=4))
    tot = {k: sum(r[k] for r in rows) for k in ("file", "lzma", "o0", "b_wn", "b_n6", "b_n6p", "raw_coef", "chunks")}
    by_mode = {}
    for r in rows:
        key = ("normal" if r["mode"] == 1 else "colour") + ("+a" if r["n"] in (3 if r["mode"] == 1 else 4,) else "")
        d = by_mode.setdefault(key, {k: 0.0 for k in ("count", "file", "lzma", "b_n6", "b_n6p")})
        d["count"] += 1
        for k in ("file", "lzma", "b_n6", "b_n6p"):
            d[k] += r[k]
    out = {"blobs": len(rows), "total": tot, "by_mode": by_mode,
           "rows": [dict(r, path=str(p.relative_to(LAB))) for r, p in zip(rows, blobs)]}
    (LAB / "out" / f"cm_{tag.replace('/', '_')}{'_' + str(limit) if limit else ''}.json").write_text(json.dumps(out, indent=1))
    MiB = 2 ** 20
    print(f"{len(rows)} blobs; file {tot['file'] / MiB:.2f} MiB (stored LZMA {tot['lzma'] / MiB:.2f})")
    for k in ("o0", "b_wn", "b_n6", "b_n6p"):
        print(f"  {k:6} {tot[k] / MiB:8.2f} MiB  {100 * (tot[k] / tot['file'] - 1):+.1f}%")
    for k, d in by_mode.items():
        print(f"  {k:9} n={int(d['count'])} file {d['file'] / MiB:.2f} b_n6 {d['b_n6'] / MiB:.2f} "
              f"({100 * (d['b_n6'] / d['file'] - 1):+.1f}%) b_n6p {d['b_n6p'] / MiB:.2f}")


if __name__ == "__main__":
    main()
