"""Read the geometry of streamed models back (numpy only, no model_screen/texlab imports).

A streamed model's .mdl only holds the coarse stand-in mesh; what players see up close is the GMOS
stream. Tools that measure the model geometry (the texscreen texel density, for one) read it here.
`decode_geometry` gives the same positions, UVs and triangles as the reference `encoder.decode`,
vectorised (the reference loops per vertex in Python).
"""
from __future__ import annotations

import json
import lzma
import struct
from pathlib import Path

import numpy as np

VERSION = 8                       # = encoder.VERSION (checked by tests)
VERSIONS = (6, 7, 8)              # = encoder.VERSIONS
_MANIFEST_START = "util.JSONToTable([["


def _lzma_alone(part: bytes) -> bytes:
    d = bytearray(part)
    d[5:13] = b"\xff" * 8                          # Python's decoder wants "unknown size" + end mark
    return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)


def stream_blob(data: bytes) -> tuple[int, bytes]:
    """(format, decompressed blob) of a GMOS file. Formats 6 and 7: one LZMA-alone blob after the 5-byte
    header. Format 8: the same blob cut in parts (u8 count, then per part u32 size + LZMA-alone), each
    decompressed on its own and joined (encoder module docstring)."""
    if data[:4] != b"GMOS" or data[4] not in VERSIONS:
        raise ValueError("not a GMOS v%s stream" % "/".join(map(str, VERSIONS)))
    ver = data[4]
    if ver != 8:
        return ver, _lzma_alone(data[5:])
    pos, parts = 6, []
    for _ in range(data[5]):
        (size,) = struct.unpack_from("<I", data, pos)
        parts.append(_lzma_alone(data[pos + 4:pos + 4 + size]))
        pos += 4 + size
    if pos != len(data):
        raise ValueError("GMOS v8: %d bytes after the last part" % (len(data) - pos))
    return ver, b"".join(parts)


def _para_places(C: np.ndarray, lens: np.ndarray, nv: int):
    """v7: per vertex, whether its new values are predicted by the parallelogram, and the three vertices
    (encoder module docstring, "Prediction")."""
    _ids, first = np.unique(C, return_index=True)
    start = np.repeat(np.concatenate([[0], np.cumsum(lens)[:-1]]), lens)
    cc = np.maximum(first, 3)
    a, b, d = C[cc - 1], C[cc - 2], C[cc - 3]
    ok = (first - 3 >= start[first]) & (a != b) & (b != d) & (a != d)
    return ok[:nv], a[:nv], b[:nv], d[:nv]


def _resolve_v7(refs, cols, start, places):
    """v7 values of a back-referenced field of one group: in vertex order (a prediction reads earlier vertices)."""
    ok, a, b, d = (x.tolist() for x in places)
    n = len(refs)
    k = int((refs == 0).sum())
    res = [c[start:start + k].tolist() for c in cols]
    refl = refs.tolist()
    if len(cols) == 3:
        rx, ry, rz = res
        X, Y, Z = [0] * n, [0] * n, [0] * n
        x = y = z = j = 0
        for i in range(n):
            r = refl[i]
            if r:
                X[i], Y[i], Z[i] = X[i - r], Y[i - r], Z[i - r]
                continue
            if ok[i]:
                p, q, s = a[i], b[i], d[i]
                x, y, z = X[p] + X[q] - X[s], Y[p] + Y[q] - Y[s], Z[p] + Z[q] - Z[s]
            x += rx[j]; y += ry[j]; z += rz[j]; j += 1
            X[i], Y[i], Z[i] = x, y, z
        return np.array([X, Y, Z], np.int64).T.reshape(n, 3), start + k
    ru, rv = res
    U, V = [0] * n, [0] * n
    u = v = j = 0
    for i in range(n):
        r = refl[i]
        if r:
            U[i], V[i] = U[i - r], V[i - r]
            continue
        if ok[i]:
            p, q, s = a[i], b[i], d[i]
            u, v = U[p] + U[q] - U[s], V[p] + V[q] - V[s]
        u += ru[j]; v += rv[j]; j += 1
        U[i], V[i] = u, v
    return np.array([U, V], np.int64).T.reshape(n, 2), start + k


def read_manifests(addon_root: Path) -> dict[str, Path]:
    """{model path (lower case, 'models/...mdl'): stream file} of every gmo_stream runtime in the addon."""
    addon_root = Path(addon_root)
    out: dict[str, Path] = {}
    lua_dir = addon_root / "lua" / "autorun" / "client"
    if not lua_dir.is_dir():
        return out
    for lua in sorted(lua_dir.glob("gmo_stream_*.lua")):
        try:
            text = lua.read_text(encoding="utf-8", errors="replace")
            a = text.index(_MANIFEST_START) + len(_MANIFEST_START)
            manifest = json.loads(text[a:text.index("]])", a)])
        except (OSError, ValueError):
            continue
        for model, stream in manifest.items():
            path = addon_root / stream
            if path.is_file():
                out[model.replace("\\", "/").lower()] = path
    return out


def decode_geometry(data: bytes) -> list[dict]:
    """Per group: b, m, slot (skin ref), bone, pos (V,3) float32, uv (V,2) float32, tris (T,3) int64."""
    ver, blob = stream_blob(data)
    pos = 0

    def take(fmt):
        nonlocal pos
        v = struct.unpack_from(fmt, blob, pos)
        pos += struct.calcsize(fmt)
        return v

    take("<3f")
    posq, uvq = take("<2f")
    (ntex,) = take("<I")
    for _ in range(ntex):
        pos = blob.index(b"\0", pos) + 1
    nfam, nref = take("<II")
    pos += 2 * nfam * nref
    (ng,) = take("<I")
    groups = []
    for _ in range(ng):
        b, m, slot, bone, _flags, nv, nch = take("<4HBIH")
        lens = np.array(take(f"<{nch}I"), np.int64)
        pos += 48
        groups.append(dict(b=b, m=m, slot=slot, bone=bone, nv=nv, lens=lens))
    np_, nn_, nu_ = take("<3I")
    nv_tot = sum(g["nv"] for g in groups)
    ni_tot = int(sum(int(g["lens"].sum()) for g in groups))

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
    stream(nv_tot), stream(nn_), stream(nn_)                       # normals: not needed here
    uvref, uu, vv = stream(nv_tot), stream(nu_, True), stream(nu_, True)
    idx = stream(ni_tot)

    def resolve(refs, cols, start):
        """Values of a back-referenced field of one group (new values are deltas from the previous one)."""
        n = len(refs)
        new = refs == 0
        k = int(new.sum())
        vals = np.cumsum(np.stack([c[start:start + k] for c in cols], 1), 0)
        src = np.where(new, np.arange(n), np.arange(n) - refs)
        while True:                                                   # follow reference chains
            nxt = src[src]
            if np.array_equal(nxt, src):
                break
            src = nxt
        rank = np.cumsum(new) - 1
        return vals[rank[src]], start + k

    out = []
    vb = ib = cp = cu = 0
    for g in groups:
        nv = g["nv"]
        lens = g["lens"]
        n3 = int(lens.sum())
        codes = idx[ib:ib + n3]
        z = np.cumsum(codes == 0)
        C = np.where(codes == 0, z - 1, z - codes)
        if ver >= 7:
            places = _para_places(C, lens, nv)
            P, cp = _resolve_v7(pref[vb:vb + nv], (px, py, pz), cp, places)
            U, cu = _resolve_v7(uvref[vb:vb + nv], (uu, vv), cu, places)
        else:
            P, cp = resolve(pref[vb:vb + nv], (px, py, pz), cp)
            U, cu = resolve(uvref[vb:vb + nv], (uu, vv), cu)
        offs = np.concatenate([[0], np.cumsum(lens)[:-1]])
        ks = [np.arange(max(0, int(ln) - 2)) for ln in lens]
        k = np.concatenate(ks) if ks else np.zeros(0, np.int64)
        base = np.concatenate([o + kk for o, kk in zip(offs, ks)]) if ks else np.zeros(0, np.int64)
        a, b2, c2 = C[base], C[base + 1], C[base + 2]
        keep = (a != b2) & (b2 != c2) & (a != c2)
        odd = (k % 2) == 1
        tris = np.stack([np.where(odd, b2, a), np.where(odd, a, b2), c2], 1)[keep]
        out.append(dict(b=g["b"], m=g["m"], slot=g["slot"], bone=g["bone"],
                        pos=(P * posq).astype(np.float32), uv=(U * uvq).astype(np.float32),
                        tris=tris.astype(np.int64)))
        vb += nv
        ib += n3
    return out
