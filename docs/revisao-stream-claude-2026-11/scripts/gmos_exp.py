"""GMOS re-coding experiments (review/claude lab). Lossless ones decode to the same positions, normals, UVs
and strips (same image in game); the q* ones change the quantization (lossy, estimate).

python gmos_exp.py <tag> -> out/gmos_<tag>.json
Sizes per blob (bytes): file (as stored), fields alone (attribution), and variants, each one LZMA 9|extreme
stream like the product:
  base      : the product's streams re-packed (must equal the file minus 5 bytes: sanity)
  para      : new positions predicted by the parallelogram of the strip's previous triangle (else delta)
  para_uv   : para + the same prediction for UVs
  para_uv_n : para_uv + normals predicted from the faces around each vertex (engine 2-byte code of the
              area-weighted face normal), residual in code space
  mo        : meshoptimizer encode_index_sequence + encode_vertex_buffer (v0) of (pos i32x3, nrm 2B, pad, uv i32x2)
  q32 / q16 : base with positions at 1/32 / 1/16 unit (lossy)
  uv2k      : base with UVs at 1/2048 (lossy)
"""
import json
import lzma
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(1, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
import gmoslib  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")


def lz(b):
    return len(lzma.compress(bytes(b), format=lzma.FORMAT_ALONE,
                             filters=[{"id": lzma.FILTER_LZMA1, "preset": 9 | lzma.PRESET_EXTREME,
                                       "dict_size": 1 << 24}]))


def zz(a):
    a = np.asarray(a, np.int64)
    return ((a << 1) ^ (a >> 63)).astype(np.uint64)


def planes(v):
    v = np.asarray(v, np.uint64)
    m = int(v.max()) if len(v) else 0
    nb = 1 if m < 1 << 8 else 2 if m < 1 << 16 else 3 if m < 1 << 24 else 4
    return struct.pack("<B", nb) + b"".join(((v >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8).tobytes()
                                            for k in range(nb))


def refs(A):
    last, out = {}, np.zeros(len(A), np.int64)
    for i, row in enumerate(map(tuple, A.tolist())):
        j = last.get(row)
        out[i] = 0 if j is None else i - j
        last[row] = i
    return out


def first_pos(code):
    """strip position where each vertex first appears (vertex i = i-th zero code)"""
    return np.flatnonzero(code == 0)


def para_residual(P, C, lens, newmask):
    """residual of the attribute rows P (per vertex, int) for the vertices with newmask (first of their
    value), predicted by the parallelogram of the three strip entries before the vertex's first appearance
    (same strip chunk, distinct), else by the previous new row (the product's delta)."""
    code_first = first_pos_from_C(C)
    nv = len(P)
    chunk_start = np.zeros(len(C), np.int64)
    o = 0
    for ln in lens:
        chunk_start[o:o + ln] = o
        o += ln
    c = code_first[:nv]
    ok = (c - 3 >= chunk_start[c])
    a = np.where(ok, C[np.maximum(c - 1, 0)], 0)
    b = np.where(ok, C[np.maximum(c - 2, 0)], 0)
    d = np.where(ok, C[np.maximum(c - 3, 0)], 0)
    ok &= (a != b) & (b != d) & (a != d)
    pred = P[a] + P[b] - P[d]
    idx = np.flatnonzero(newmask)
    prev = np.vstack([np.zeros((1, P.shape[1]), np.int64), P[idx[:-1]]])
    delta_pred = prev
    pr = np.where(ok[idx][:, None], pred[idx], delta_pred)
    return P[idx] - pr


def first_pos_from_C(C):
    # vertices are numbered by first appearance: the first time C reaches value i
    nv = int(C.max()) + 1 if len(C) else 0
    first = np.full(nv, len(C), np.int64)
    np.minimum.at(first, C, np.arange(len(C)))
    return first


def face_normal_codes(P, C, lens, posq):
    """engine 2-byte codes of each vertex's area-weighted face normal"""
    from vvd_engine_exact import pack_ubyte4
    t, _ = gmoslib.triangles(C, lens)
    X = P.astype(np.float64) * posq
    nv = len(P)
    acc = np.zeros((nv, 3))
    if len(t):
        fn = np.cross(X[t[:, 1]] - X[t[:, 0]], X[t[:, 2]] - X[t[:, 0]])
        for j in range(3):
            np.add.at(acc, t[:, j], fn)
    ln = np.linalg.norm(acc, axis=1)
    acc = np.where(ln[:, None] > 0, acc / np.maximum(ln, 1e-30)[:, None], np.array([0, 0, 1.0]))
    with np.errstate(invalid="ignore", divide="ignore"):
        cx, cy, _xb, _yb = pack_ubyte4(acc.astype(np.float32))
    cx = np.where(np.isfinite(cx), cx, 192).astype(np.int64)
    cy = np.where(np.isfinite(cy), cy, 192).astype(np.int64)
    return np.stack([cx, cy], 1)


def pack_variant(g, mode):
    """streams of all groups for a variant -> bytes"""
    S = {k: [] for k in ("pref", "p", "nref", "n", "uvref", "uv", "idx")}
    for gr in g["groups"]:
        P, N, U, C, lens = gr["P"], gr["N"], gr["U"], gr["C"], gr["lens"]
        if mode == "q32":
            P = np.round(P / 2.0).astype(np.int64)
        elif mode == "q16":
            P = np.round(P / 4.0).astype(np.int64)
        elif mode == "uv2k":
            U = np.round(U / 2.0).astype(np.int64)
        pr = refs(P) if mode in ("q32", "q16") else gr["pid"] * 0 + _ref_from_ids(gr["pid"])
        nr = _ref_from_ids(gr["nid"])
        ur = refs(U) if mode == "uv2k" else _ref_from_ids(gr["uid"])
        newp, newn, newu = pr == 0, nr == 0, ur == 0
        if mode in ("para", "para_uv", "para_uv_n"):
            pres = para_residual(P, C, lens, newp)
        else:
            idx = np.flatnonzero(newp)
            pres = np.diff(np.vstack([np.zeros((1, 3), np.int64), P[idx]]), axis=0)
        if mode in ("para_uv", "para_uv_n"):
            ures = para_residual(U, C, lens, newu)
        else:
            idx = np.flatnonzero(newu)
            ures = np.diff(np.vstack([np.zeros((1, 2), np.int64), U[idx]]), axis=0)
        if mode == "para_uv_n":
            pn = face_normal_codes(P, C, lens, g["posq"])
            idx = np.flatnonzero(newn)
            nres = N[idx] - pn[idx]
            S["n"].append(zz(nres.ravel()).reshape(-1, 2))
        else:
            S["n"].append(N[np.flatnonzero(newn)].astype(np.uint64))
        S["pref"].append(pr)
        S["nref"].append(nr)
        S["uvref"].append(ur)
        S["p"].append(zz(pres.ravel()).reshape(-1, 3))
        S["uv"].append(zz(ures.ravel()).reshape(-1, 2))
        S["idx"].append(gr["code"])
    cat = {k: (np.concatenate(v) if v else np.zeros((0,), np.int64)) for k, v in S.items()}
    out = bytearray(g["head"])
    out += planes(cat["pref"].astype(np.uint64))
    for j in range(3):
        out += planes(cat["p"][:, j] if len(cat["p"]) else [])
    out += planes(cat["nref"].astype(np.uint64))
    for j in range(2):
        out += planes(cat["n"][:, j] if len(cat["n"]) else [])
    out += planes(cat["uvref"].astype(np.uint64))
    for j in range(2):
        out += planes(cat["uv"][:, j] if len(cat["uv"]) else [])
    out += planes(cat["idx"].astype(np.uint64))
    return bytes(out)


def _ref_from_ids(ids):
    """back-reference code (0 new, k = same as k back) from value ids numbered in first-appearance order"""
    last = {}
    out = np.zeros(len(ids), np.int64)
    for i, v in enumerate(ids.tolist()):
        j = last.get(v)
        out[i] = 0 if j is None else i - j
        last[v] = i
    return out


def mo_variant(g):
    import meshoptimizer as mo
    parts = bytearray(g["head"])
    for gr in g["groups"]:
        P, N, U, C = gr["P"], gr["N"], gr["U"], gr["C"]
        vb = np.zeros(len(P), dtype=[("p", "<i4", 3), ("n", "u1", 2), ("pad", "u1", 2), ("u", "<i4", 2)])
        vb["p"], vb["n"], vb["u"] = P, N, U
        enc_v = mo.encode_vertex_buffer(vb.view(np.uint8).reshape(len(P), -1), len(P), vb.dtype.itemsize)
        enc_i = mo.encode_index_sequence(C.astype(np.uint32), len(C), len(P))
        parts += struct.pack("<II", len(enc_v), len(enc_i)) + bytes(enc_v) + bytes(enc_i)
    return bytes(parts)


def one(path):
    data = Path(path).read_bytes()
    g = gmoslib.parse(data)
    r = {"file": len(data), "verts": sum(gr["nv"] for gr in g["groups"]),
         "tris": sum(len(gmoslib.triangles(gr["C"], gr["lens"])[0]) for gr in g["groups"]),
         "strip": int(sum(len(gr["C"]) for gr in g["groups"]))}
    # attribution: header + each field alone
    blob = g["raw"]
    r["f_header"] = lz(g["head"])
    pos = len(g["head"])
    for f in gmoslib.FIELDS:
        nb = blob[pos]
        cnt = len(g["S"][f])
        r["f_" + f] = lz(blob[pos:pos + 1 + nb * cnt])
        r["raw_" + f] = 1 + nb * cnt
        pos += 1 + nb * cnt
    for mode in ("base", "para", "para_uv", "para_uv_n", "q32", "q16", "uv2k"):
        try:
            r[mode] = lz(pack_variant(g, mode)) + 5
        except Exception as e:  # noqa: BLE001
            r[mode] = None
            r["err_" + mode] = repr(e)[:200]
    try:
        r["mo"] = lz(mo_variant(g)) + 5
    except Exception as e:  # noqa: BLE001
        r["mo"] = None
        r["err_mo"] = repr(e)[:200]
    return r


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "cb"
    blobs = sorted(p for p in (LAB / tag).rglob("data_static/gmo/*") if p.is_file())
    if len(sys.argv) > 2:
        blobs = blobs[::max(1, len(blobs) // int(sys.argv[2]))]
    with ProcessPoolExecutor(int(__import__("os").environ.get("NPROC", "8"))) as ex:
        rows = list(ex.map(one, map(str, blobs), chunksize=2))
    keys = [k for k in rows[0] if isinstance(rows[0][k], (int, float))]
    tot = {k: sum((r.get(k) or 0) for r in rows) for k in keys}
    errs = [r for r in rows if any(k.startswith("err_") for k in r)]
    out = {"blobs": len(rows), "total": tot, "errors": len(errs), "rows": [dict(r, path=str(p.relative_to(LAB))) for r, p in zip(rows, blobs)]}
    (LAB / "out" / f"gmos_{tag.replace('/', '_')}.json").write_text(json.dumps(out, indent=1))
    MiB = 2 ** 20
    print(f"{len(rows)} blobs, errors {len(errs)}; file {tot['file'] / MiB:.2f} MiB; verts {tot['verts']:,} tris {tot['tris']:,} strip {tot['strip']:,}")
    print("  fields alone:", {k[2:]: round(v / MiB, 2) for k, v in tot.items() if k.startswith("f_")})
    print("  raw:", {k[4:]: round(v / MiB, 2) for k, v in tot.items() if k.startswith("raw_")})
    for k in ("base", "para", "para_uv", "para_uv_n", "mo", "q32", "q16", "uv2k"):
        print(f"  {k:10} {tot[k] / MiB:8.2f} MiB  {100 * (tot[k] / tot['file'] - 1):+.1f}%")
    if errs:
        print("  first error:", {k: v for k, v in errs[0].items() if k.startswith("err_")})


if __name__ == "__main__":
    main()
