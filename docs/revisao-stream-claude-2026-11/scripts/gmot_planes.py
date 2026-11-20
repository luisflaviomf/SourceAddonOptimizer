"""GMOT bytes per plane and level including chunk 0 (chunk 0 split by each band's LZMA size alone, scaled to the
chunk's real size)."""
import sys, os, json
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import gmotlib

def one(path):
    g = gmotlib.parse(Path(path).read_bytes())
    planes = (["nx", "ny"] if g["mode"] == 1 else ["y", "co", "cg"]) + (["a"] if g["n"] > (2 if g["mode"] == 1 else 3) else [])
    kind = "normal" if g["mode"] == 1 else "colour"
    c = Counter()
    b0 = [b for b in g["bands"] if b["chunk"] == 0]
    alone = [gmotlib.lzma_chunk(gmotlib.band_bytes(b)) - 13 for b in b0]
    scale = (g["sizes"][0] - 40) / max(1, sum(alone))
    for b, s in zip(b0, alone):
        c[f"{kind}:{planes[b['plane']]}:c0"] += s * scale
    c[f"{kind}:header"] += 40
    for b in g["bands"]:
        if b["chunk"]:
            c[f"{kind}:{planes[b['plane']]}:fine"] += g["sizes"][b["chunk"]]
    c["file"] += sum(g["sizes"]) + 6 + 4 * len(g["sizes"])
    c[f"side{max(g['w'], g['h'])}"] += sum(g["sizes"])
    c[f"n_side{max(g['w'], g['h'])}"] += 1
    return c

if __name__ == "__main__":
    fs = sorted(Path(r"C:/GModLab/revisao/claude/cb").rglob("data_static/gmot/*/*"))
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "6"))) as ex:
        tot = sum(ex.map(one, map(str, fs), chunksize=8), Counter())
    MiB = 2**20
    for k, v in sorted(tot.items()):
        print(f"{k:22} {v/MiB if not k.startswith('n_') else v:9.2f}")
