"""Screen-aware simplification of compiled Source models (product engine of the Models "Tela" mode).

Compiled game models are cut into thousands of tiny islands (UV seams + hard edges), so most
vertices sit on island borders. Two passes, both keeping ORIGINAL vertices (exact bytes):

1. Consistent seams: every island border is followed as a chain of welded points; points are
   removed along each chain (Douglas-Peucker) in ALL islands sharing the chain, so no crack can
   open. Corners (3+ islands, bone change) stay. A point goes only if, on every island side, its
   UV and normal are reproduced by the interpolation along the chain and no triangle flips. The
   chain tolerance is also limited by the chain's own size: a small circle (cap outline, bolt
   hole) keeps enough sides.
2. Island interiors: meshoptimizer collapse inside each island, borders locked, tolerance set by
   the island's own size.

Limits: screen error (px at d units, 1080p 75 deg), normal deviation, UV deviation in texels of the
texture the material really samples, roundness (fraction of size) and thinness (fraction of
thickness). Two profiles: "balanced" (near-identical) and "strong" (>= 50% smaller models, small
close-up differences; also drops the VVD tangent block).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import meshopt
from .binary import mesh_arrays, VVD_V

FOCAL_1080 = 540.0 / math.tan(math.radians(37.5))
WELD = 2e-4


@dataclass(frozen=True)
class Profile:
    name: str
    px: float                 # screen error budget (pixels at 1080p) ...
    d: float = 40.0           # ... at this viewing distance (units)
    d_closest: float = 16.0   # roundness/thinness never asked beyond what shows from here ...
    px_close: float = 1.0     # ... at this pixel tolerance
    rel_size: float = 0.01    # roundness: fraction of the piece/chain size
    rel_thin: float = 0.2     # thin parts: fraction of the thickness
    normal_deg: float = 20.0
    uv_texels: float = 1.0
    drop_tangents: bool = False
    min_tris: int = 4
    engine: str = "seams"     # "seams": this module; "remesh": remesh.py + mirror.py (whole parts + mirror)


PROFILES = {
    "balanced": Profile("balanced", px=2.0, normal_deg=20.0, uv_texels=1.0, rel_size=0.01),
    "strong": Profile("strong", px=6.0, normal_deg=35.0, uv_texels=3.0, rel_size=0.012, drop_tangents=True),
    # Forte's limits with the remesh engine: whole-part simplification + mirror instancing
    "strong_mirror": Profile("strong_mirror", px=6.0, normal_deg=35.0, uv_texels=3.0, rel_size=0.012,
                             drop_tangents=True, engine="remesh"),
}


# ----------------------------------------------------------------------------- helpers

def vvd_data(mdl: Path):
    raw = mdl.with_suffix(".vvd").read_bytes()
    n = int(np.frombuffer(raw, "<i4", 1, 16)[0])
    vstart = int(np.frombuffer(raw, "<i4", 1, 56)[0])
    V = np.frombuffer(raw, np.uint8, n * VVD_V, vstart).reshape(n, VVD_V)
    f = V[:, 16:48].copy().view(np.float32).reshape(n, 8)
    w = V[:, 0:12].copy().view(np.float32).reshape(n, 3)
    b = V[:, 12:15].astype(np.int64)
    nb = V[:, 15]
    key = np.zeros(n, np.int64)
    for k in range(3):
        use = (k < nb) & (w[:, k] > 0)
        key = key * 257 + np.where(use, b[:, k] + 1, 0)
    return f, key


def weld_ids(pos: np.ndarray, cell: float = WELD) -> np.ndarray:
    """Id per vertex, equal for points that fall in the same grid cell (seam duplicates)."""
    q = np.round(pos.astype(np.float64) / cell).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    return inv.ravel()


def components(n: int, edges: np.ndarray, use_native: bool = True) -> np.ndarray:
    """Connected component label per node (min-label propagation with pointer jumping): the
    smallest node index of the component. The Rust union-find gives the same labels."""
    lab = np.arange(n)
    if len(edges) == 0:
        return lab
    if use_native:
        from texlab import native
        res = native.components(n, edges)
        if res is not None:
            return res
    a, b = edges[:, 0], edges[:, 1]
    while True:
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m)
        np.minimum.at(new, b, m)
        new = new[new]
        if np.array_equal(new, lab):
            return lab
        lab = new


def douglas_peucker(points: np.ndarray, eps: float) -> np.ndarray:
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = points[a], points[b]
        d = q - p
        L2 = float(d @ d)
        seg = points[a + 1:b] - p
        if L2 < 1e-24:
            dist = np.linalg.norm(seg, axis=1)
        else:
            t = np.clip(seg @ d / L2, 0, 1)
            dist = np.linalg.norm(seg - np.outer(t, d), axis=1)
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return keep


def _eps_for(ext_sorted: np.ndarray, p: Profile) -> float:
    screen = p.px * p.d / FOCAL_1080
    thin = p.rel_thin * max(float(ext_sorted[0]), 1e-4)
    e = min(screen, p.rel_size * 0.5 * float(ext_sorted[2]), thin)
    return max(e, min(p.px_close * p.d_closest / FOCAL_1080, thin))


# ----------------------------------------------------------------------------- pass 1: seams

def _dot3(a, b) -> float:
    # numpy's length-3 float64 dot (BLAS ddot): 0.0, then += a[i]*b[i] in order, no fused multiply-add
    return 0.0 + a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _clip01(x: float) -> float:
    # np.clip(x, 0, 1) on a float64: NaN passes through, -0.0 becomes 0.0
    if x == x:
        x = x if x > 0.0 else 0.0
        x = x if x < 1.0 else 1.0
    return x


def _seams(f, bkey, wid, T, uvtol_v, p: Profile, use_native: bool = True):
    """Pass 1. Runs in Rust (gmo_native.dll, `gmo_seams`) when available; the Python below is the
    reference and the fallback. Both reproduce the original numpy code exactly: Python set
    iteration order (the DLL emulates CPython's set table) and numpy's float operation order.
    T is updated in place (callers read it afterwards)."""
    pos = f[:, 0:3].astype(np.float64)
    nrm = f[:, 3:6].astype(np.float64)
    uv = f[:, 6:8].astype(np.float64)
    nW = int(wid.max()) + 1
    P = np.zeros((nW, 3))
    P[wid] = pos
    tw = wid[T]
    comp = components(nW, np.concatenate([tw[:, [0, 1]], tw[:, [1, 2]]]))
    # per component bounding box (max/min do not depend on the order, so grouping by sort is exact)
    srt = np.argsort(comp, kind="stable")
    cs = comp[srt]
    seg = np.flatnonzero(np.r_[True, cs[1:] != cs[:-1]])
    Ps = P[srt]
    ext = np.maximum.reduceat(Ps, seg, axis=0) - np.minimum.reduceat(Ps, seg, axis=0)
    labels = cs[seg]
    comp_eps = {}
    for c in np.unique(comp[tw[:, 0]]):
        comp_eps[int(c)] = _eps_for(np.sort(ext[np.searchsorted(labels, c)]), p)
    cos_n = math.cos(math.radians(p.normal_deg))
    floor = p.px_close * p.d_closest / FOCAL_1080 * 0.5
    from texlab import native   # Rust port (gmo_native.dll): same result, Python below is the fallback
    eps_arr = np.full(nW, np.nan)
    eps_arr[list(comp_eps)] = list(comp_eps.values())
    res = native.seams(pos, nrm, uv, wid, bkey, uvtol_v * p.uv_texels, P, comp, eps_arr, T, cos_n, floor,
                       p.rel_size) if use_native else None
    if res is not None:
        tris, alive, removed = res
        T[:] = tris
        T2 = T[alive]
        nd = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
        return T2[nd], removed
    TL = T.tolist()
    widL = wid.tolist()
    alive = [True] * len(TL)
    v2t = defaultdict(set)
    for i, t in enumerate(TL):
        for v in t:
            v2t[v].add(i)
    w2v = defaultdict(list)
    for v in np.unique(T).tolist():
        w2v[widL[v]].append(v)

    def border_neighbors(v):
        cnt = {}
        for ti in v2t[v]:
            if alive[ti]:
                for x in TL[ti]:
                    if x != v:
                        cnt[x] = cnt.get(x, 0) + 1
        return [x for x, c in cnt.items() if c == 1]

    adj = defaultdict(set)
    for vs in w2v.values():
        for v in vs:
            for x in border_neighbors(v):
                a, b = widL[v], widL[x]
                if a != b:
                    adj[a].add(b)
                    adj[b].add(a)
    corner = {q for q in adj if len(adj[q]) != 2}
    bkeyL = bkey.tolist()
    for q, vs in w2v.items():
        if q in adj and len({bkeyL[v] for v in vs}) > 1:
            corner.add(q)
    uv_lim = (uvtol_v * p.uv_texels).tolist()
    geo =(P.tolist(), widL, w2v, v2t, TL, alive, pos.tolist(), nrm.tolist(), uv.tolist(), cos_n, uv_lim,
           border_neighbors)
    seen = set()
    removed = 0
    for c in list(corner) + [q for q in adj if q not in corner]:
        for nb in list(adj[c]):
            if (c, nb) in seen:
                continue
            chain = [c, nb]
            seen.add((c, nb)); seen.add((nb, c))
            while chain[-1] not in corner and chain[-1] != c:
                nxt = [x for x in adj[chain[-1]] if x != chain[-2]]
                if not nxt:
                    break
                seen.add((chain[-1], nxt[0])); seen.add((nxt[0], chain[-1]))
                chain.append(nxt[0])
            if len(chain) < 3:
                continue
            cext = P[chain].max(0) - P[chain].min(0)
            eps = min(comp_eps[int(comp[chain[0]])], max(p.rel_size * 0.5 * float(cext.max()), floor))
            keep = douglas_peucker(P[chain], eps)
            k_prev = 0
            for i in range(1, len(chain) - 1):
                if keep[i]:
                    k_prev = i
                    continue
                k_next = i + 1
                while k_next < len(chain) - 1 and not keep[k_next]:
                    k_next += 1
                if _remove_point(chain, i, k_prev, k_next, *geo):
                    removed += 1
                else:
                    keep[i] = True
                    k_prev = i
    T[:] = np.array(TL, dtype=T.dtype).reshape(T.shape)
    T2 = T[np.array(alive, bool)]
    nd = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
    return T2[nd], removed


def _walk_to(u, chain, i, k_next, wid, border_neighbors):
    cur = u
    for j in range(i + 1, k_next + 1):
        cand = [x for x in border_neighbors(cur) if wid[x] == chain[j]]
        if len(cand) != 1:
            return None
        cur = cand[0]
    return cur


def _cross_sub(o0, o1, o2):
    # np.cross(o1 - o0, o2 - o0) for 3-vectors, same float operations
    a0, a1, a2 = o1[0] - o0[0], o1[1] - o0[1], o1[2] - o0[2]
    b0, b1, b2 = o2[0] - o0[0], o2[1] - o0[1], o2[2] - o0[2]
    return (a1 * b2 - a2 * b1, a2 * b0 - a0 * b2, a0 * b1 - a1 * b0)


def _remove_point(chain, i, k_prev, k_next, P, wid, w2v, v2t, T, alive, pos, nrm, uv, cos_n, uv_lim,
                  border_neighbors):
    """P, wid, T, pos, nrm, uv, uv_lim are Python lists (see _seams)."""
    q, a, b = chain[i], chain[k_prev], chain[k_next]
    pa, pb, pq = P[a], P[b], P[q]
    d = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
    L2 = _dot3(d, d)
    t = _clip01(_dot3((pq[0] - pa[0], pq[1] - pa[1], pq[2] - pa[2]), d) / L2) if L2 > 1e-12 else 0.0
    s = 1 - t
    plans = []
    for u in w2v[q]:
        bn = border_neighbors(u)
        tgt = [x for x in bn if wid[x] == chain[i - 1]]
        if len(bn) != 2 or len(tgt) != 1:
            return False
        v = tgt[0]
        nxt = _walk_to(u, chain, i, k_next, wid, border_neighbors)
        if nxt is None:
            return False
        ref = v
        nr, nn = nrm[ref], nrm[nxt]
        n_i = (s * nr[0] + t * nn[0], s * nr[1] + t * nn[1], s * nr[2] + t * nn[2])
        m = max(math.sqrt(_dot3(n_i, n_i)), 1e-12)
        n_i = (n_i[0] / m, n_i[1] / m, n_i[2] / m)
        if _dot3(n_i, nrm[u]) < cos_n:
            return False
        ur, un, uu = uv[ref], uv[nxt], uv[u]
        e0 = abs(s * ur[0] + t * un[0] - uu[0])
        e1 = abs(s * ur[1] + t * un[1] - uu[1])
        # numpy .max() propagates NaN (and NaN > limit is False)
        if e0 == e0 and e1 == e1 and (e0 if e0 >= e1 else e1) > uv_lim[u]:
            return False
        pv = pos[v]
        for ti in v2t[u]:
            if not alive[ti]:
                continue
            tri = T[ti]
            if v in tri:
                continue
            old = [pos[tri[0]], pos[tri[1]], pos[tri[2]]]
            new = list(old)
            new[tri.index(u)] = pv
            n0 = _cross_sub(*old)
            n1 = _cross_sub(*new)
            l0, l1 = math.sqrt(_dot3(n0, n0)), math.sqrt(_dot3(n1, n1))
            if l1 < 1e-10 or (l0 > 1e-10 and _dot3(n0, n1) / (l0 * l1) < 0.5):
                return False
        plans.append((u, v))
    for u, v in plans:
        for ti in list(v2t[u]):
            if not alive[ti]:
                continue
            tri = T[ti]
            if v in tri:
                alive[ti] = False
                continue
            tri[tri.index(u)] = v
            v2t[v].add(ti)
        v2t[u] = set()
    w2v[q] = []
    return True


# ----------------------------------------------------------------------------- pass 2: interiors

def _interiors(f, bkey, wid, T, uvtol_v, p: Profile):
    nV = len(f)
    pos = f[:, 0:3]
    lab = components(nV, np.concatenate([T[:, [0, 1]], T[:, [1, 2]]]))
    tl = lab[T[:, 0]]
    order = np.argsort(tl, kind="stable")
    bounds = np.flatnonzero(np.r_[True, tl[order][1:] != tl[order][:-1], True])
    out = []
    for a, b2 in zip(bounds[:-1], bounds[1:]):
        tris = T[order[a:b2]]
        if len(tris) < p.min_tris:
            out.append(tris)
            continue
        verts = np.unique(tris)
        P = pos[verts].astype(np.float64)
        eps = _eps_for(np.sort(P.max(0) - P.min(0)), p)
        lt = np.searchsorted(verts, tris)   # verts is sorted and unique: the local ids
        tol = float(uvtol_v[verts].min())
        wts = np.array([eps / math.radians(p.normal_deg)] * 3 + [eps / (p.uv_texels * tol)] * 2, np.float32)
        A = np.c_[f[verts, 3:6], f[verts, 6:8]]
        lock = np.zeros(len(verts), np.uint8)
        tb = bkey[tris]
        lock[np.unique(lt[(tb != tb[:, :1]).any(axis=1)])] = meshopt.V_LOCK
        res = meshopt.simplify_with_attributes(lt, P, A, wts, lock, eps, meshopt.ERROR_ABSOLUTE | meshopt.LOCK_BORDER)
        out.append(verts[res] if len(res) else tris)
    return np.concatenate(out) if out else np.zeros((0, 3), np.int64)


# ----------------------------------------------------------------------------- model

def simplify_model(mdl: Path, p: Profile, uvtol_per_mesh: list[float] | None = None):
    """Returns ({mesh key: (T,3) mesh-local original vertex ids}, stats)."""
    f, bkey = vvd_data(mdl)
    meshes = mesh_arrays(mdl)
    uvtol_v = np.full(len(f), 1.0 / 1024.0)
    if uvtol_per_mesh is not None:
        for mi, (_k, st, n, _t) in enumerate(meshes):
            uvtol_v[st:st + n] = uvtol_per_mesh[mi]
    nonempty = [t + st for _k, st, n, t in meshes if len(t)]
    if not nonempty:
        return {}, {"vertices_before": 0, "vertices_after": 0, "seam_points_removed": 0}
    T = np.concatenate(nonempty).copy()
    wid = weld_ids(f[:, 0:3])
    T2, removed = _seams(f, bkey, wid, T, uvtol_v, p)
    T3 = _interiors(f, bkey, wid, T2, uvtol_v, p)
    result = {}
    # rows of a mesh: every id in [st, st+n). Same rows, same order as testing all of T3 per mesh,
    # but each mesh only looks at the rows whose smallest id falls in its range.
    lo_id, hi_id = T3.min(axis=1), T3.max(axis=1)
    by_lo = np.argsort(lo_id, kind="stable")
    lo_sorted = lo_id[by_lo]
    for key, st, n, _t in meshes:
        a, b = np.searchsorted(lo_sorted, st, "left"), np.searchsorted(lo_sorted, st + n, "left")
        idx = np.sort(by_lo[a:b])
        idx = idx[hi_id[idx] < st + n]
        result[key] = T3[idx] - st
    return result, {"vertices_before": int(len(np.unique(T))), "vertices_after": int(len(np.unique(T3))),
                    "seam_points_removed": int(removed)}
