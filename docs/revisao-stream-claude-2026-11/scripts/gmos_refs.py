"""Alternatives for the GMOS back-reference streams (pref/nref/uvref), lossless. LZMA 9e size of the three
ref streams (byte planes, like the product) under each coding."""
import sys, os, glob, lzma, json
import numpy as np
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(__file__))
import gmoslib
from gmos_exp import planes, lz, first_pos_from_C

def ref_codes(ids, C, mode, K=64):
    """ids: value id per vertex (first-appearance numbering). -> codes per vertex"""
    n = len(ids)
    out = np.zeros(n, np.int64)
    if mode == "vert":            # product: vertices back to the last vertex with that value
        last = {}
        for i, v in enumerate(ids.tolist()):
            j = last.get(v); out[i] = 0 if j is None else i - j; last[v] = i
        return out
    if mode == "id":              # values back: (values so far) - id
        seen = 0
        for i, v in enumerate(ids.tolist()):
            if v == seen: seen += 1; out[i] = 0
            else: out[i] = seen - v
        return out
    if mode == "strip":           # twin among the last K strip entries before the vertex appears: 1..K; else K + id distance
        first = first_pos_from_C(C)
        seen = 0
        for i, v in enumerate(ids.tolist()):
            if v == seen:
                seen += 1; out[i] = 0; continue
            c = first[i]
            win = C[max(0, c - K):c][::-1]
            k = 0
            for t, u in enumerate(win.tolist()):
                if ids[u] == v: k = t + 1; break
            out[i] = k if k else K + (seen - v)
        return out

def one(path):
    g = gmoslib.parse(open(path, "rb").read())
    res = {}
    for mode in ("vert", "id", "strip"):
        S = {"p": [], "n": [], "u": []}
        for gr in g["groups"]:
            for key, ids in (("p", gr["pid"]), ("n", gr["nid"]), ("u", gr["uid"])):
                S[key].append(ref_codes(ids, gr["C"], mode))
        res[mode] = sum(lz(planes(np.concatenate(S[k]).astype(np.uint64))) for k in S)
        res[mode + "_split"] = {k: lz(planes(np.concatenate(S[k]).astype(np.uint64))) for k in S}
    return res

if __name__ == "__main__":
    fs = sorted(glob.glob(sys.argv[1] + "/**/data_static/gmo/*", recursive=True))
    fs = fs[::max(1, len(fs) // int(sys.argv[2]))]
    with ProcessPoolExecutor(8) as ex:
        rows = list(ex.map(one, fs))
    for m in ("vert", "id", "strip"):
        t = sum(r[m] for r in rows)
        sp = {k: sum(r[m + "_split"][k] for r in rows) for k in "pnu"}
        print(m, f"{t/2**20:.2f} MiB", {k: round(v/2**20, 2) for k, v in sp.items()})
