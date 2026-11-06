"""Pure-numpy reconstruction-aware downscaling (worker-safe: no torch, no numba).

Same mathematics as texlab.downscale.solve_factor: find L (h, w, C) minimising

    alpha/f^2 * || U_f(L) - H ||^2 + beta * || L - Mk ||^2

where U_f is the exact GPU bilinear magnification by an integer factor f
(texel-centre convention, wrap or clamp addressing), solved with conjugate
gradients. U_f is separable and periodic with period f, so it is applied with
shifted copies instead of dense matrices.
"""
from __future__ import annotations

import math

import numpy as np


def _phases(f: int):
    """For fine sample phase r: coarse offset o_r and weight t_r of the second tap."""
    out = []
    for r in range(f):
        p = (r + 0.5) / f - 0.5
        o = math.floor(p)
        out.append((o, p - o))
    return out


def _shift(x: np.ndarray, k: int, axis: int, wrap: bool) -> np.ndarray:
    """y[i] = x[i + k] along axis (wrap or clamp)."""
    if k == 0:
        return x
    if wrap:
        return np.roll(x, -k, axis=axis)
    n = x.shape[axis]
    idx = np.clip(np.arange(n) + k, 0, n - 1)
    return np.take(x, idx, axis=axis)


def up_axis(x: np.ndarray, axis: int, f: int, wrap: bool) -> np.ndarray:
    parts = []
    for o, t in _phases(f):
        a = _shift(x, o, axis, wrap)
        b = _shift(x, o + 1, axis, wrap)
        parts.append((1.0 - t) * a + t * b)
    stacked = np.stack(parts, axis=axis + 1)
    shape = list(x.shape)
    shape[axis] *= f
    return stacked.reshape(shape)


def _shift_t(y: np.ndarray, k: int, axis: int, wrap: bool) -> np.ndarray:
    """Adjoint of _shift: z[j] = sum over i with clamp(i + k) == j of y[i]."""
    if k == 0:
        return y
    if wrap:
        return np.roll(y, k, axis=axis)
    n = y.shape[axis]
    idx = np.clip(np.arange(n) + k, 0, n - 1)
    out = np.zeros_like(y)
    moved = np.moveaxis(out, axis, 0)
    src = np.moveaxis(y, axis, 0)
    np.add.at(moved, idx, src)
    return out


def up_axis_t(y: np.ndarray, axis: int, f: int, wrap: bool) -> np.ndarray:
    shape = list(y.shape)
    n = shape[axis] // f
    split = y.reshape(shape[:axis] + [n, f] + shape[axis + 1:])
    out = None
    for r, (o, t) in enumerate(_phases(f)):
        yr = np.take(split, r, axis=axis + 1)
        contrib = (1.0 - t) * _shift_t(yr, o, axis, wrap) + t * _shift_t(yr, o + 1, axis, wrap)
        out = contrib if out is None else out + contrib
    return out


def up(x: np.ndarray, f: int, wrap_s: bool, wrap_t: bool) -> np.ndarray:
    """(h, w, C) -> (h*f, w*f, C)."""
    return up_axis(up_axis(x, 1, f, wrap_s), 0, f, wrap_t)


def up_t(y: np.ndarray, f: int, wrap_s: bool, wrap_t: bool) -> np.ndarray:
    return up_axis_t(up_axis_t(y, 0, f, wrap_t), 1, f, wrap_s)


def solve(H: np.ndarray, Mk: np.ndarray, factor: int, alpha: float = 4.0, beta: float = 1.0,
          wrap_s: bool = True, wrap_t: bool = True, iters: int = 40, tol: float = 1e-10,
          clamp_local: bool = True) -> np.ndarray:
    """alpha/beta may be scalars or per-texel (h, w) maps at the reduced resolution."""
    H = np.asarray(H, np.float32)
    m = np.asarray(Mk, np.float32)
    a_map = np.maximum(np.asarray(alpha, np.float32), 0.0)        # weights must keep A positive definite
    b_map = np.maximum(np.asarray(beta, np.float32), 1e-3)
    if a_map.ndim == 2:
        a_fine = np.repeat(np.repeat(a_map, factor, axis=0), factor, axis=1)[..., None]
    else:
        a_fine = a_map
    if b_map.ndim == 2:
        b_map = b_map[..., None]
    scale = 1.0 / (factor * factor)

    def A(x):
        return scale * up_t(a_fine * up(x, factor, wrap_s, wrap_t), factor, wrap_s, wrap_t) + b_map * x

    b = scale * up_t(a_fine * H, factor, wrap_s, wrap_t) + b_map * m
    x = m.copy()
    r = b - A(x)
    p = r.copy()
    rs = float((r * r).sum())
    for _ in range(iters):
        if rs < tol:
            break
        Ap = A(p)
        a = rs / max(float((p * Ap).sum()), 1e-20)
        x += a * p
        r -= a * Ap
        rs_new = float((r * r).sum())
        p = r + (rs_new / rs) * p
        rs = rs_new
    if clamp_local:
        lo, hi = local_range(H, factor, wrap_s, wrap_t)
        x = np.minimum(np.maximum(x, lo), hi)
    return np.clip(x, 0.0, 1.0)


def local_range(H: np.ndarray, factor: int, wrap_s: bool, wrap_t: bool):
    """Per coarse texel: min/max of the source over its footprint and the 8 neighbouring footprints.

    Least-squares reconstruction overshoots at very strong edges (bright halos next to dark
    shapes, alpha fringes). Limiting each new texel to colours that really occur around it
    removes the halo while keeping the sharpening inside the edge.
    """
    h, w, c = H.shape
    blocks = H.reshape(h // factor, factor, w // factor, factor, c)
    lo = blocks.min(axis=(1, 3))
    hi = blocks.max(axis=(1, 3))
    out_lo, out_hi = lo.copy(), hi.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            sl = _shift(_shift(lo, dy, 0, wrap_t), dx, 1, wrap_s)
            sh = _shift(_shift(hi, dy, 0, wrap_t), dx, 1, wrap_s)
            out_lo = np.minimum(out_lo, sl)
            out_hi = np.maximum(out_hi, sh)
    return out_lo, out_hi
