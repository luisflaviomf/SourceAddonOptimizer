"""Real (not estimated) size of the GMOT bands re-coded by the lab context coder (rust/gmotcm), every chunk
checked by decoding it back. python cm_real.py <tag> [sample] [cfg...]  -> out/cmreal_<tag>_<cfg>.json

cfg = pbits,shift,tiles,sign_ctx (default 12,4,1,1). Blob = container (6 + 4 nch) + chunk 0 (its plain header:
w,h,L,n,mode,cdrop,P, steps, VTF header, out_fmt, mips, band kinds, then the coded bands) + coded fine chunks.
"""
import ctypes
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import gmotlib  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
DLL = LAB / "rust" / "gmotcm" / "target" / "release" / "gmotcm.dll"
_lib = None


def lib():
    global _lib
    if _lib is None:
        _lib = ctypes.CDLL(str(DLL))
        _lib.cm_chunk.restype = ctypes.c_int
        V = ctypes.c_void_p
        _lib.cm_chunk.argtypes = [V, ctypes.c_int, V, ctypes.c_size_t, V, ctypes.c_size_t, V, ctypes.POINTER(ctypes.c_size_t)]
    return _lib


def code_chunk(bands, cfg):
    meta = np.array([[b["q"].shape[0], b["q"].shape[1], b["class"], 1 if b["k"] == 0 else 0,
                      1 if b["codes"] is not None else 0, -1] for b in bands], np.int32)
    q = np.ascontiguousarray(np.concatenate([b["q"].ravel() for b in bands]).astype(np.int32))
    cl = [b["codes"].ravel() for b in bands if b["codes"] is not None]
    codes = np.ascontiguousarray(np.concatenate(cl).astype(np.uint8)) if cl else np.zeros(1, np.uint8)
    c = np.array(cfg, np.int32)
    size = ctypes.c_size_t(0)
    rc = lib().cm_chunk(meta.ctypes.data, len(bands), q.ctypes.data, q.size, codes.ctypes.data,
                        int(sum(x.size for x in cl)), c.ctypes.data, ctypes.byref(size))
    if rc != 0:
        raise RuntimeError(f"round trip failed {rc}")
    return size.value


def one(args):
    path, cfg = args
    data = Path(path).read_bytes()
    g = gmotlib.parse(data)
    chunks = {}
    for b in g["bands"]:
        chunks.setdefault(b["chunk"], []).append(b)
    plain0 = 9 + 4 * g["n"] + 4 + len(g["header"]) + 2
    sizes = []
    for c in sorted(chunks):
        bl = chunks[c]
        # an all-zero band without a noise map is just its kind byte
        coded = [b for b in bl if not (b["kind"] == 0 and b["codes"] is None)]
        for b in coded:
            b["class"] = (min(b["plane"], 1) * 4 + b["k"]) if c == 0 else 0
        n = len(bl)  # one kind byte per band
        sizes.append(n + (code_chunk(coded, cfg) if coded else 0) + (plain0 if c == 0 else 0))
    new = 6 + 4 * len(sizes) + sum(sizes)
    return {"file": len(data), "new": new, "lzma0": g["sizes"][0], "new0": sizes[0],
            "fine_lzma": sum(g["sizes"][1:]), "fine_new": sum(sizes[1:]), "mode": g["mode"], "n": g["n"],
            "side": max(g["w"], g["h"]), "path": str(Path(path).relative_to(LAB))}


def main():
    tag = sys.argv[1]
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    cfg = [int(x) for x in (sys.argv[3].split(",") if len(sys.argv) > 3 else "12,4,1,1".split(","))] + [0]
    blobs = sorted(p for p in (LAB / tag).rglob("data_static/gmot/*/*") if p.is_file())
    if sample:
        blobs = blobs[::max(1, len(blobs) // sample)][:sample]
    with ProcessPoolExecutor(int(__import__("os").environ.get("NPROC", "8"))) as ex:
        rows = list(ex.map(one, [(str(p), cfg) for p in blobs], chunksize=4))
    tot = {k: sum(r[k] for r in rows) for k in ("file", "new", "lzma0", "new0", "fine_lzma", "fine_new")}
    name = f"cmreal_{tag.replace('/', '_')}_{'-'.join(map(str, cfg[:4]))}{'_' + str(sample) if sample else ''}"
    (LAB / "out" / f"{name}.json").write_text(json.dumps({"cfg": cfg, "total": tot, "rows": rows}, indent=1))
    MiB = 2 ** 20
    print(f"{name}: {len(rows)} blobs, file {tot['file'] / MiB:.2f} -> {tot['new'] / MiB:.2f} MiB "
          f"({100 * (tot['new'] / tot['file'] - 1):+.1f}%); chunk0 {tot['lzma0'] / MiB:.2f} -> {tot['new0'] / MiB:.2f}; "
          f"fine {tot['fine_lzma'] / MiB:.2f} -> {tot['fine_new'] / MiB:.2f}")


if __name__ == "__main__":
    main()
