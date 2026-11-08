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

VERSION = 6                       # = encoder.VERSION (checked by tests)
_MANIFEST_START = "util.JSONToTable([["


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
    if data[:4] != b"GMOS" or data[4] != VERSION:
        raise ValueError("not a GMOS v%d stream" % VERSION)
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8
    blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
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
        P, cp = resolve(pref[vb:vb + nv], (px, py, pz), cp)
        U, cu = resolve(uvref[vb:vb + nv], (uu, vv), cu)
        lens = g["lens"]
        n3 = int(lens.sum())
        codes = idx[ib:ib + n3]
        z = np.cumsum(codes == 0)
        C = np.where(codes == 0, z - 1, z - codes)
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
