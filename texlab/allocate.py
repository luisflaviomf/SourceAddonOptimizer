"""Screen-space rate/distortion allocation of texture resolution (texlab).

For a texture reduced by k levels, the player loses the detail bands between
mip j and j+1 for every j < k -- but only on screen pixels where the GPU would
have sampled those fine levels. Per triangle we know (from the geometry) the
level lambda(d) the GPU samples at viewing distance d and how many screen
pixels the triangle covers. The expected visible error of a choice k is

    E(k) = sum_d w(d) * sum_tri pixels(tri, d) * sum_{j = max(0, floor(lambda)) .. k-1} B_j(tri)

where B_j(tri) is the band energy (mean squared difference between mip j and
the bilinear upsampling of mip j+1) around the triangle's UV location.
Bytes(k) shrink by ~4^k. A Lagrangian sweep picks, for every texture, the k
that minimises bytes + mu * E, and mu is chosen to hit a target total error
(e.g. the error of reducing everything 2x).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .npsolve import up
from .vtf import decode_level, read_vtf

# Viewing-distance profile (Source units) and weights: walking around, inspecting, driving.
DEFAULT_DISTANCES = (24.0, 48.0, 96.0, 192.0, 384.0)
DEFAULT_WEIGHTS = (0.10, 0.20, 0.30, 0.25, 0.15)


def band_energy_maps(vtf_path, max_levels: int = 4, contrast: bool = True) -> list[np.ndarray]:
    """B_j maps (at mip j resolution, luminance-weighted RGB + alpha) for j = 0..max_levels-1."""
    v = read_vtf(vtf_path)
    levels = [decode_level(v, l).astype(np.float32) / 255.0 for l in range(min(v.mip_count, max_levels + 1))]
    maps = []
    for j in range(len(levels) - 1):
        hi, lo = levels[j], levels[j + 1]
        if hi.shape[0] != lo.shape[0] * 2 or hi.shape[1] != lo.shape[1] * 2:
            break
        u = up(lo, 2, True, True)
        d = hi - u
        e = 0.299 * d[..., 0] ** 2 + 0.587 * d[..., 1] ** 2 + 0.114 * d[..., 2] ** 2 + 0.25 * d[..., 3] ** 2
        if contrast:
            # Weber-style normalisation: the same absolute detail is far more visible on dark
            # surfaces (black meshes, carbon, rubber) than on bright paint.
            luma = 0.299 * u[..., 0] + 0.587 * u[..., 1] + 0.114 * u[..., 2]
            e = e / (luma + 0.1) ** 2 * 0.36   # equals the plain energy at mid grey (luma 0.5)
        maps.append(e)
    return maps


def _sample_map(m: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Average band energy in a small neighbourhood around each UV (wrap addressing)."""
    h, w = m.shape
    # 4x4 box blur in texel space keeps the estimate stable for small triangles
    k = 4
    pad = np.pad(m, ((k, k), (k, k)), mode="wrap")
    c = pad.cumsum(0).cumsum(1)
    blur = (c[2 * k:, 2 * k:] - c[:-2 * k, 2 * k:] - c[2 * k:, :-2 * k] + c[:-2 * k, :-2 * k]) / (4 * k * k)
    blur = blur[:h, :w]
    x = np.mod(np.floor(uv[:, 0] * w).astype(np.int64), w)
    y = np.mod(np.floor(uv[:, 1] * h).astype(np.int64), h)
    return blur[y, x]


@dataclass
class TextureCost:
    ref: str
    bytes_by_drop: list[int]               # bytes of the VTF after dropping k levels
    error_by_drop: list[float] = field(default_factory=list)


def texture_error_curve(s_eff: np.ndarray, area: np.ndarray, uv_centroids: np.ndarray, band_maps: list[np.ndarray],
                        focal: float, max_drop: int, distances=DEFAULT_DISTANCES, weights=DEFAULT_WEIGHTS) -> list[float]:
    """Expected screen error (pixel-weighted band energy) for dropping 0..max_drop levels."""
    band_at_tri = [_sample_map(m, uv_centroids) for m in band_maps]
    errors = [0.0] * (max_drop + 1)
    for d, wgt in zip(distances, weights):
        lam = np.log2(np.maximum(s_eff, 1e-12) * d / focal)
        pixels = area * (focal / d) ** 2            # facing-on upper bound of covered pixels
        start = np.maximum(np.floor(lam), 0).astype(np.int64)
        for k in range(1, max_drop + 1):
            lost = np.zeros_like(s_eff)
            for j in range(min(k, len(band_at_tri))):
                lost += np.where(start <= j, band_at_tri[j], 0.0)
            errors[k] += wgt * float((pixels * lost).sum())
    return errors


def allocate(costs: list[TextureCost], mu: float) -> dict[str, int]:
    plan = {}
    for c in costs:
        best_k, best = 0, math.inf
        for k, (b, e) in enumerate(zip(c.bytes_by_drop, c.error_by_drop)):
            score = b + mu * e
            if score < best:
                best, best_k = score, k
        plan[c.ref] = best_k
    return plan


def totals(costs: list[TextureCost], plan: dict[str, int]) -> tuple[int, float]:
    b = sum(c.bytes_by_drop[plan[c.ref]] for c in costs)
    e = sum(c.error_by_drop[plan[c.ref]] for c in costs)
    return b, e


def solve_for_error(costs: list[TextureCost], target_error: float) -> tuple[float, dict[str, int]]:
    """Smallest-bytes plan whose total error does not exceed target_error (bisection on mu)."""
    lo, hi = 1e-6, 1e12
    best_plan = allocate(costs, hi)
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        plan = allocate(costs, mid)
        _, e = totals(costs, plan)
        if e <= target_error:
            best_plan, hi = plan, mid
        else:
            lo = mid
    return hi, best_plan


def texture_local_error(s_eff: np.ndarray, area: np.ndarray, uv_centroids: np.ndarray, band_maps: list[np.ndarray],
                        focal: float, max_drop: int, distances=DEFAULT_DISTANCES, weights=DEFAULT_WEIGHTS,
                        quantile: float = 0.9) -> list[float]:
    """Pixel-weighted `quantile` of the per-triangle visible detail loss for dropping 0..max_drop levels.

    Unlike the global sum, this does not shrink for textures that cover few pixels: a blurred
    logo counts as much as a blurred door, which matches how players judge quality.
    """
    band_at_tri = [_sample_map(m, uv_centroids) for m in band_maps]
    out = [0.0]
    for k in range(1, max_drop + 1):
        losses, wts = [], []
        for d, wgt in zip(distances, weights):
            lam = np.log2(np.maximum(s_eff, 1e-12) * d / focal)
            start = np.maximum(np.floor(lam), 0).astype(np.int64)
            lost = np.zeros_like(s_eff)
            for j in range(min(k, len(band_at_tri))):
                lost += np.where(start <= j, band_at_tri[j], 0.0)
            losses.append(lost)
            wts.append(wgt * area * (focal / d) ** 2)
        loss = np.concatenate(losses)
        wt = np.concatenate(wts)
        order = np.argsort(loss)
        cum = np.cumsum(wt[order])
        if not len(cum) or cum[-1] <= 0:
            out.append(0.0)
            continue
        idx = int(np.searchsorted(cum, quantile * cum[-1]))
        out.append(float(loss[order][min(idx, len(order) - 1)]))
    return out
