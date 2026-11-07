"""Fixed-radius neighbour search on a uniform grid (numpy only; the worker does not ship scipy)."""
from __future__ import annotations

import itertools

import numpy as np

_CHUNK = 65536


def radius_pairs(points: np.ndarray, queries: np.ndarray, r: float, k: int | None = None):
    """Every (query, point) pair closer than r (inclusive), sorted by query, then distance, then
    point index, so the order never depends on hashing or threads. With k, only the k nearest of
    each query are kept. Returns (qi, pj, dist, rank)."""
    P = np.asarray(points, np.float64)
    Q = np.asarray(queries, np.float64)
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64))
    if len(P) == 0 or len(Q) == 0 or r <= 0:
        return empty
    d = P.shape[1]
    lo = np.minimum(P.min(0), Q.min(0))
    pc = np.floor((P - lo) / r).astype(np.int64) + 1
    qc = np.floor((Q - lo) / r).astype(np.int64) + 1
    dims = np.maximum(pc.max(0), qc.max(0)) + 2
    mult = np.ones(d, np.int64)
    for i in range(d - 2, -1, -1):
        mult[i] = mult[i + 1] * dims[i + 1]
    pkey = pc @ mult
    order = np.argsort(pkey, kind="stable")
    sk = pkey[order]
    offsets = [np.array(o, np.int64) for o in itertools.product((-1, 0, 1), repeat=d)]
    out_q, out_p, out_d = [], [], []
    for c0 in range(0, len(Q), _CHUNK):
        qcc = qc[c0:c0 + _CHUNK]
        QQ = Q[c0:c0 + _CHUNK]
        qs, ps = [], []
        for off in offsets:
            key = (qcc + off) @ mult
            a = np.searchsorted(sk, key, "left")
            cnt = np.searchsorted(sk, key, "right") - a
            tot = int(cnt.sum())
            if not tot:
                continue
            qi = np.repeat(np.arange(len(qcc)), cnt)
            within = np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            qs.append(qi)
            ps.append(order[np.repeat(a, cnt) + within])
        if not qs:
            continue
        qi = np.concatenate(qs)
        pj = np.concatenate(ps)
        dist = np.sqrt(((P[pj] - QQ[qi]) ** 2).sum(1))
        keep = dist <= r
        out_q.append(qi[keep] + c0)
        out_p.append(pj[keep])
        out_d.append(dist[keep])
    if not out_q:
        return empty
    qi = np.concatenate(out_q)
    pj = np.concatenate(out_p)
    dist = np.concatenate(out_d)
    o = np.lexsort((pj, dist, qi))
    qi, pj, dist = qi[o], pj[o], dist[o]
    start = np.r_[0, np.flatnonzero(qi[1:] != qi[:-1]) + 1]
    rank = np.arange(len(qi)) - np.repeat(start, np.diff(np.r_[start, len(qi)]))
    if k is not None:
        keep = rank < k
        qi, pj, dist, rank = qi[keep], pj[keep], dist[keep], rank[keep]
    return qi, pj, dist, rank
