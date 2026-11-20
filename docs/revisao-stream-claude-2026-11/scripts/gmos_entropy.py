"""Adaptive-coder estimate (KT, closed form) for each GMOS field vs its LZMA 9e size alone."""
import sys, os, glob
import numpy as np
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(__file__))
import gmoslib
from cm_estimate import kt_multi
from gmos_exp import planes, lz

def cls(v):
    v = np.asarray(v, np.int64)
    return np.minimum(v, 15) + np.where(v > 15, np.minimum(np.floor(np.log2(np.maximum(v, 1))).astype(np.int64), 30), 0)

def est(v):
    """order-1 (context = class of the previous value) over the Elias-gamma bucket of each value + raw bits"""
    v = np.asarray(v, np.int64)
    if len(v) == 0: return 0.0, 0.0
    b = np.where(v == 0, 0, np.floor(np.log2(np.maximum(v, 1))).astype(np.int64) + 1)  # 0 or 1+floor(log2)
    raw = np.maximum(b - 1, 0).sum()
    prev = np.concatenate([[0], b[:-1]])
    o0 = kt_multi(np.zeros(len(b), np.int64), b, 40) + raw
    o1 = kt_multi(prev, b, 40) + raw
    return o0 / 8, o1 / 8

def one(path):
    g = gmoslib.parse(open(path, "rb").read())
    r = {}
    for f in gmoslib.FIELDS:
        v = g["S"][f]
        if f in ("px", "py", "pz", "u", "v"):
            v = ((v << 1) ^ (v >> 63))
        r[f] = (lz(planes(v.astype(np.uint64))),) + est(v)
    return r

if __name__ == "__main__":
    fs = sorted(glob.glob(sys.argv[1] + "/**/data_static/gmo/*", recursive=True))
    fs = fs[::max(1, len(fs) // int(sys.argv[2]))]
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(one, fs))
    T = [0, 0, 0]
    for f in gmoslib.FIELDS:
        s = [sum(r[f][k] for r in rows) for k in range(3)]
        T = [a + b for a, b in zip(T, s)]
        print(f"{f:6} lzma {s[0]/2**20:6.2f}  o0 {s[1]/2**20:6.2f}  o1 {s[2]/2**20:6.2f} MiB")
    print(f"total  lzma {T[0]/2**20:6.2f}  o0 {T[1]/2**20:6.2f}  o1 {T[2]/2**20:6.2f}")
