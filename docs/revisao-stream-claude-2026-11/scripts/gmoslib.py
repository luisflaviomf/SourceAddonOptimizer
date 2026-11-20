"""Lab helpers (review/claude): read GMOS v6 into integer arrays per group (vectorised; equal to
model_stream.encoder.decode, checked by check())."""
import lzma
import struct

import numpy as np

FIELDS = ("pref", "px", "py", "pz", "nref", "ncx", "ncy", "uvref", "u", "v", "idx")


def unpack(data):
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8
    return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)


def _resolve(refs):
    """refs[i] = 0: new value (next counter), k: same as element i - k. -> id per element."""
    n = len(refs)
    new = refs == 0
    ids = np.full(n, -1, np.int64)
    ids[new] = np.arange(int(new.sum()))
    src = np.arange(n) - refs
    src[new] = np.arange(n)[new]
    while True:
        todo = ids < 0
        if not todo.any():
            return ids
        ids[todo] = ids[src[todo]]
        src[todo] = src[src[todo]]


def parse(data):
    blob = unpack(data)
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
        mats.append(blob[pos:e])
        pos = e + 1
    nfam, nref = take("<II")
    skins = [take(f"<{nref}H") for _ in range(nfam)]
    (ng,) = take("<I")
    groups = []
    for _ in range(ng):
        b, m, slot, bone, flags, nv, nch = take("<4HBIH")
        lens = list(take(f"<{nch}I"))
        ptb = take("<12f")
        groups.append(dict(b=b, m=m, slot=slot, bone=bone, flags=flags, nv=nv, lens=lens, ptb=ptb))
    head_end = pos
    np_, nn_, nu_ = take("<3I")
    nv_tot = sum(g["nv"] for g in groups)
    ni_tot = sum(sum(g["lens"]) for g in groups)
    counts = dict(pref=nv_tot, px=np_, py=np_, pz=np_, nref=nv_tot, ncx=nn_, ncy=nn_, uvref=nv_tot, u=nu_, v=nu_,
                  idx=ni_tot)
    S, nbs = {}, {}
    for f in FIELDS:
        (nb,) = take("<B")
        cnt = counts[f]
        a = np.zeros(cnt, np.int64)
        for k in range(nb):
            a |= np.frombuffer(blob, np.uint8, cnt, pos + k * cnt).astype(np.int64) << (8 * k)
        pos += nb * cnt
        if f in ("px", "py", "pz", "u", "v"):
            a = np.where(a % 2 == 0, a // 2, -(a + 1) // 2)
        S[f], nbs[f] = a, nb
    assert pos == len(blob)
    # per group arrays
    vb = ib = cp = cn = cu = 0
    out = []
    for g in groups:
        nv, n3 = g["nv"], sum(g["lens"])
        pr, nr, ur = S["pref"][vb:vb + nv], S["nref"][vb:vb + nv], S["uvref"][vb:vb + nv]
        pid, nid, uid = _resolve(pr), _resolve(nr), _resolve(ur)
        kp, kn, ku = int((pr == 0).sum()), int((nr == 0).sum()), int((ur == 0).sum())
        P = np.cumsum(np.stack([S["px"][cp:cp + kp], S["py"][cp:cp + kp], S["pz"][cp:cp + kp]], 1), 0)
        N = np.stack([S["ncx"][cn:cn + kn], S["ncy"][cn:cn + kn]], 1)
        U = np.cumsum(np.stack([S["u"][cu:cu + ku], S["v"][cu:cu + ku]], 1), 0)
        code = S["idx"][ib:ib + n3]
        new = code == 0
        hwm = np.cumsum(new) - 1                      # hwm after this element
        C = np.where(new, hwm, hwm + 1 - code)        # code>0: hwm (before) + 1 - code; hwm unchanged
        out.append(dict(g, P=P[pid], N=N[nid], U=U[uid], pid=pid, nid=nid, uid=uid, C=C, code=code,
                        Pnew=P, Nnew=N, Unew=U))
        vb += nv
        ib += n3
        cp += kp
        cn += kn
        cu += ku
    return dict(illum=illum, posq=posq, uvq=uvq, mats=mats, skins=skins, groups=out, head=blob[:head_end + 12],
                raw=blob, S=S, nbs=nbs)


def triangles(C, lens):
    """strip chunks -> (T,3) triangles (same winding as the runtime) and, per triangle, the strip position of
    its last vertex"""
    tris, at = [], []
    o = 0
    for ln in lens:
        s = C[o:o + ln]
        if ln >= 3:
            a, b, c = s[:-2], s[1:-1], s[2:]
            k = np.arange(ln - 2)
            ok = (a != b) & (b != c) & (a != c)
            t = np.where((k % 2 == 0)[:, None], np.stack([a, b, c], 1), np.stack([b, a, c], 1))
            tris.append(t[ok])
            at.append(o + 2 + k[ok])
        o += ln
    if not tris:
        return np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    return np.concatenate(tris), np.concatenate(at)


def check(path):
    """compare with the product's reference decoder"""
    import sys
    sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
    from model_stream.encoder import decode
    data = open(path, "rb").read()
    ref = decode(data)
    mine = parse(data)
    for gr, gm in zip(ref["groups"], mine["groups"]):
        assert np.allclose(gr["pos"], gm["P"] * mine["posq"]), "pos"
        assert (gr["ncodes"] == gm["N"]).all(), "normal"
        assert np.allclose(gr["uv"], gm["U"] * mine["uvq"]), "uv"
        assert (gr["strip"] == gm["C"]).all(), "strip"
        t, _ = triangles(gm["C"], gm["lens"])
        assert (gr["tris"] == t).all(), "tris"
    return True
