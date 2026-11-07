"""Mirror instancing inside a compiled Source model (Models "Tela" mode, profile "strong_mirror").

The right side of a symmetric model is not stored. The left side's VTX vertices are drawn a second
time by an extra STRIP of the same strip group whose bone state changes point every hardware slot
at a "mirror bone": a child of the target bone with an identity local transform and
poseToBone = poseToBone(target) x Reflection. A left bind-pose vertex then lands exactly on its
right-side partner (position and normal); doors map LD -> RD. The VTX format says hardware-skinned
vertices take their bone from these slots, and the engine draws it so (checked in game, LVS cars).

append_bones() adds bones to an MDL v49: the bone array, the bone-by-name table, the linear bone
table and the sequence weight lists are rebuilt at the end of the file; nothing else moves.
"""
from __future__ import annotations

import struct

import numpy as np

from .binary import Strip, parse_vtx, write_vtx

BONE = 216
SEQ = 212


def _cstr(raw, off):
    return raw[off:raw.index(b"\0", off)].decode("latin-1")


def read_bones(raw: bytes) -> list[dict]:
    nb, bi = struct.unpack_from("<ii", raw, 156)
    out = []
    for i in range(nb):
        o = bi + i * BONE
        out.append(dict(name=_cstr(raw, o + struct.unpack_from("<i", raw, o)[0]),
                        parent=struct.unpack_from("<i", raw, o + 4)[0],
                        ptb=np.array(struct.unpack_from("<12f", raw, o + 96), np.float64).reshape(3, 4),
                        flags=struct.unpack_from("<i", raw, o + 160)[0]))
    return out


def _align(buf: bytearray, n=4):
    while len(buf) % n:
        buf.append(0)


def append_bones(raw: bytes, new: list[dict]) -> bytes:
    """new: [{name, parent, ptb (3x4), template}] appended after the existing bones."""
    raw = bytes(raw)
    out = bytearray(raw)
    nb, bi = struct.unpack_from("<ii", raw, 156)
    k = len(new)
    _align(out, 16)
    new_bi = len(out)
    out.extend(b"\0" * (BONE * (nb + k)))
    names_pos = []
    for nbn in new:
        names_pos.append(len(out))
        out.extend(nbn["name"].encode("latin-1") + b"\0")
    _align(out)
    for i in range(nb):                       # old bones move: fix their bone-relative offsets
        o_old, o_new = bi + i * BONE, new_bi + i * BONE
        rec = bytearray(raw[o_old:o_old + BONE])
        for field in (0, 168, 176):           # sznameindex, procindex, surfacepropidx
            v = struct.unpack_from("<i", rec, field)[0]
            if v:
                struct.pack_into("<i", rec, field, v + o_old - o_new)
        out[o_new:o_new + BONE] = rec
    for j, nbn in enumerate(new):
        t_old = bi + nbn["template"] * BONE
        o_new = new_bi + (nb + j) * BONE
        rec = bytearray(raw[t_old:t_old + BONE])
        struct.pack_into("<i", rec, 0, names_pos[j] - o_new)
        struct.pack_into("<i", rec, 4, nbn["parent"])
        struct.pack_into("<6i", rec, 8, *([-1] * 6))
        struct.pack_into("<3f", rec, 32, 0, 0, 0)                 # pos
        struct.pack_into("<4f", rec, 44, 0, 0, 0, 1)              # quat
        struct.pack_into("<3f", rec, 60, 0, 0, 0)                 # rot
        struct.pack_into("<12f", rec, 96, *np.asarray(nbn["ptb"], np.float64).ravel().tolist())
        struct.pack_into("<4f", rec, 144, 0, 0, 0, 1)             # qAlignment
        struct.pack_into("<ii", rec, 164, 0, 0)                   # proctype, procindex
        sp = struct.unpack_from("<i", rec, 176)[0]
        if sp:
            struct.pack_into("<i", rec, 176, sp + t_old - o_new)
        out[o_new:o_new + BONE] = rec
    struct.pack_into("<ii", out, 156, nb + k, new_bi)
    bones = read_bones(bytes(out))
    order = sorted(range(nb + k), key=lambda i: bones[i]["name"].lower())
    tbl = len(out)
    out.extend(bytes(order))
    _align(out)
    struct.pack_into("<i", out, 364, tbl)
    h2 = struct.unpack_from("<i", raw, 400)[0]
    if h2 and struct.unpack_from("<i", raw, h2 + 16)[0]:
        n = nb + k
        _align(out, 16)
        lb = len(out)
        out.extend(b"\0" * 64)
        rel = {}
        for name, field, size in (("flags", 160, 4), ("parent", 4, 4), ("pos", 32, 12), ("quat", 44, 16),
                                  ("rot", 60, 12), ("ptb", 96, 48), ("posscale", 72, 12), ("rotscale", 84, 12),
                                  ("qalign", 144, 16)):
            rel[name] = len(out) - lb
            for i in range(n):
                o = new_bi + i * BONE
                out.extend(out[o + field:o + field + size])
        struct.pack_into("<10i", out, lb, n, rel["flags"], rel["parent"], rel["pos"], rel["quat"], rel["rot"],
                         rel["ptb"], rel["posscale"], rel["rotscale"], rel["qalign"])
        struct.pack_into("<i", out, h2 + 16, lb - h2)
    nseq, si = struct.unpack_from("<ii", raw, 188)
    made = {}
    for s in range(nseq):
        so = si + s * SEQ
        wrel = struct.unpack_from("<i", raw, so + 156)[0]
        if not wrel:
            continue
        w = list(struct.unpack_from(f"<{nb}f", raw, so + wrel)) + [None] * k
        for j, nbn in enumerate(new):
            w[nb + j] = w[nbn["template"]]
        key = tuple(w)
        if key not in made:
            made[key] = len(out)
            out.extend(struct.pack(f"<{len(w)}f", *w))
        struct.pack_into("<i", out, so + 156, made[key] - so)
    struct.pack_into("<i", out, 76, len(out))
    return bytes(out)


def reflection(axis: int, c: float = 0.0) -> np.ndarray:
    m = np.eye(4)
    m[axis, axis] = -1.0
    m[axis, 3] = 2.0 * c
    return m


def apply(mdl_bytes: bytes, vtx_bytes: bytes, plan: dict) -> tuple[bytes, bytes, int]:
    """Add the mirrored strips and mirror bones to a model already compacted by
    rewrite_model_bytes(main triangles). plan: remesh.build() result. Returns (mdl, vtx, bones added)."""
    if not plan["mirror"]:
        return mdl_bytes, vtx_bytes, 0
    bones = read_bones(mdl_bytes)
    nb = len(bones)
    M = reflection(plan["axis"], plan["c"])
    pair_index, new_bones = {}, []
    for _key, (src, dst) in sorted(plan["pairs"].items()):
        if (src, dst) not in pair_index:
            pair_index[(src, dst)] = nb + len(new_bones)
            ptb = np.vstack([bones[dst]["ptb"], [0, 0, 0, 1]]) @ M
            new_bones.append(dict(name=f"{bones[src]['name']}~{bones[dst]['name']}", parent=dst,
                                  ptb=ptb[:3], template=dst))
    vtx = parse_vtx(vtx_bytes)
    mesh_index = {key: mi for mi, key in enumerate(plan["mesh_keys"])}
    for key, mt in plan["mirror"].items():
        b, m, k = key
        grp = vtx.bodyparts[b][m][0].meshes[k].groups[0]
        keep = np.unique(plan["main"][key])
        ids = np.searchsorted(keep, mt)
        if not (keep[ids] == mt).all():
            raise ValueError("mirrored triangle uses a vertex the main strip dropped")
        mi = mesh_index[key]
        changes = []
        for h in sorted(set(int(x) for x in grp.verts[np.unique(ids), 6])):
            pr = plan["pairs"].get((mi, h))
            if pr is None:
                raise ValueError(f"no mirror bone for mesh {key} slot {h}")
            changes.append(struct.pack("<ii", h, pair_index[pr]))
        st0 = grp.strips[0]
        off = len(grp.indices)
        grp.indices = np.concatenate([grp.indices, ids.ravel().astype(np.uint16)])
        grp.strips.append(Strip(st0.num_bones, st0.flags, b"".join(changes), ids.size, off,
                                st0.num_verts, st0.vert_offset))
    return append_bones(mdl_bytes, new_bones), write_vtx(vtx), len(new_bones)
