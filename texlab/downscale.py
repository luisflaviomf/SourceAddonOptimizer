"""Reconstruction-aware 2x texture downscaling (texlab).

A reduced texture L is seen in two regimes:
  * magnified (close views): the GPU shows the bilinear reconstruction of L,
    which should match the original H;
  * minified (far views): the GPU shows L itself where the original showed
    its first mip M1.
We pick the L that minimises

    alpha * || U(L) - H ||^2  +  beta * || L - M1 ||^2

where U is the exact GPU bilinear 2x magnification (texel-centre convention,
wrap or clamp addressing). The problem is a small SPD linear system solved
with conjugate gradients on the GPU.
"""
from __future__ import annotations

import numpy as np
import torch

_DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _shift(x: torch.Tensor, k: int, dim: int, wrap: bool) -> torch.Tensor:
    """y[i] = x[i + k] along dim, with wrap or clamp-to-edge addressing."""
    if wrap:
        return torch.roll(x, shifts=-k, dims=dim)
    n = x.shape[dim]
    idx = torch.clamp(torch.arange(n, device=x.device) + k, 0, n - 1)
    return x.index_select(dim, idx)


def _up1d(x: torch.Tensor, dim: int, wrap: bool) -> torch.Tensor:
    prev, nxt = _shift(x, -1, dim, wrap), _shift(x, 1, dim, wrap)
    even = 0.25 * prev + 0.75 * x
    odd = 0.75 * x + 0.25 * nxt
    out = torch.stack([even, odd], dim=dim + 1)
    shape = list(x.shape)
    shape[dim] *= 2
    return out.reshape(shape)


def _up1d_t(y: torch.Tensor, dim: int, wrap: bool) -> torch.Tensor:
    """Adjoint of _up1d."""
    shape = list(y.shape)
    n = shape[dim] // 2
    split = y.reshape(shape[:dim] + [n, 2] + shape[dim + 1:])
    even = split.select(dim + 1, 0)
    odd = split.select(dim + 1, 1)
    out = 0.75 * even + 0.75 * odd
    if wrap:
        out = out + 0.25 * _shift(even, 1, dim, True) + 0.25 * _shift(odd, -1, dim, True)
    else:
        # clamp addressing: contributions that clamp at the borders fold back onto the edge texel
        e_next = torch.cat([even.narrow(dim, 1, n - 1), torch.zeros_like(even.narrow(dim, 0, 1))], dim=dim)
        o_prev = torch.cat([torch.zeros_like(odd.narrow(dim, 0, 1)), odd.narrow(dim, 0, n - 1)], dim=dim)
        out = out + 0.25 * e_next + 0.25 * o_prev
        first = [slice(None)] * out.dim(); first[dim] = 0
        last = [slice(None)] * out.dim(); last[dim] = n - 1
        out[tuple(first)] += 0.25 * even.select(dim, 0)
        out[tuple(last)] += 0.25 * odd.select(dim, n - 1)
    return out


def up2(L: torch.Tensor, wrap_s: bool = True, wrap_t: bool = True) -> torch.Tensor:
    """(C,h,w) -> (C,2h,2w) GPU bilinear magnification sampled at the fine texel centres."""
    return _up1d(_up1d(L, 2, wrap_s), 1, wrap_t)


def up2_t(Y: torch.Tensor, wrap_s: bool = True, wrap_t: bool = True) -> torch.Tensor:
    return _up1d_t(_up1d_t(Y, 1, wrap_t), 2, wrap_s)


def solve(H: np.ndarray, M1: np.ndarray, alpha: float = 1.0, beta: float = 1.0,
          wrap_s: bool = True, wrap_t: bool = True, iters: int = 40) -> np.ndarray:
    """H: (2h,2w,C) float in [0,1]; M1: (h,w,C) initial/minification target. Returns (h,w,C)."""
    h_t = torch.from_numpy(np.ascontiguousarray(H.transpose(2, 0, 1))).float().to(_DEV)
    m_t = torch.from_numpy(np.ascontiguousarray(M1.transpose(2, 0, 1))).float().to(_DEV)

    def A(x):
        return alpha * up2_t(up2(x, wrap_s, wrap_t), wrap_s, wrap_t) + beta * x

    b = alpha * up2_t(h_t, wrap_s, wrap_t) + beta * m_t
    x = m_t.clone()
    r = b - A(x)
    p = r.clone()
    rs = (r * r).sum()
    for _ in range(iters):
        Ap = A(p)
        a = rs / (p * Ap).sum().clamp_min(1e-20)
        x = x + a * p
        r = r - a * Ap
        rs_new = (r * r).sum()
        if rs_new < 1e-10:
            break
        p = r + (rs_new / rs) * p
        rs = rs_new
    x = x.clamp(0.0, 1.0)
    return x.permute(1, 2, 0).cpu().numpy()


def check_adjoint(seed: int = 0) -> float:
    g = torch.Generator().manual_seed(seed)
    for wrap in (True, False):
        x = torch.rand((2, 6, 10), generator=g).to(_DEV)
        y = torch.rand((2, 12, 20), generator=g).to(_DEV)
        lhs = (up2(x, wrap, wrap) * y).sum()
        rhs = (x * up2_t(y, wrap, wrap)).sum()
        err = float(abs(lhs - rhs) / abs(lhs))
        if err > 1e-5:
            return err
    return 0.0


# ----------------------------------------------------------------------------- any power-of-two factor

def _interp_matrix(n_low: int, factor: int, wrap: bool) -> torch.Tensor:
    """(n_low*factor, n_low) GPU bilinear magnification: fine texel x samples low at (x+0.5)/f - 0.5."""
    n_high = n_low * factor
    m = torch.zeros((n_high, n_low), dtype=torch.float32)
    for x in range(n_high):
        p = (x + 0.5) / factor - 0.5
        i0 = int(np.floor(p))
        t = p - i0
        for i, wgt in ((i0, 1.0 - t), (i0 + 1, t)):
            if wgt == 0.0:
                continue
            j = i % n_low if wrap else min(max(i, 0), n_low - 1)
            m[x, j] += wgt
    return m.to(_DEV)


def solve_factor(H: np.ndarray, Mk: np.ndarray, factor: int, alpha: float = 1.0, beta: float = 1.0,
                 wrap_s: bool = True, wrap_t: bool = True, iters: int = 60) -> np.ndarray:
    """Like solve() for any power-of-two factor. H: (h*f, w*f, C), Mk: (h, w, C) original mip k."""
    h, w = Mk.shape[:2]
    my = _interp_matrix(h, factor, wrap_t)          # (h*f, h)
    mx = _interp_matrix(w, factor, wrap_s)          # (w*f, w)
    h_t = torch.from_numpy(np.ascontiguousarray(H.transpose(2, 0, 1))).float().to(_DEV)
    m_t = torch.from_numpy(np.ascontiguousarray(Mk.transpose(2, 0, 1))).float().to(_DEV)
    U = lambda x: my @ x @ mx.T
    Ut = lambda y: my.T @ y @ mx
    # magnification error is measured per fine texel; normalise so alpha/beta weigh per area
    scale = 1.0 / (factor * factor)
    A = lambda x: alpha * scale * Ut(U(x)) + beta * x
    b = alpha * scale * Ut(h_t) + beta * m_t
    x = m_t.clone()
    r = b - A(x)
    p = r.clone()
    rs = (r * r).sum()
    for _ in range(iters):
        Ap = A(p)
        a = rs / (p * Ap).sum().clamp_min(1e-20)
        x = x + a * p
        r = r - a * Ap
        rs_new = (r * r).sum()
        if rs_new < 1e-12:
            break
        p = r + (rs_new / rs) * p
        rs = rs_new
    return x.clamp(0.0, 1.0).permute(1, 2, 0).cpu().numpy()
