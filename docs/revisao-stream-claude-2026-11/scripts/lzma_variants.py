"""Pre-LZMA layouts of the same GMOT bands (decoded by util.Decompress + a cheap Lua pass)."""
import sys, os, json, lzma
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import gmotlib
LAB = Path(r"C:/GModLab/revisao/claude")

def lz(b, lc=3, lp=0, pb=0):
    return gmotlib.lzma_chunk(b, preset=9, pb=pb, lc=lc, lp=lp)

def zz8(q):
    z = np.where(q >= 0, 2 * q, -2 * q - 1)
    return z

def layouts(b):
    q, codes, kind = b["q"], b["codes"], b["kind"]
    head = (bytes(codes.astype(np.uint8).ravel()) if codes is not None else b"")
    if kind == 0 and codes is None:
        return {k: b"\0" for k in ("cur", "lc8", "zz", "zz_lc8", "sig_split", "tr")}
    raw = q.astype(np.int8).tobytes() if kind == 1 else q.astype("<i2").tobytes()
    out = {"cur": gmotlib.band_bytes(b)}
    out["lc8"] = out["cur"]
    if kind == 1:
        z = zz8(q)
        zb = np.clip(z, 0, 255).astype(np.uint8)
        esc = z > 255
        zbytes = bytes([1]) + head + zb.tobytes()
        if esc.any():
            zbytes = None
        out["zz"] = zbytes or out["cur"]
        out["zz_lc8"] = out["zz"]
        nzmask = (q != 0)
        bits = np.packbits(nzmask.ravel()).tobytes()
        vals = zz8(q[nzmask]) - 1
        out["sig_split"] = head + bits + (np.clip(vals, 0, 255).astype(np.uint8).tobytes() if vals.max(initial=0) < 256 else q[nzmask].astype(np.int8).tobytes())
        # transposed for vertical bands (HL: vertical detail? LZMA sees rows; try columns for k==2)
        out["tr"] = (bytes([1]) + head + (zb.T.tobytes() if b["k"] == 1 else zb.tobytes()))
    else:
        for k in ("zz", "zz_lc8", "sig_split", "tr"):
            out[k] = out["cur"]
    return out

def one(path):
    g = gmotlib.parse(Path(path).read_bytes())
    chunks = {}
    for b in g["bands"]:
        chunks.setdefault(b["chunk"], []).append(b)
    plain0 = 9 + 4 * g["n"] + 4 + len(g["header"]) + 2
    res = {k: 0 for k in ("cur", "lc8", "zz", "zz_lc8", "sig_split", "tr")}
    for c, bl in chunks.items():
        lay = [layouts(b) for b in bl]
        for k in res:
            body = b"".join(l[k] for l in lay) + (bytes(plain0) if c == 0 else b"")
            lc = 4 if k in ("lc8", "zz_lc8") else 3
            res[k] += lz(body, lc=lc)
    res["file"] = len(Path(path).read_bytes())
    return res

if __name__ == "__main__":
    tag, n = sys.argv[1], int(sys.argv[2])
    blobs = sorted(p for p in (LAB / tag).rglob("data_static/gmot/*/*") if p.is_file())
    blobs = blobs[::max(1, len(blobs) // n)][:n]
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "8"))) as ex:
        rows = list(ex.map(one, map(str, blobs), chunksize=2))
    tot = {k: sum(r[k] for r in rows) for k in rows[0]}
    base = tot["cur"]
    print(tag, len(rows), {k: f"{100*(v/base-1):+.1f}%" for k, v in tot.items() if k != "file"})
