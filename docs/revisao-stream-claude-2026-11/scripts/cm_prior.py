"""Shared starting probabilities for the context coder (review/claude lab): trained on the even blobs of the
bench (final state of every chunk, averaged per chunk kind in the logit domain), measured on the odd ones.
The priors would be a fixed table in the decoder (a few KB), the same for every addon: no per-addon rule.
python cm_prior.py <tag>
"""
import ctypes
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cm_real  # noqa: E402
import gmotlib  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
CFG = np.array([12, 4, 1, 1, 0], np.int32)
PL = {0: ["y", "co", "cg", "a"], 1: ["nx", "ny", "a"]}


def lib():
    L = cm_real.lib()
    V = ctypes.c_void_p
    L.cm_chunk_prior.restype = ctypes.c_ssize_t
    L.cm_chunk_prior.argtypes = [V, ctypes.c_int, V, ctypes.c_size_t, V, ctypes.c_size_t, V, ctypes.c_int, V, V]
    L.cm_model_len.restype = ctypes.c_size_t
    L.cm_model_len.argtypes = [ctypes.c_int]
    return L


def chunks_of(g):
    ch = defaultdict(list)
    for b in g["bands"]:
        ch[b["chunk"]].append(b)
    for c in sorted(ch):
        bl = [b for b in ch[c] if not (b["kind"] == 0 and b["codes"] is None)]
        if not bl:
            continue
        for b in bl:
            b["class"] = (min(b["plane"], 1) * 4 + b["k"]) if c == 0 else 0
        mode = g["mode"]
        if c == 0:
            key, ncl = ("c0", mode), 8
        else:
            planes = (["nx", "ny"] if mode == 1 else ["y", "co", "cg"]) + ["a"]
            key, ncl = ("f", mode, planes[bl[0]["plane"]], bl[0]["lvl"]), 1
        yield key, ncl, bl


def code(L, bl, ncl, prior=None, want_state=False):
    meta = np.array([[b["q"].shape[0], b["q"].shape[1], b["class"], 1 if b["k"] == 0 else 0,
                      1 if b["codes"] is not None else 0, -1] for b in bl], np.int32)
    q = np.ascontiguousarray(np.concatenate([b["q"].ravel() for b in bl]).astype(np.int32))
    cl = [b["codes"].ravel() for b in bl if b["codes"] is not None]
    codes = np.ascontiguousarray(np.concatenate(cl).astype(np.uint8)) if cl else np.zeros(1, np.uint8)
    n = L.cm_model_len(ncl)
    st = np.zeros(n, np.uint32) if want_state else None
    pr = None if prior is None else np.ascontiguousarray(prior.astype(np.uint32))
    size = L.cm_chunk_prior(meta.ctypes.data, len(bl), q.ctypes.data, q.size, codes.ctypes.data,
                            int(sum(x.size for x in cl)), CFG.ctypes.data, ncl,
                            None if pr is None else pr.ctypes.data, None if st is None else st.ctypes.data)
    return size, st


def train_one(path):
    L = lib()
    g = gmotlib.parse(Path(path).read_bytes())
    out = defaultdict(lambda: [None, 0])
    for key, ncl, bl in chunks_of(g):
        _s, st = code(L, bl, ncl, want_state=True)
        p = np.clip(st.astype(np.float64) / 4096, 1e-3, 1 - 1e-3)
        lg = np.log(p / (1 - p))
        acc = out[key]
        acc[0] = lg if acc[0] is None else acc[0] + lg
        acc[1] += 1
    return dict(out)


def test_one(args):
    path, priors = args
    L = lib()
    g = gmotlib.parse(Path(path).read_bytes())
    s0 = s1 = 0
    for key, ncl, bl in chunks_of(g):
        a, _ = code(L, bl, ncl)
        b, _ = code(L, bl, ncl, priors.get(key))
        s0 += a
        s1 += b
    return s0, s1, max(g["w"], g["h"])


def main():
    tag = sys.argv[1]
    blobs = sorted(str(p) for p in (LAB / tag).rglob("data_static/gmot/*/*") if p.is_file())
    train, test = blobs[0::2], blobs[1::2]
    nproc = int(os.environ.get("NPROC", "8"))
    acc = defaultdict(lambda: [None, 0])
    with ProcessPoolExecutor(nproc) as ex:
        for part in ex.map(train_one, train, chunksize=8):
            for k, (lg, n) in part.items():
                a = acc[k]
                a[0] = lg if a[0] is None else a[0] + lg
                a[1] += n
    priors = {k: np.round(4096 / (1 + np.exp(-(lg / n)))).clip(31, 4065) for k, (lg, n) in acc.items()}
    print("prior tables:", len(priors), "entries", sum(len(v) for v in priors.values()))
    with ProcessPoolExecutor(nproc) as ex:
        res = list(ex.map(test_one, [(p, priors) for p in test], chunksize=8))
    s0, s1 = sum(r[0] for r in res), sum(r[1] for r in res)
    print(f"held-out half: coded bands {s0 / 2**20:.2f} -> {s1 / 2**20:.2f} MiB with priors ({100 * (s1 / s0 - 1):+.1f}%)")
    for side in sorted({r[2] for r in res}):
        a = sum(r[0] for r in res if r[2] == side)
        b = sum(r[1] for r in res if r[2] == side)
        print(f"  side {side:5}: {100 * (b / a - 1):+.1f}%  ({a / 2**20:.2f} MiB)")


if __name__ == "__main__":
    main()
