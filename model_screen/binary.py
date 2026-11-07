"""Direct binary rewriting of compiled Source models (MDL + VVD + .dx90.vtx), no decompile.

rewrite_model() keeps a subset of each mesh's original vertices (exact bytes: position, normal,
UV, bone weights, tangent) and a new triangle list per mesh. MDL vertex counts/offsets are
patched in place, the VVD is rebuilt compactly, the VTX is regenerated from its parsed tree
(bone state changes, flags, material replacement lists copied verbatim).

Supported: VTX v7, one LOD, VVD without fixups, meshes without flexes (checked, else refused).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

VVD_V = 48
VTX_VERT = 9


# ----------------------------------------------------------------------------- VTX tree

@dataclass
class Strip:
    num_bones: int
    flags: int
    bone_changes: bytes          # raw (hardwareID, newBoneID) pairs
    num_indices: int
    index_offset: int
    num_verts: int
    vert_offset: int


@dataclass
class StripGroup:
    flags: int
    verts: np.ndarray            # (n, 9) uint8 raw vertex records
    indices: np.ndarray          # (m,) uint16
    strips: list[Strip]


@dataclass
class Mesh:
    flags: int
    groups: list[StripGroup]


@dataclass
class Lod:
    switch_point: float
    meshes: list[Mesh]


@dataclass
class Vtx:
    header: bytes                # first 36 bytes (offsets rewritten on save)
    bodyparts: list[list[list[Lod]]]   # [bodypart][model][lod]
    mat_replacement: bytes       # raw tail block (material replacement lists + strings)


def parse_vtx(raw: bytes) -> Vtx:
    ver = struct.unpack_from("<i", raw, 0)[0]
    if ver != 7:
        raise ValueError("unsupported VTX version")
    nlods, mrl, nbp, bpo = struct.unpack_from("<iiii", raw, 20)
    ends = []
    bodyparts = []
    for b in range(nbp):
        bp = bpo + b * 8
        nmodels, mo = struct.unpack_from("<ii", raw, bp)
        models = []
        for mi in range(nmodels):
            mp = bp + mo + mi * 8
            nl, lo = struct.unpack_from("<ii", raw, mp)
            lods = []
            for li in range(nl):
                lp = mp + lo + li * 12
                nmeshes, meo, sw = struct.unpack_from("<iif", raw, lp)
                meshes = []
                for k in range(nmeshes):
                    kp = lp + meo + k * 9
                    nsg, sgo, mflags = struct.unpack_from("<iiB", raw, kp)
                    groups = []
                    for g in range(nsg):
                        gp = kp + sgo + g * 25
                        nv, vo, ni, io, ns, so, gflags = struct.unpack_from("<iiiiiiB", raw, gp)
                        verts = np.frombuffer(raw, np.uint8, nv * VTX_VERT, gp + vo).reshape(nv, VTX_VERT).copy()
                        idx = np.frombuffer(raw, "<u2", ni, gp + io).copy()
                        ends += [gp + vo + nv * VTX_VERT, gp + io + ni * 2]
                        strips = []
                        for s in range(ns):
                            sp = gp + so + s * 27
                            sni, sio, snv, svo, snb, sflags, nbsc, bsco = struct.unpack_from("<iiiihBii", raw, sp)
                            bsc = raw[sp + bsco: sp + bsco + nbsc * 8] if nbsc else b""
                            ends.append(sp + bsco + nbsc * 8)
                            strips.append(Strip(snb, sflags, bsc, sni, sio, snv, svo))
                        groups.append(StripGroup(gflags, verts, idx, strips))
                    meshes.append(Mesh(mflags, groups))
                lods.append(Lod(sw, meshes))
            models.append(lods)
        bodyparts.append(models)
    tail = b""
    if mrl and mrl >= max(ends, default=0):
        tail = raw[mrl:]
    elif mrl:
        raise ValueError("material replacement list is not the trailing block")
    return Vtx(raw[:36], bodyparts, tail)


def write_vtx(v: Vtx) -> bytes:
    out = bytearray(v.header)

    def alloc(n):
        pos = len(out)
        out.extend(b"\0" * n)
        return pos

    nbp = len(v.bodyparts)
    bp_pos = alloc(8 * nbp)
    struct.pack_into("<ii", out, 28, nbp, bp_pos)
    # level by level so each array of headers is contiguous
    model_hdr = []
    for b, models in enumerate(v.bodyparts):
        mp = alloc(8 * len(models))
        struct.pack_into("<ii", out, bp_pos + b * 8, len(models), mp - (bp_pos + b * 8))
        model_hdr.append(mp)
    lod_hdr = {}
    for b, models in enumerate(v.bodyparts):
        for m, lods in enumerate(models):
            hp = model_hdr[b] + m * 8
            lp = alloc(12 * len(lods)) if lods else hp
            struct.pack_into("<ii", out, hp, len(lods), (lp - hp) if lods else 0)
            lod_hdr[(b, m)] = lp
    mesh_hdr = {}
    for (b, m), lp in lod_hdr.items():
        for li, lod in enumerate(v.bodyparts[b][m]):
            hp = lp + li * 12
            kp = alloc(9 * len(lod.meshes)) if lod.meshes else hp
            struct.pack_into("<iif", out, hp, len(lod.meshes), (kp - hp) if lod.meshes else 0, lod.switch_point)
            mesh_hdr[(b, m, li)] = kp
    group_hdr = {}
    for (b, m, li), kp in mesh_hdr.items():
        for k, mesh in enumerate(v.bodyparts[b][m][li].meshes):
            hp = kp + k * 9
            gp = alloc(25 * len(mesh.groups)) if mesh.groups else hp
            struct.pack_into("<iiB", out, hp, len(mesh.groups), (gp - hp) if mesh.groups else 0, mesh.flags)
            group_hdr[(b, m, li, k)] = gp
    strip_hdr = {}
    for key, gp in group_hdr.items():
        b, m, li, k = key
        for g, grp in enumerate(v.bodyparts[b][m][li].meshes[k].groups):
            hp = gp + g * 25
            sp = alloc(27 * len(grp.strips)) if grp.strips else hp
            strip_hdr[key + (g,)] = (hp, sp)
    # payloads
    for key, (hp, sp) in strip_hdr.items():
        b, m, li, k, g = key
        grp = v.bodyparts[b][m][li].meshes[k].groups[g]
        vpos = alloc(grp.verts.size)
        out[vpos:vpos + grp.verts.size] = grp.verts.tobytes()
        ipos = alloc(grp.indices.size * 2)
        out[ipos:ipos + grp.indices.size * 2] = grp.indices.astype("<u2").tobytes()
        struct.pack_into("<iiiiiiB", out, hp, len(grp.verts), vpos - hp, len(grp.indices), ipos - hp,
                         len(grp.strips), (sp - hp) if grp.strips else 0, grp.flags)
        for s, st in enumerate(grp.strips):
            shp = sp + s * 27
            bpos = alloc(len(st.bone_changes)) if st.bone_changes else shp
            if st.bone_changes:
                out[bpos:bpos + len(st.bone_changes)] = st.bone_changes
            struct.pack_into("<iiiihBii", out, shp, st.num_indices, st.index_offset, st.num_verts, st.vert_offset,
                             st.num_bones, st.flags, len(st.bone_changes) // 8, (bpos - shp) if st.bone_changes else 0)
    if v.mat_replacement:
        mpos = alloc(len(v.mat_replacement))
        out[mpos:] = v.mat_replacement
        struct.pack_into("<i", out, 24, mpos)
    else:
        struct.pack_into("<i", out, 24, 0)
    return bytes(out)


# ----------------------------------------------------------------------------- MDL walk

@dataclass
class MdlMesh:
    offset: int          # byte offset of mstudiomesh_t
    numvertices: int
    vertexoffset: int
    numflexes: int


@dataclass
class MdlModel:
    offset: int          # byte offset of mstudiomodel_t
    numvertices: int
    vertexindex: int
    tangentsindex: int
    meshes: list[MdlMesh] = field(default_factory=list)


def walk_mdl(raw: bytes):
    numbodyparts, bodypartindex = struct.unpack_from("<ii", raw, 232)
    numflexdesc = struct.unpack_from("<i", raw, 260)[0]
    parts = []
    for b in range(numbodyparts):
        bb = bodypartindex + b * 16
        _n, nmodels, _base, modelindex = struct.unpack_from("<4i", raw, bb)
        models = []
        for m in range(nmodels):
            mb = bb + modelindex + m * 148
            nmeshes, meshindex, nverts, vindex, tindex = struct.unpack_from("<5i", raw, mb + 72)
            neyeballs = struct.unpack_from("<i", raw, mb + 100)[0]
            model = MdlModel(mb, nverts, vindex, tindex)
            for k in range(nmeshes):
                kb = mb + meshindex + k * 116
                _mat, _mi, mnv, mvo, nflex = struct.unpack_from("<5i", raw, kb)
                model.meshes.append(MdlMesh(kb, mnv, mvo, nflex))
            model.eyeballs = neyeballs
            models.append(model)
        parts.append(models)
    return parts, numflexdesc


# ----------------------------------------------------------------------------- rewrite

def mesh_arrays(mdl: Path):
    """Per (bodypart, model, mesh): VVD slice indices and VTX triangles in mesh-local ids."""
    raw_mdl = mdl.read_bytes()
    parts, nflex = walk_mdl(raw_mdl)
    vtx = parse_vtx(Path(str(mdl.with_suffix("")) + ".dx90.vtx").read_bytes())
    out = []
    for b, models in enumerate(parts):
        for m, model in enumerate(models):
            lods = vtx.bodyparts[b][m]
            for k, mesh in enumerate(model.meshes):
                start = model.vertexindex // VVD_V + mesh.vertexoffset
                tris = np.zeros((0, 3), np.int64)
                if lods and k < len(lods[0].meshes):
                    for grp in lods[0].meshes[k].groups:
                        orig = grp.verts[:, 4].astype(np.int64) | (grp.verts[:, 5].astype(np.int64) << 8)
                        for st in grp.strips:
                            if not st.flags & 1:
                                raise ValueError("triangle strips not supported")
                            seq = grp.indices[st.index_offset: st.index_offset + st.num_indices].astype(np.int64)
                            tris = np.concatenate([tris, orig[seq].reshape(-1, 3)])
                out.append(((b, m, k), start, mesh.numvertices, tris))
    return out


def rewrite_model_bytes(mdl: Path, new_tris: dict, drop_tangents: bool = False):
    """new_tris[(b,m,k)] = (T,3) mesh-local ORIGINAL vertex ids to keep (missing key = unchanged).
    Returns (mdl bytes, vvd bytes, dx90.vtx bytes, stats). drop_tangents removes the VVD tangent
    block (the engine then derives tangents itself: 25% smaller VVD, slightly flatter normal maps)."""
    raw_mdl = bytearray(mdl.read_bytes())
    vvd_raw = mdl.with_suffix(".vvd").read_bytes()
    vtx_path = Path(str(mdl.with_suffix("")) + ".dx90.vtx")
    vtx = parse_vtx(vtx_path.read_bytes())
    parts, nflex = walk_mdl(bytes(raw_mdl))
    nlods = struct.unpack_from("<i", vvd_raw, 12)[0]
    lodv = list(struct.unpack_from("<8i", vvd_raw, 16))
    nfix, fixstart, vstart, tstart = struct.unpack_from("<4i", vvd_raw, 48)
    if nfix or nlods != 1:
        raise ValueError("VVD with fixups/LODs not supported")
    nverts = lodv[0]
    V = np.frombuffer(vvd_raw, np.uint8, nverts * VVD_V, vstart).reshape(nverts, VVD_V)
    Tg = np.frombuffer(vvd_raw, np.uint8, nverts * 16, tstart).reshape(nverts, 16) if tstart else None

    new_V, new_T = [], []
    cursor = 0
    total_before = total_after = 0
    for b, models in enumerate(parts):
        for m, model in enumerate(models):
            if any(me.numflexes for me in model.meshes) or getattr(model, "eyeballs", 0):
                if any(new_tris.get((b, m, k)) is not None for k in range(len(model.meshes))):
                    raise ValueError("flexes/eyeballs not supported")
            model_start_new = cursor
            lods = vtx.bodyparts[b][m]
            for k, mesh in enumerate(model.meshes):
                start = model.vertexindex // VVD_V + mesh.vertexoffset
                n = mesh.numvertices
                total_before += n
                vtx_mesh = lods[0].meshes[k] if lods and k < len(lods[0].meshes) else None
                want = new_tris.get((b, m, k))
                if want is None or vtx_mesh is None or not vtx_mesh.groups:
                    keep = np.arange(n)
                    remap = np.arange(n)
                else:
                    keep = np.unique(want)
                    remap = np.full(n, -1, np.int64)
                    remap[keep] = np.arange(len(keep))
                    grp = vtx_mesh.groups[0]
                    if len(vtx_mesh.groups) != 1 or len(grp.strips) != 1:
                        raise ValueError("only one strip group with one strip per mesh is supported")
                    orig = grp.verts[:, 4].astype(np.int64) | (grp.verts[:, 5].astype(np.int64) << 8)
                    first = {}
                    for i, o in enumerate(orig):
                        first.setdefault(int(o), i)
                    rec = np.stack([grp.verts[first[int(o)]] for o in keep]) if len(keep) else np.zeros((0, 9), np.uint8)
                    newid = np.arange(len(keep))
                    rec[:, 4] = (newid & 0xFF).astype(np.uint8)
                    rec[:, 5] = ((newid >> 8) & 0xFF).astype(np.uint8)
                    grp.verts = rec
                    grp.indices = remap[want].reshape(-1).astype(np.uint16)
                    st = grp.strips[0]
                    st.num_indices, st.index_offset = len(grp.indices), 0
                    st.num_verts, st.vert_offset = len(keep), 0
                new_V.append(V[start + keep])
                if Tg is not None:
                    new_T.append(Tg[start + keep])
                # patch mesh: numvertices, vertexoffset, numLODVertexes
                struct.pack_into("<ii", raw_mdl, mesh.offset + 8, len(keep), cursor - model_start_new)
                lod_counts = list(struct.unpack_from("<8i", raw_mdl, mesh.offset + 52))
                lod_counts = [len(keep) if c == n else c for c in lod_counts]
                struct.pack_into("<8i", raw_mdl, mesh.offset + 52, *lod_counts)
                cursor += len(keep)
                total_after += len(keep)
            struct.pack_into("<i", raw_mdl, model.offset + 80, cursor - model_start_new)
            struct.pack_into("<ii", raw_mdl, model.offset + 84, model_start_new * VVD_V, model_start_new * 16)
    Vn = np.concatenate(new_V) if new_V else np.zeros((0, VVD_V), np.uint8)
    hdr = bytearray(vvd_raw[:vstart])
    new_lodv = [cursor if c == nverts else c for c in lodv]
    struct.pack_into("<8i", hdr, 16, *new_lodv)
    keep_t = Tg is not None and not drop_tangents
    tstart_new = vstart + Vn.size if keep_t else 0
    struct.pack_into("<4i", hdr, 48, 0, fixstart, vstart, tstart_new)
    vvd_new = bytes(hdr) + Vn.tobytes() + (np.concatenate(new_T).tobytes() if keep_t and new_T else b"")
    return bytes(raw_mdl), vvd_new, write_vtx(vtx), {"vertices_before": total_before, "vertices_after": total_after}


def rewrite_model(mdl: Path, out_dir: Path, new_tris: dict, drop_tangents: bool = False) -> dict:
    """Write <out_dir>/<name>.mdl/.vvd/.dx90.vtx (atomically per file) and return the stats."""
    mdl_b, vvd_b, vtx_b, stats = rewrite_model_bytes(mdl, new_tris, drop_tangents)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = mdl.stem
    for name, data in ((stem + ".mdl", mdl_b), (stem + ".vvd", vvd_b), (stem + ".dx90.vtx", vtx_b)):
        tmp = out_dir / (name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(out_dir / name)
    return stats
