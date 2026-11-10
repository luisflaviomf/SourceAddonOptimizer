"""GMOS v6: compressed model geometry drawn by the Lua runtime (runtime.lua).

File = b"GMOS" + u8 version (6) + LZMA-alone blob in the format Garry's Mod's util.Decompress reads
(5 property bytes, u64 uncompressed size, LZMA data).
Blob (little endian):
  3 x f32 illumposition (lighting origin in model space), f32 POS_Q, f32 UV_Q
  u32 ntex, ntex x zstring material path (as studiomdl resolves cdmaterials + texture)
  u32 nfam, u32 nref, nfam*nref x u16 skin table (texture index per skin family and ref slot)
  u32 ngroups; per group: u16 bodypart, u16 choice, u16 slot (skin ref), u16 bone, u8 flags,
                           u32 nverts, u16 nchunks, nchunks x u32 strip length, 12 x f32 poseToBone(bone)
     flags bit 0: rebuild tangents (the source model had them)
  u32 n_newpos, u32 n_newnrm, u32 n_newuv
  streams (each = u8 nbytes then nbytes byte planes, low plane first; groups concatenated):
    pref (nverts): 0 = new position, k = same position as the vertex k places back (same group)
    px py pz (n_newpos): zigzag(delta of round(pos / POS_Q)) from the previous new position of the group
    nref, ncx ncy  : the same scheme for normals: the engine's own PackNormal_UBYTE4 bytes (no delta),
                     so the GPU receives exactly the vectors it receives for the compiled model
    uvref, u v     : the same scheme for UVs (zigzag delta of round(uv / UV_Q))
    idx (strip indices of every chunk): high-water mark code: 0 = next new vertex, k > 0 = vertex hwm+1-k
A group is one (bodypart, choice, mesh, bone): its triangles become triangle strips (degenerate joins,
meshoptimizer) of at most CHUNK_TRIS triangles, one IMesh each. Tangents are rebuilt by the runtime
(per-triangle UV derivatives summed per vertex, Gram-Schmidt, handedness from the bitangent, then the
same 2-byte packing the engine applies).
"""
from __future__ import annotations

import ctypes
import lzma
import struct
import sys
from pathlib import Path

import numpy as np

from model_screen.binary import mesh_arrays
from model_screen.mirror import read_bones
from texlab.source_model import _read_mdl

VERSION = 6
CHUNK_TRIS = 20000
MAX_STRIP = 65534       # indices of one strip chunk: a game mesh takes 65535 vertices (runtime.lua MAX_STRIP)
POS_Q = 1.0 / 64.0      # 0.016 units: at most 0.15 mm off
UV_Q = 1.0 / 4096.0     # at most 1/4 texel of a 2048 texture
WEIGHTED_LIMIT = 0.05   # models with more weighted (multi-bone) vertices are not streamed


def _zz(a) -> np.ndarray:
    a = np.asarray(a, np.int64)
    return ((a << 1) ^ (a >> 63)).astype(np.uint64)


def _planes(v) -> bytes:
    v = np.asarray(v, np.uint64)
    m = int(v.max()) if len(v) else 0
    nb = 1 if m < 1 << 8 else 2 if m < 1 << 16 else 3 if m < 1 << 24 else 4
    return struct.pack("<B", nb) + b"".join(((v >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8).tobytes()
                                            for k in range(nb))


def vvd_records(mdl: Path):
    """(floats (n,8) pos/normal/uv, has tangents, dominant bone per record, bone count, weights)."""
    raw = mdl.with_suffix(".vvd").read_bytes()
    n = int(np.frombuffer(raw, "<i4", 1, 16)[0])
    vstart = int(np.frombuffer(raw, "<i4", 1, 56)[0])
    tstart = int(np.frombuffer(raw, "<i4", 1, 60)[0])
    V = np.frombuffer(raw, np.uint8, n * 48, vstart).reshape(n, 48)
    f = V[:, 16:48].copy().view(np.float32).reshape(n, 8).astype(np.float64)
    w = V[:, 0:12].copy().view(np.float32).reshape(n, 3)
    b3 = V[:, 12:15].astype(np.int64)
    nb = V[:, 15].astype(np.int64)
    wm = np.where(np.arange(3)[None, :] < nb[:, None], w, -1.0)
    dominant = b3[np.arange(n), wm.argmax(1)]          # weighted vertices follow their main bone
    return f, bool(tstart), dominant, nb, w


def eligible(mdl: Path) -> tuple[bool, str]:
    """Rigid models only: IMeshes have no skinning, each group follows one bone."""
    _f, _t, _bone, nb, w = vvd_records(mdl)
    if not len(nb):
        return False, "no vertices"
    weighted = (nb > 1) & (w[:, 1] > 0)
    if weighted.mean() > WEIGHTED_LIMIT:
        return False, "weighted skinning"
    return True, ""


def material_paths(mdl: Path, resolver) -> list[str]:
    """Material of each texture slot. When none of the cdmaterials candidates exists in the addon or its
    neighbours (shared texture packs live in other Workshop items), every candidate is kept in the
    engine's order, joined by "|": the runtime takes the first one the game has, like the engine does."""
    _v, _n, _c, textures, cdmats, _sk, _bp = _read_mdl(mdl)
    out = []
    for tex in textures:
        chosen = None
        cands = []
        for cd in cdmats:
            p = (cd.strip("/\\") + "/" + tex).replace("\\", "/").lstrip("/")
            cands.append(p)
            if resolver is None or resolver.material(p.lower()):
                chosen = p
                break
        out.append(chosen or "|".join(dict.fromkeys(cands)) or tex.replace("\\", "/"))
    return out


_strip_dll = None


def _strip_dll_path() -> Path:
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        roots.append(Path(sys.executable).resolve().parent)
    repo = Path(__file__).resolve().parents[1]
    roots += [repo / "tools" / "vtx", repo]
    for root in roots:
        for p in (root / "meshopt_stripify.dll", root / "tools" / "vtx" / "meshopt_stripify.dll"):
            if p.is_file():
                return p
    raise FileNotFoundError("meshopt_stripify.dll not found (tools/vtx)")


def stripify(tris: np.ndarray, nverts: int) -> np.ndarray:
    """meshoptimizer triangle strip (degenerate joins, same winding rules as the GPU)."""
    global _strip_dll
    if _strip_dll is None:
        lib = ctypes.CDLL(str(_strip_dll_path()))
        u32p = ctypes.POINTER(ctypes.c_uint)
        lib.probe_stripify_bound.restype = ctypes.c_size_t
        lib.probe_stripify_bound.argtypes = [ctypes.c_size_t]
        lib.probe_stripify.restype = ctypes.c_size_t
        lib.probe_stripify.argtypes = [u32p, u32p, ctypes.c_size_t, ctypes.c_size_t]
        _strip_dll = lib
    idx = np.ascontiguousarray(tris, np.uint32).ravel()
    out = np.zeros(_strip_dll.probe_stripify_bound(len(idx)), np.uint32)
    n = _strip_dll.probe_stripify(out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint)),
                                  idx.ctypes.data_as(ctypes.POINTER(ctypes.c_uint)), len(idx), nverts)
    return out[:n].astype(np.int64)


def strips_for(tris: np.ndarray, nverts: int) -> list:
    """Triangle strips of a group, each at most MAX_STRIP indices (one game mesh). The group is cut in
    chunks of CHUNK_TRIS triangles; a chunk of poorly connected triangles strips into up to 5 indices per
    triangle, so one that comes out too long is halved until it fits."""
    def fit(part):
        strip = stripify(part, nverts)
        if len(strip) <= MAX_STRIP or len(part) <= 1:
            return [strip]
        half = len(part) // 2
        return fit(part[:half]) + fit(part[half:])
    out = []
    for c in range(0, len(tris), CHUNK_TRIS):
        out += fit(tris[c:c + CHUNK_TRIS])
    return out


def _refs(A: np.ndarray, delta: bool):
    """back-reference coding of rows of A (int64): (refs, new rows coded)"""
    last, refs, new = {}, [], []
    prev = np.zeros(A.shape[1], np.int64)
    for i, row in enumerate(map(tuple, A.tolist())):
        j = last.get(row)
        if j is not None:
            refs.append(i - j)
        else:
            refs.append(0)
            r = np.array(row, np.int64)
            new.append(r - prev if delta else r)
            if delta:
                prev = r
        last[row] = i
    return np.array(refs, np.int64), (np.array(new, np.int64).reshape(-1, A.shape[1]))


def encode(mdl: Path, resolver, meshes_tris: dict | None = None) -> bytes:
    """GMOS bytes of a compiled model. meshes_tris: optional {mesh key: (T,3) mesh-local record ids}
    (e.g. a simplified set); default = the model's own LOD0 triangles."""
    from vvd_engine_exact import pack_ubyte4
    f, has_tan, bone, _nb, _w = vvd_records(mdl)
    _v, _n, _c, _textures, _cd, skins, bodyparts = _read_mdl(mdl)
    raw_mdl = mdl.read_bytes()
    bones = read_bones(raw_mdl)
    paths = material_paths(mdl, resolver)
    slots = [mat for _b, models in bodyparts for _m, _nv, _vi, ms in models for mat, _x, _y in ms]
    meshes = mesh_arrays(mdl)
    S = {k: [] for k in ("pref", "p", "nref", "n", "uvref", "uv", "idx")}
    groups = []
    for idx, (key, st, _cnt, t) in enumerate(meshes):
        if meshes_tris is not None:
            t = meshes_tris.get(key, np.zeros((0, 3), np.int64))
        if not len(t):
            continue
        T = np.asarray(t, np.int64) + st
        tb = bone[T[:, 0]]
        for bb in np.unique(tb):
            tri = T[tb == bb]
            ug = np.unique(tri)
            lt3 = np.searchsorted(ug, tri)
            strips = strips_for(lt3, len(ug))
            seq = np.concatenate(strips)
            uniq, first = np.unique(seq, return_index=True)
            order_local = uniq[np.argsort(first)]
            order = ug[order_local]
            remap = np.zeros(len(ug), np.int64)
            remap[order_local] = np.arange(len(order_local))
            lt = remap[seq]
            P = np.round(f[order, 0:3] / POS_Q).astype(np.int64)
            with np.errstate(invalid="ignore", divide="ignore"):
                cx, cy, _xb, _yb = pack_ubyte4(f[order, 3:6].astype(np.float32))
            N = np.stack([cx, cy], 1)
            N = np.where(np.isfinite(N), N, 192).astype(np.int64)   # zero-length normal -> (0, 0, 1)
            UV = np.round(f[order, 6:8] / UV_Q).astype(np.int64)
            for name, A, delta in (("p", P, True), ("n", N, False), ("uv", UV, True)):
                r, new = _refs(A, delta)
                S[name + "ref"].append(r)
                S[name].append(new)
            hwm = -1
            code = np.empty(len(lt), np.int64)
            for i, x in enumerate(lt.tolist()):
                if x == hwm + 1:
                    code[i] = 0
                    hwm += 1
                else:
                    code[i] = hwm + 1 - x
            S["idx"].append(code)
            b, m, _k = key
            groups.append((b, m, slots[idx], int(bb), 1 if has_tan else 0, len(order), [len(x) for x in strips],
                           bones[int(bb)]["ptb"]))
    blob = bytearray(raw_mdl[92:104])                # illumposition (lighting origin, model space)
    blob += struct.pack("<2f", POS_Q, UV_Q)
    blob += struct.pack("<I", len(paths))
    for p in paths:
        blob += p.encode("latin-1") + b"\0"
    nfam, nref = len(skins), (len(skins[0]) if skins else 0)
    blob += struct.pack("<II", nfam, nref)
    for fam in skins:
        blob += struct.pack(f"<{nref}H", *fam)
    blob += struct.pack("<I", len(groups))
    for b, m, slot, bb, fl, nv, chunk_lens, ptb in groups:
        blob += struct.pack("<4HBIH", b, m, slot, bb, fl, nv, len(chunk_lens))
        blob += struct.pack(f"<{len(chunk_lens)}I", *chunk_lens)
        blob += np.asarray(ptb, np.float32).ravel().tobytes()
    cat = {k: (np.concatenate(v) if v else np.zeros((0,), np.int64)) for k, v in S.items()}
    blob += struct.pack("<3I", len(cat["p"]), len(cat["n"]), len(cat["uv"]))
    blob += _planes(cat["pref"].astype(np.uint64))
    for j in range(3):
        blob += _planes(_zz(cat["p"][:, j]) if len(cat["p"]) else [])
    blob += _planes(cat["nref"].astype(np.uint64))
    for j in range(2):
        blob += _planes(cat["n"][:, j].astype(np.uint64) if len(cat["n"]) else [])
    blob += _planes(cat["uvref"].astype(np.uint64))
    for j in range(2):
        blob += _planes(_zz(cat["uv"][:, j]) if len(cat["uv"]) else [])
    blob += _planes(cat["idx"].astype(np.uint64))
    comp = bytearray(lzma.compress(bytes(blob), format=lzma.FORMAT_ALONE,
                                   filters=[{"id": lzma.FILTER_LZMA1, "preset": 9 | lzma.PRESET_EXTREME,
                                             "dict_size": 1 << 24}]))
    comp[5:13] = struct.pack("<Q", len(blob))       # known size: util.Decompress allocates from it
    return b"GMOS" + bytes([VERSION]) + bytes(comp)


# ----------------------------------------------------------------------------- reference decoder
# Mirrors runtime.lua step by step (used by the tests to prove encoder and runtime agree).

def unpack_ubyte4(cx: float, cy: float):
    zs = 1 if cx - 128 < 0 else 0
    ts = 1 if cy - 128 < 0 else 0
    ax, ay = abs(cx - 128) - zs, abs(cy - 128) - ts
    xs = 1 if ax - 64 < 0 else 0
    ys = 1 if ay - 64 < 0 else 0
    x, y = (abs(ax - 64) - xs) / 63, (abs(ay - 64) - ys) / 63
    z = 1 - x - y
    ln = (x * x + y * y + z * z) ** 0.5
    x, y, z = x / ln, y / ln, z / ln
    return (-x if xs else x), (-y if ys else y), (-z if zs else z), (-1 if ts else 1)


def decode(data: bytes) -> dict:
    if data[:4] != b"GMOS" or data[4] != VERSION:
        raise ValueError("not a GMOS v%d stream" % VERSION)
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8                          # Python's decoder wants "unknown size" + end mark
    blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    pos = 0

    def take(fmt):
        nonlocal pos
        v = struct.unpack_from(fmt, blob, pos)
        pos += struct.calcsize(fmt)
        return v

    illum = take("<3f")
    posq, uvq = take("<2f")
    (ntex,) = take("<I")
    mats = []
    for _ in range(ntex):
        e = blob.index(b"\0", pos)
        mats.append(blob[pos:e].decode("latin-1"))
        pos = e + 1
    nfam, nref = take("<II")
    skins = [list(take(f"<{nref}H")) for _ in range(nfam)]
    (ng,) = take("<I")
    groups = []
    for _ in range(ng):
        b, m, slot, bone, flags, nv, nch = take("<4HBIH")
        lens = list(take(f"<{nch}I"))
        ptb = take("<12f")
        groups.append(dict(b=b, m=m, slot=slot, bone=bone, flags=flags, nv=nv, lens=lens, ptb=ptb))
    np_, nn_, nu_ = take("<3I")
    nv_tot = sum(g["nv"] for g in groups)
    ni_tot = sum(sum(g["lens"]) for g in groups)

    def stream(count, signed=False):
        nonlocal pos
        (nb,) = take("<B")
        a = np.zeros(count, np.int64)
        for k in range(nb):
            a |= np.frombuffer(blob, np.uint8, count, pos + k * count).astype(np.int64) << (8 * k)
        pos += nb * count
        if signed:
            a = np.where(a % 2 == 0, a // 2, -(a + 1) // 2)
        return a

    pref, px, py, pz = stream(nv_tot), stream(np_, True), stream(np_, True), stream(np_, True)
    nref_, ncx, ncy = stream(nv_tot), stream(nn_), stream(nn_)
    uvref, uu, vv = stream(nv_tot), stream(nu_, True), stream(nu_, True)
    idx = stream(ni_tot)
    vb = ib = cp = cn = cu = 0
    for g in groups:
        nv = g["nv"]
        X = np.zeros((nv, 3)); Nn = np.zeros((nv, 3)); U = np.zeros((nv, 2)); codes = np.zeros((nv, 2), np.int64)
        x = y = z = u = v = 0
        for i in range(nv):
            r = pref[vb + i]
            if r == 0:
                x += px[cp]; y += py[cp]; z += pz[cp]; cp += 1
                X[i] = (x * posq, y * posq, z * posq)
            else:
                X[i] = X[i - r]
            r = nref_[vb + i]
            if r == 0:
                codes[i] = (ncx[cn], ncy[cn]); cn += 1
                Nn[i] = unpack_ubyte4(int(codes[i, 0]), int(codes[i, 1]))[:3]
            else:
                Nn[i] = Nn[i - r]; codes[i] = codes[i - r]
            r = uvref[vb + i]
            if r == 0:
                u += uu[cu]; v += vv[cu]; cu += 1
                U[i] = (u * uvq, v * uvq)
            else:
                U[i] = U[i - r]
        n3 = sum(g["lens"])
        C = np.zeros(n3, np.int64)
        hwm = -1
        for c in range(n3):
            code = idx[ib + c]
            if code == 0:
                hwm += 1
                C[c] = hwm
            else:
                C[c] = hwm + 1 - code
        tris, o = [], 0
        for ln in g["lens"]:
            for k in range(ln - 2):
                a, b2, c2 = C[o + k], C[o + k + 1], C[o + k + 2]
                if a != b2 and b2 != c2 and a != c2:
                    tris.append((a, b2, c2) if k % 2 == 0 else (b2, a, c2))
            o += ln
        g.update(pos=X, normal=Nn, uv=U, ncodes=codes, strip=C, tris=np.array(tris, np.int64).reshape(-1, 3))
        vb += nv
        ib += n3
    return dict(illum=illum, posq=posq, uvq=uvq, mats=mats, skins=skins, groups=groups)
