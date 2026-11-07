"""Remesh engine of the Models "Tela" profile "strong_mirror" (Forte + espelho).

Same contract as simplify.simplify_model for the main triangles ({mesh key: (T,3) mesh-local
ORIGINAL vertex ids}), plus the mirrored triangles drawn by mirror.apply().

1. Records of a mesh that differ only by UV are welded when no texture of the mesh needs the UV
   (uniform colour, noise masks): those UV seams disappear.
2. Mirror plan: the mirror plane is found from point pairs; each record gets a partner (mirrored
   position within pos_tol and mirrored normal, same mesh, single bone). Right-side triangles whose
   partner triangle exists on the left and whose textures show the same thing at both UVs
   (texture-verified, so text and badges never come out reversed) form R; their partners form L.
3. R is removed; the rest (left, centre, unmatched right side) is simplified one physically
   connected part at a time with meshoptimizer: UV seams that matter, hard edges, material and
   bone boundaries are meshopt seams (both sides collapse together: no cracks, borders slide along
   themselves). L is kept as its own region (its border vertices are duplicated, which meshopt
   treats as a seam), and the L border, the right-side positions that touched R (with their left
   partners) and the L vertices on the plane are locked, so the mirror image of the simplified L
   meets the rest exactly. Locks are applied to every record of a position (meshopt reads the
   lock of one wedge only).
4. Per-part error: Forte's rule (screen px at d, roundness = fraction of the part size,
   thinness = fraction of the smallest extent, floor for close views).
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np

from . import meshopt
from .binary import mesh_arrays, parse_vtx
from .gridsearch import radius_pairs
from .simplify import Profile, _eps_for, components, vvd_data, weld_ids

# Texture parameters that are NOT sampled through the mesh UV (cubemaps, lighting lookups). Every
# other parameter whose value is a texture of the addon counts as UV-sampled ($basetexture,
# $refracttinttexture, $detail, ...), so an unknown shader parameter can only make us more careful.
NON_UV = {"$envmap", "$lightwarptexture", "$phongwarptexture", "$fresnelwarptexture", "$ramptexture",
          "$envmapmask_cubemap"}
# Shared paint "flake" mask (alpha of the LVS body normal map): noise, any UV shows the same.
NOISE_OK = {"models/diggercars/shared/skin_normal"}
BARY = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1 / 3, 1 / 3, 1 / 3], [.6, .2, .2], [.2, .6, .2], [.2, .2, .6]])

_varies_cache: dict = {}
_image_cache: dict = {}


def _decode(resolver, ref: str, max_size: int):
    from texlab.vtf import decode_level, read_vtf
    p = resolver.find(ref, "vtf")
    if p is None:
        return None
    try:
        v = read_vtf(p)
        lvl = 0
        while lvl + 1 < v.mip_count and max(v.width >> lvl, v.height >> lvl) > max_size:
            lvl += 1
        return decode_level(v, lvl).astype(np.float32)
    except Exception:
        return None


def _tex_varies(resolver, ref: str) -> bool | None:
    """True when the texture shows something, False when uniform, None when missing/unreadable."""
    key = (id(resolver), ref)
    if key not in _varies_cache:
        img = _decode(resolver, ref, 256)
        r = None
        if img is not None:
            sd = float(img[..., :3].std((0, 1)).max())
            sa = float(img[..., 3].std()) if img.shape[-1] == 4 else 0.0
            r = not (sd < 3 and sa < 3)
        _varies_cache[key] = r
    return _varies_cache[key]


def _image(resolver, ref: str):
    key = (id(resolver), ref)
    if key not in _image_cache:
        _image_cache[key] = _decode(resolver, ref, 1024)
    return _image_cache[key]


def _sample(img, uv):
    h, w = img.shape[:2]
    x = np.mod(uv[:, 0], 1.0) * w - 0.5
    y = np.mod(uv[:, 1], 1.0) * h - 0.5
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    x0 %= w
    y0 %= h
    x1 = (x0 + 1) % w
    y1 = (y0 + 1) % h
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x1] * fx * (1 - fy)
            + img[y1, x0] * (1 - fx) * fy + img[y1, x1] * fx * fy)


def _uv_refs(mat, resolver) -> list[tuple[str, float]]:
    """(texture, uv scale) for every UV-sampled texture of a material (see NON_UV)."""
    from texlab.materials import normalize_texture_ref
    out = []
    for prm in sorted(mat.params):
        val = mat.params[prm]
        if prm in NON_UV or not isinstance(val, str) or not val.strip() or val.strip()[0] in "[{":
            continue
        ref = normalize_texture_ref(val)
        if not ref or resolver.find(ref, "vtf") is None:
            continue
        out.append((ref, mat.texture_scale(prm)))
    return out


def _slots(mdl: Path, resolver):
    from texlab.source_model import _read_mdl
    _v, _n, _c, textures, cdmaterials, skins, bodyparts = _read_mdl(mdl)
    mats = {}
    for fam in skins or [list(range(len(textures)))]:
        for slot, ti in enumerate(fam):
            mat = resolver.model_material(cdmaterials, textures[ti]) if 0 <= ti < len(textures) else None
            mats.setdefault(slot, []).append(mat)
    order = []
    for _b, models in bodyparts:
        for _m, _nv, _vi, meshes in models:
            for material, _mnv, _vo in meshes:
                order.append(material)
    return mats, order


def mesh_uv_free(mdl: Path, resolver) -> list[bool]:
    """Per mesh (mesh_arrays order): True when no texture of any skin of its slot needs the UV."""
    mats, order = _slots(mdl, resolver)
    free_slot = {}
    for slot, lst in mats.items():
        ok = True
        for mat in lst:
            if mat is None:
                ok = False
                break
            for ref, _sc in _uv_refs(mat, resolver):
                if ref not in NOISE_OK and _tex_varies(resolver, ref) is not False:
                    ok = False
                    break
            if not ok:
                break
        free_slot[slot] = ok
    return [free_slot.get(material, False) for material in order]


def _uv_textures(mdl: Path, resolver):
    """Per mesh: [(texture, uv scale)] that need the UV, or a list containing None when unknown."""
    mats, order = _slots(mdl, resolver)
    per_slot = {}
    for slot, lst in mats.items():
        out = []
        for mat in lst:
            if mat is None:
                out.append(None)
                continue
            for ref, sc in _uv_refs(mat, resolver):
                if ref in NOISE_OK or _tex_varies(resolver, ref) is False:
                    continue
                out.append((ref, sc))
        per_slot[slot] = out
    return [per_slot.get(material, [None]) for material in order]


def _record_bones(mdl: Path, meshes, n):
    """Per record: hardware slot of its first single-bone VTX entry and the bone that slot means."""
    vtx = parse_vtx(Path(str(mdl.with_suffix("")) + ".dx90.vtx").read_bytes())
    slot = np.full(n, -1)
    bone = np.full(n, -1)
    for (b, m, k), st, _cnt, _t in meshes:
        lods = vtx.bodyparts[b][m]
        if not lods or k >= len(lods[0].meshes):
            continue
        for grp in lods[0].meshes[k].groups:
            for s in grp.strips:
                hw = {}
                for j in range(len(s.bone_changes) // 8):
                    h, nb = struct.unpack_from("<ii", s.bone_changes, j * 8)
                    hw[h] = nb
                ev = grp.verts[s.vert_offset:s.vert_offset + s.num_verts]
                orig = ev[:, 4].astype(np.int64) | (ev[:, 5].astype(np.int64) << 8)
                for e, o in zip(ev, orig):
                    r = st + int(o)
                    if slot[r] < 0 and e[3] == 1:
                        slot[r] = int(e[6])
                        bone[r] = hw.get(int(e[6]), -1)
    return slot, bone


def _plane(P, used, tol):
    """Best mirror plane (share of points with a partner, axis, c)."""
    best = (0.0, 1, 0.0)
    Pu = P[used]
    rng = np.random.default_rng(0)
    S = Pu[rng.choice(len(Pu), min(len(Pu), 20000), replace=False)]
    for ax in range(3):
        oth = [i for i in range(3) if i != ax]
        cands = [0.0, 0.5 * (Pu[:, ax].min() + Pu[:, ax].max())]
        qi, pj, _d, _r = radius_pairs(Pu[:, oth], S[:, oth], max(tol, 1e-3), k=8)
        diff = S[qi, ax] - Pu[pj, ax]
        mids = ((S[qi, ax] + Pu[pj, ax]) / 2)[np.abs(diff) > 1.0]
        if len(mids):
            h, e = np.histogram(mids, bins=2000)
            c0 = 0.5 * (e[h.argmax()] + e[h.argmax() + 1])
            near = mids[np.abs(mids - c0) < 0.5]
            if len(near):
                cands.append(float(np.median(near)))
        for c in cands:
            Q = S.copy()
            Q[:, ax] = 2 * c - Q[:, ax]
            qi2, _pj, _d, _r = radius_pairs(Pu, Q, tol, k=1)
            frac = len(np.unique(qi2)) / len(S)
            if frac > best[0]:
                best = (frac, ax, c)
    return best


def build(mdl: Path, p: Profile, uvtol_per_mesh, uv_free, resolver, mirror: bool = True,
          tol_mean: float = 10.0, tol_max: float = 64.0, pos_tol: float = 0.05, ncos: float = 0.999):
    f, bkey = vvd_data(mdl)
    meshes = mesh_arrays(mdl)
    n = len(f)
    mid = np.full(n, -1)
    tol = np.full(n, 1.0 / 1024.0)
    free = np.zeros(n, bool)
    for mi, (_k, st, cnt, _t) in enumerate(meshes):
        mid[st:st + cnt] = mi
        tol[st:st + cnt] = uvtol_per_mesh[mi]
        free[st:st + cnt] = uv_free[mi]
    nonempty = [t + st for _k, st, cnt, t in meshes if len(t)]
    if not nonempty:
        return None
    # UV tolerance also follows textures uvtol.py does not list (e.g. $refracttinttexture)
    from .uvtol import _vtf_size
    texs = _uv_textures(mdl, resolver)
    for mi, (_k, st, cnt, _t) in enumerate(meshes):
        for item in texs[mi]:
            if item is None or item[1] > 4.0:
                continue
            found = resolver.find(item[0], "vtf")
            need = (_vtf_size(found) if found else 1024) * item[1]
            if need > 0:
                tol[st:st + cnt] = np.minimum(tol[st:st + cnt], 1.0 / need)
    T = np.concatenate(nonempty).astype(np.int64)
    before = len(np.unique(T))
    pos = f[:, 0:3].astype(np.float64)
    nrm = f[:, 3:6].astype(np.float64)
    uv = f[:, 6:8].astype(np.float64)
    wid = weld_ids(pos)
    nw = int(wid.max()) + 1
    first = np.full(nw, n)
    np.minimum.at(first, wid, np.arange(n))
    psnap = pos[first[wid]]
    # ---- 1. weld UV-only splits of UV-free meshes
    rep = np.arange(n)
    cos_w = math.cos(math.radians(2.0))
    cand = np.flatnonzero(free)
    if len(cand):
        order = cand[np.lexsort((bkey[cand], mid[cand], wid[cand]))]
        k1 = np.stack([wid[order], mid[order], bkey[order]], 1)
        brk = np.flatnonzero((k1[1:] != k1[:-1]).any(1)) + 1
        for grp in np.split(order, brk):
            if len(grp) < 2:
                continue
            reps = []
            for i in grp:
                for r in reps:
                    if nrm[i] @ nrm[r] >= cos_w:
                        rep[i] = r
                        break
                else:
                    reps.append(i)
    T = rep[T]
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    used = np.unique(T)
    # ---- 2. mirror plan
    isR = np.zeros(len(T), bool)
    isL = np.zeros(len(T), bool)
    pairs = {}                     # (mesh index, hardware slot) -> (left bone, right bone)
    lock = np.zeros(n, bool)
    axis, c, frac = 1, 0.0, 0.0
    if mirror and len(used):
        frac, axis, c = _plane(psnap, used, pos_tol)
    if mirror and frac >= 0.10:
        slot, bone = _record_bones(mdl, meshes, n)
        Pu = psnap[used]
        Q = Pu.copy()
        Q[:, axis] = 2 * c - Q[:, axis]
        Nm = nrm[used].copy()
        Nm[:, axis] = -Nm[:, axis]
        qi, pj, _d, rank = radius_pairs(Pu, Q, pos_tol, k=6)
        partner = np.full(n, -1)
        for kk in range(6):
            sel = rank == kk
            u = used[qi[sel]]
            cu = used[pj[sel]]
            good = ((partner[u] < 0) & (mid[cu] == mid[u]) & (bone[u] >= 0) & (bone[cu] >= 0)
                    & ((nrm[cu] * Nm[qi[sel]]).sum(1) > ncos))
            partner[u[good]] = cu[good]
        s = -(psnap[:, axis] - c)
        right = s > max(1e-3, pos_tol)
        plane = np.abs(s) <= max(1e-3, pos_tol)
        cand_t = (partner[T] >= 0).all(1) & (right[T] | plane[T]).all(1) & right[T].any(1)
        tri_index = {}
        for ti, t in enumerate(T.tolist()):
            tri_index[tuple(sorted(t))] = ti
        lt = np.full(len(T), -1)
        for ti in np.flatnonzero(cand_t):
            lt[ti] = tri_index.get(tuple(sorted(partner[T[ti]].tolist())), -1)
        cand_t &= lt >= 0
        # texture check where the right side would show the left side's UVs
        idx = np.flatnonzero(cand_t)
        if len(idx):
            tm = mid[T[idx, 0]]
            for mi in np.unique(tm):
                if uv_free[mi]:
                    continue
                sel = idx[tm == mi]
                same_uv = (np.abs(uv[T[sel]] - uv[partner[T[sel]]]).max((1, 2)) == 0.0)
                tl = texs[mi]
                if not tl or any(x is None for x in tl):
                    cand_t[sel] = same_uv
                    continue
                uvr = np.einsum("pk,tkc->tpc", BARY, uv[T[sel]]).reshape(-1, 2)
                uvl = np.einsum("pk,tkc->tpc", BARY, uv[partner[T[sel]]]).reshape(-1, 2)
                good = np.ones(len(sel), bool)
                for ref, sc in tl:
                    img = _image(resolver, ref)
                    if img is None:
                        good[:] = False
                        break
                    dd = np.abs(_sample(img, uvr * sc) - _sample(img, uvl * sc)).reshape(len(sel), len(BARY), -1)
                    good &= (dd.mean((1, 2)) <= tol_mean) & (dd.max((1, 2)) <= tol_max)
                cand_t[sel] = good | same_uv
        # one (left bone, right bone) pair per (mesh, hardware slot): the majority wins
        idx = np.flatnonzero(cand_t)
        votes = {}
        for ti in idx:
            for a in T[ti]:
                pa = partner[a]
                key = (int(mid[pa]), int(slot[pa]))
                pr = (int(bone[pa]), int(bone[a]))
                votes.setdefault(key, {}).setdefault(pr, 0)
                votes[key][pr] += 1
        for key, v in votes.items():
            pairs[key] = max(v.items(), key=lambda kv: kv[1])[0]
        for ti in idx:
            for a in T[ti]:
                pa = partner[a]
                if slot[pa] < 0 or pairs.get((int(mid[pa]), int(slot[pa]))) != (int(bone[pa]), int(bone[a])):
                    cand_t[ti] = False
                    break
        isR = cand_t
        isL[lt[isR]] = True
        keepT = ~isR
        # interface by position: every record at a position touched by R and by the rest, and the
        # left partners of those positions
        wR = np.zeros(nw, bool)
        wR[wid[T[isR].ravel()]] = True
        wK = np.zeros(nw, bool)
        wK[wid[T[keepT].ravel()]] = True
        iface = (wR & wK)[wid]
        lock |= iface
        pw = np.zeros(nw, bool)
        pw[wid[partner[np.flatnonzero(iface & (partner >= 0))]]] = True
        lock |= pw[wid]
        inL = np.zeros(n, bool)
        inL[T[isL].ravel()] = True
        lock |= inL & plane
    # ---- 3. half model with L as its own region
    keepT = ~isR
    TH = T[keepT]
    LH = isL[keepT]
    inL = np.zeros(n, bool)
    inL[TH[LH].ravel()] = True
    inN = np.zeros(n, bool)
    inN[TH[~LH].ravel()] = True
    shared = np.flatnonzero(inL & inN)
    origin = np.arange(n)
    if len(shared):
        copy_of = np.full(n, -1)
        copy_of[shared] = n + np.arange(len(shared))
        origin = np.concatenate([np.arange(n), shared])
        TL = TH[LH]
        TH = TH.copy()
        TH[LH] = np.where(copy_of[TL] >= 0, copy_of[TL], TL)
        lock = np.concatenate([lock, np.ones(len(shared), bool)])
        lock[shared] = True
    isLvert = np.zeros(len(origin), bool)
    isLvert[TH[LH].ravel()] = True
    wid2 = wid[origin]
    ps2 = psnap[origin]
    uvs = uv / tol[:, None]
    uvs[free] = 0.0
    A_all = np.c_[nrm, uvs][origin]
    # bone-change positions stay; every lock covers all records of its position
    u2 = np.unique(TH)
    ws, wb = wid2[u2], bkey[origin][u2]
    o = np.lexsort((wb, ws))
    ws, wb = ws[o], wb[o]
    lw = np.zeros(nw, bool)
    lw[np.unique(ws[1:][(ws[1:] == ws[:-1]) & (wb[1:] != wb[:-1])])] = True
    lw[wid2[lock]] = True
    lock = lw[wid2]
    lab = components(nw, np.concatenate([wid2[TH[:, [0, 1]]], wid2[TH[:, [1, 2]]]]))
    tl_ = lab[wid2[TH[:, 0]]]
    order = np.argsort(tl_, kind="stable")
    bounds = np.flatnonzero(np.r_[True, tl_[order][1:] != tl_[order][:-1], True])
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        tris = TH[order[a:b]]
        if len(tris) < p.min_tris:
            out.append(tris)
            continue
        verts = np.unique(tris)
        Pp = ps2[verts]
        eps = _eps_for(np.sort(Pp.max(0) - Pp.min(0)), p)
        ltri = np.searchsorted(verts, tris)
        wts = np.array([eps / math.radians(p.normal_deg)] * 3 + [eps / p.uv_texels] * 2, np.float32)
        lk = lock[verts].astype(np.uint8) * meshopt.V_LOCK
        res = meshopt.simplify_with_attributes(ltri, Pp, A_all[verts], wts, lk, eps, meshopt.ERROR_ABSOLUTE)
        out.append(verts[res] if len(res) else tris)
    T3 = np.concatenate(out) if out else np.zeros((0, 3), np.int64)
    tri_L = isLvert[T3].all(1)
    T3o = origin[T3]
    main, mirr = {}, {}
    tm = mid[T3o[:, 0]]
    for mi, (key, st, _cnt, _t) in enumerate(meshes):
        sel = tm == mi
        main[key] = T3o[sel] - st
        ml = T3o[sel & tri_L]
        if len(ml):
            mirr[key] = ml[:, [0, 2, 1]] - st      # image of a left triangle: corner order reversed
    return dict(main=main, mirror=mirr, pairs=pairs, axis=axis, c=c, plane_share=frac,
                mesh_keys=[key for key, *_ in meshes],
                stats=dict(vertices_before=before, vertices_after=int(len(np.unique(T3o))),
                           triangles=int(len(T)), mirrored_source=int(isR.sum()),
                           mirrored_out=int(tri_L.sum())))
