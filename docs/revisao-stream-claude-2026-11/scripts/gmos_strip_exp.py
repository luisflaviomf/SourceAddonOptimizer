"""Strip order experiment (review/claude lab): the n largest streamed models encoded as now and with the triangles
reordered by meshoptimizer's optimizeVertexCacheStrip before stripify. Places (vertices the runtime emits) and
stream bytes. python gmos_strip_exp.py [n]"""
import hashlib
import lzma
import struct
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO))
LAB = Path(r"C:/GModLab/revisao/claude")
import os  # noqa: E402
import shutil  # noqa: E402
import meshoptimizer as mo  # noqa: E402
from model_screen.binary import flatten_lods  # noqa: E402
OUT = Path(os.environ["STRIPEXP_OUT"]) if os.environ.get("STRIPEXP_OUT") else None
from model_stream import encoder  # noqa: E402

GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def places(data: bytes) -> int:
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8
    blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    pos = 20
    (ntex,) = struct.unpack_from("<I", blob, pos); pos += 4
    for _ in range(ntex):
        pos = blob.index(b"\0", pos) + 1
    nfam, nref = struct.unpack_from("<II", blob, pos); pos += 8 + 2 * nfam * nref
    (ng,) = struct.unpack_from("<I", blob, pos); pos += 4
    tot = 0
    for _ in range(ng):
        b, m, slot, bone, flags, nv, nch = struct.unpack_from("<4HBIH", blob, pos); pos += struct.calcsize("<4HBIH")
        lens = struct.unpack_from(f"<{nch}I", blob, pos); pos += 4 * nch + 48
        tot += sum(lens)
    return tot


STOCK = encoder.stripify


def vcache_stripify(tris, nverts):
    idx = np.ascontiguousarray(tris, np.uint32).ravel()
    out = np.zeros_like(idx)
    mo.optimize_vertex_cache_strip(out, idx, len(idx), nverts)
    return STOCK(out.reshape(-1, 3), nverts)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    by_hash = {}
    for g in GROUPS:
        root = LAB / "src" / g / f"{g}_merged"
        for p in (root / "models").rglob("*.mdl"):
            rel = p.relative_to(root).as_posix()
            by_hash[hashlib.sha1(rel.lower().encode()).hexdigest()[:16]] = p
    files = []
    for g in GROUPS:
        files += list((LAB / "cb_gp" / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir())
    files.sort(key=lambda p: -p.stat().st_size)
    T = dict(shipped=0, stock=0, vc=0, p_stock=0, p_vc=0)
    for f in files[:n]:
        src = by_hash[f.stem]
        # the pipeline streams the model after flattening its LODs (model_screen.cli): a copy, flattened
        tmp = LAB / "work" / "stripexp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for q in src.parent.glob(src.stem + ".*"):
            shutil.copyfile(q, tmp / q.name)
        mdl = tmp / src.name
        flatten_lods(mdl)
        encoder.stripify = STOCK
        a = encoder.encode(mdl, None)
        encoder.stripify = vcache_stripify
        b = encoder.encode(mdl, None)
        encoder.stripify = STOCK
        pa, pb = places(a), places(b)
        if OUT:
            for tag, data in (("stock", a), ("vc", b)):
                q = OUT / tag / f.name
                q.parent.mkdir(parents=True, exist_ok=True)
                q.write_bytes(data)
        T["shipped"] += f.stat().st_size
        T["stock"] += len(a); T["vc"] += len(b); T["p_stock"] += pa; T["p_vc"] += pb
        print(f"{mdl.name:36} shipped {f.stat().st_size:9d} bytes {len(a):9d} -> {len(b):9d} ({100 * (len(b) / len(a) - 1):+5.1f}%)  "
              f"places {pa:8d} -> {pb:8d} ({100 * (pb / pa - 1):+5.1f}%)", flush=True)
    print(T)
    print(f"bytes {100 * (T['vc'] / T['stock'] - 1):+.2f}%  places {100 * (T['p_vc'] / T['p_stock'] - 1):+.2f}%")


if __name__ == "__main__":
    main()
