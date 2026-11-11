"""Loader for the Rust hot paths (`gmo_native.dll`, source in native/gmo_native).

Used by texlab (textures) and model_screen (models).

Every native routine returns exactly the same bytes as the numpy code it replaces, which
stays as the reference and as the fallback when the DLL is missing or disabled
(environment variable GMO_NATIVE=0). ctypes releases the GIL during the calls, so worker
threads decode in parallel.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

import numpy as np

ABI_VERSION = 7
DLL_NAME = "gmo_native.dll"

_lock = threading.Lock()
_lib = None
_loaded = False


def _candidates():
    if getattr(sys, "frozen", False):
        yield Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / DLL_NAME
        yield Path(sys.executable).parent / DLL_NAME
    yield Path(__file__).resolve().parents[1] / "tools" / "native" / DLL_NAME


def lib():
    """The loaded DLL, or None (missing, disabled, or a different ABI version)."""
    global _lib, _loaded
    if _loaded:
        return _lib
    with _lock:
        if _loaded:
            return _lib
        if os.environ.get("GMO_NATIVE", "1") != "0":
            for path in _candidates():
                if not path.is_file():
                    continue
                try:
                    dll = ctypes.CDLL(str(path))
                    dll.gmo_native_abi_version.restype = ctypes.c_uint32
                    if dll.gmo_native_abi_version() != ABI_VERSION:
                        continue
                    dll.gmo_decode_bc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32,
                                                  ctypes.c_int32, ctypes.c_void_p]
                    dll.gmo_decode_bc.restype = ctypes.c_int32
                    dll.gmo_band_energy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                                    ctypes.c_int32, ctypes.c_void_p]
                    dll.gmo_band_energy.restype = ctypes.c_int32
                    dll.gmo_sample_map.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                                   ctypes.c_size_t, ctypes.c_void_p]
                    dll.gmo_sample_map.restype = ctypes.c_int32
                    vp, sz = ctypes.c_void_p, ctypes.c_size_t
                    dll.gmo_seams.argtypes = [sz, vp, vp, vp, vp, vp, vp, sz, vp, vp, vp, sz, vp, vp,
                                              ctypes.c_double, ctypes.c_double, ctypes.c_double, vp]
                    dll.gmo_seams.restype = ctypes.c_int32
                    dll.gmo_pyset_order.argtypes = [vp, sz, vp]
                    dll.gmo_pyset_order.restype = ctypes.c_int64
                    dll.gmo_components.argtypes = [sz, vp, sz, vp]
                    dll.gmo_components.restype = ctypes.c_int32
                    u32 = ctypes.c_uint32
                    dll.gmo_ts_recon.argtypes = [vp, sz, u32, u32, u32, ctypes.c_int64, u32, vp]
                    dll.gmo_ts_recon.restype = ctypes.c_int32
                    dll.gmo_ts_dwt.argtypes = [vp, u32, u32, u32, vp]
                    dll.gmo_ts_dwt.restype = ctypes.c_int32
                    dll.gmo_ts_put_band.argtypes = [vp, u32, u32, ctypes.c_double, u32, vp, sz]
                    dll.gmo_ts_put_band.restype = ctypes.c_ssize_t
                    dll.gmo_ts_ycocg.argtypes = [vp, sz, sz, vp, vp, vp]
                    dll.gmo_ts_ycocg.restype = ctypes.c_int32
                    dll.gmo_ts_unit_normals.argtypes = [vp, sz, sz, vp]
                    dll.gmo_ts_unit_normals.restype = ctypes.c_int32
                    dll.gmo_ts_sqerr_ycocg.argtypes = [vp, vp, vp, vp, sz, vp]
                    dll.gmo_ts_sqerr_ycocg.restype = ctypes.c_int32
                    dll.gmo_ts_sqerr_plane.argtypes = [vp, vp, sz, vp]
                    dll.gmo_ts_sqerr_plane.restype = ctypes.c_int32
                    dll.gmo_ts_sqerr_normal.argtypes = [vp, vp, vp, sz, vp]
                    dll.gmo_ts_sqerr_normal.restype = ctypes.c_int32
                    dll.gmo_ts_mse_ycocg.argtypes = [vp, vp, vp, vp, sz, vp]
                    dll.gmo_ts_mse_ycocg.restype = ctypes.c_int32
                    dll.gmo_ts_mse_plane.argtypes = [vp, vp, sz, vp]
                    dll.gmo_ts_mse_plane.restype = ctypes.c_int32
                    dll.gmo_ts_mse_normal.argtypes = [vp, vp, vp, sz, vp]
                    dll.gmo_ts_mse_normal.restype = ctypes.c_int32
                    _lib = dll
                    break
                except (OSError, AttributeError):
                    continue
        _loaded = True
    return _lib


def available() -> bool:
    return lib() is not None


def decode_bc(data: bytes, w: int, h: int, fmt: int) -> np.ndarray | None:
    """(h, w, 4) uint8 RGBA, or None when the native path cannot handle it (caller falls back)."""
    dll = lib()
    if dll is None or w <= 0 or h <= 0:
        return None
    src = np.frombuffer(data, dtype=np.uint8)
    out = np.empty((h, w, 4), np.uint8)
    rc = dll.gmo_decode_bc(src.ctypes.data if len(src) else None, len(src), w, h, fmt, out.ctypes.data)
    return out if rc == 0 else None


def band_energy(hi: np.ndarray, lo: np.ndarray, contrast: bool = True) -> np.ndarray | None:
    """Band energy map of mip j (hi, uint8 (2h, 2w, 4)) against mip j+1 (lo, uint8 (h, w, 4)),
    identical to the float32 numpy computation in texlab.allocate.band_energy_maps."""
    dll = lib()
    if dll is None:
        return None
    hl, wl = lo.shape[0], lo.shape[1]
    if hi.shape != (2 * hl, 2 * wl, 4) or lo.shape != (hl, wl, 4) or hi.dtype != np.uint8 or lo.dtype != np.uint8:
        return None
    hi = np.ascontiguousarray(hi)
    lo = np.ascontiguousarray(lo)
    out = np.empty((2 * hl, 2 * wl), np.float32)
    rc = dll.gmo_band_energy(hi.ctypes.data, lo.ctypes.data, hl, wl, 1 if contrast else 0, out.ctypes.data)
    return out if rc == 0 else None


def sample_map(m: np.ndarray, uv: np.ndarray) -> np.ndarray | None:
    """texlab.allocate._sample_map (wrapped 8x8 box blur of a float32 map at UV centroids)."""
    dll = lib()
    if dll is None or m.ndim != 2 or m.dtype != np.float32 or m.shape[0] == 0 or m.shape[1] == 0:
        return None
    uv = np.ascontiguousarray(uv, dtype=np.float64)
    if uv.ndim != 2 or uv.shape[1] != 2:
        return None
    m = np.ascontiguousarray(m)
    out = np.empty(len(uv), np.float32)
    rc = dll.gmo_sample_map(m.ctypes.data, m.shape[0], m.shape[1], uv.ctypes.data if len(uv) else None, len(uv),
                            out.ctypes.data if len(uv) else None)
    return out if rc == 0 else None


def seams(pos, nrm, uv, wid, bkey, uv_lim, pw, comp, comp_eps, T, cos_n: float, floor: float, rel_size: float):
    """model_screen.simplify._seams core (pass 1). Returns (new T, alive bool mask, removed) or
    None when the native path is unavailable or refuses the input (caller runs the Python code).
    T is not modified (the DLL works on a copy)."""
    dll = lib()
    if dll is None or len(T) == 0 or len(wid) == 0 or len(comp) == 0:
        return None
    f64 = lambda a: np.ascontiguousarray(a, dtype=np.float64)
    i64 = lambda a: np.ascontiguousarray(a, dtype=np.int64)
    pos, nrm, uv, uv_lim, pw, comp_eps = f64(pos), f64(nrm), f64(uv), f64(uv_lim), f64(pw), f64(comp_eps)
    wid, bkey, comp = i64(wid), i64(bkey), i64(comp)
    tris = np.array(T, dtype=np.int64, order="C", copy=True)
    n_v, n_w = len(wid), len(comp)
    if (pos.shape != (n_v, 3) or nrm.shape != (n_v, 3) or uv.shape != (n_v, 2) or bkey.shape != (n_v,)
            or uv_lim.shape != (n_v,) or pw.shape != (n_w, 3) or comp_eps.shape != (n_w,) or tris.ndim != 2
            or tris.shape[1] != 3):
        return None
    alive = np.zeros(len(tris), np.uint8)
    removed = ctypes.c_int64(0)
    rc = dll.gmo_seams(n_v, pos.ctypes.data, nrm.ctypes.data, uv.ctypes.data, wid.ctypes.data, bkey.ctypes.data,
                       uv_lim.ctypes.data, n_w, pw.ctypes.data, comp.ctypes.data, comp_eps.ctypes.data, len(tris),
                       tris.ctypes.data, alive.ctypes.data, float(cos_n), float(floor), float(rel_size),
                       ctypes.byref(removed))
    if rc != 0:
        return None
    return tris, alive.astype(bool), int(removed.value)


def pyset_order(keys) -> list[int] | None:
    """Iteration order of the DLL's CPython-set emulation after inserting `keys` (test hook)."""
    dll = lib()
    if dll is None:
        return None
    k = np.ascontiguousarray(keys, dtype=np.int64)
    out = np.zeros(max(1, len(k)), np.int64)
    n = dll.gmo_pyset_order(k.ctypes.data if len(k) else None, len(k), out.ctypes.data)
    return None if n < 0 else out[:n].tolist()


def components(n: int, edges: np.ndarray) -> np.ndarray | None:
    """model_screen.simplify.components: int64 label per node = smallest node index of its
    connected component. None when unavailable or an edge is out of range."""
    dll = lib()
    if dll is None or n <= 0:
        return None
    e = np.ascontiguousarray(edges, dtype=np.int64)
    if e.ndim != 2 or e.shape[1] != 2:
        return None
    out = np.empty(n, np.int64)
    rc = dll.gmo_components(n, e.ctypes.data if len(e) else None, len(e), out.ctypes.data)
    return out if rc == 0 else None


# ----------------------------------------------------------------------------- texture stream step search
def _f64c(a) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float64)


def ts_recon(coef: np.ndarray, h: int, w: int, levels: int, step: int, drop: int) -> np.ndarray | None:
    """texture_stream.choose._recon from the flat coefficients of a plane (LL, then LH, HL, HH of each level,
    coarse to fine). (h, w) float64, or None when the DLL is not there."""
    dll = lib()
    if dll is None:
        return None
    coef = _f64c(coef)
    out = np.empty((h, w), np.float64)
    rc = dll.gmo_ts_recon(coef.ctypes.data, coef.size, h, w, levels, int(step), drop, out.ctypes.data)
    return out if rc == 0 else None


def ts_dwt(plane: np.ndarray, levels: int):
    """texture_stream.codec.dwt: (LL, [(LH, HL, HH) per level, coarse to fine]) as views of one flat array
    in the layout ts_recon reads; None when the DLL is not there."""
    dll = lib()
    if dll is None or plane.ndim != 2:
        return None
    plane = _f64c(plane)
    h, w = plane.shape
    flat = np.empty(h * w, np.float64)
    if dll.gmo_ts_dwt(plane.ctypes.data, h, w, levels, flat.ctypes.data) != 0:
        return None
    lh, lw = h >> levels, w >> levels
    pos = lh * lw
    ll = flat[:pos].reshape(lh, lw)
    bands = []
    for level in range(levels):
        bh, bw = h >> (levels - level), w >> (levels - level)
        n = bh * bw
        bands.append(tuple(flat[pos + k * n:pos + (k + 1) * n].reshape(bh, bw) for k in range(3)))
        pos += 3 * n
    return ll, bands


def ts_put_band(band: np.ndarray, st: float, noise: bool) -> bytes | None:
    """The bytes codec.put_band(out, codec.quant(band, st)) appends, or those of codec.put_band_nf with the
    band's noise map (noise=True). None when the DLL is not there or the Python code has to speak (a
    coefficient out of range raises there)."""
    dll = lib()
    if dll is None or band.ndim != 2 or band.dtype != np.float64 or not band.size:
        return None
    band = _f64c(band)
    h, w = band.shape
    cap = 1 + max(1, h // 8) * max(1, w // 8) + 2 * h * w
    out = np.empty(cap, np.uint8)
    n = dll.gmo_ts_put_band(band.ctypes.data, h, w, float(st), 1 if noise else 0, out.ctypes.data, cap)
    return out[:n].tobytes() if n > 0 else None


def _pixels(rgb: np.ndarray):
    """(array to keep alive, address, pixel stride in doubles) of an (h, w, >= 3) float64 image whose pixels
    sit at a regular distance (a contiguous image or its [..., :3] view); copies anything else."""
    if rgb.dtype != np.float64 or rgb.ndim != 3 or rgb.shape[2] < 3:
        return None
    s = rgb.strides
    if not (s[2] == 8 and s[1] >= 24 and s[1] % 8 == 0 and s[0] == rgb.shape[1] * s[1]):
        rgb = np.ascontiguousarray(rgb[..., :3])
        s = rgb.strides
    return rgb, rgb.ctypes.data, s[1] // 8


def ts_ycocg(rgb: np.ndarray):
    """codec.rgb_to_ycocg: [Y, Co, Cg] planes of an (h, w, >= 3) float64 image; None without the DLL."""
    dll = lib()
    px = _pixels(rgb) if dll is not None else None
    if px is None or not rgb.size:
        return None
    keep, address, stride = px
    h, w = rgb.shape[:2]
    planes = [np.empty((h, w), np.float64) for _ in range(3)]
    rc = dll.gmo_ts_ycocg(address, h * w, stride, *[p.ctypes.data for p in planes])
    return planes if rc == 0 else None


def ts_unit_normals(rgb: np.ndarray) -> np.ndarray | None:
    """codec.unit_normals of an (h, w, 3) float64 image (or the [..., :3] view of a wider one)."""
    dll = lib()
    px = _pixels(rgb) if dll is not None else None
    if px is None or not rgb.size or rgb.shape[2] != 3:
        return None
    keep, address, stride = px
    h, w = rgb.shape[:2]
    out = np.empty((h, w, 3), np.float64)
    rc = dll.gmo_ts_unit_normals(address, h * w, stride, out.ctypes.data)
    return out if rc == 0 else None


def ts_sqerr_ycocg(y: np.ndarray, co: np.ndarray, cg: np.ndarray, reference: np.ndarray) -> np.ndarray | None:
    """(np.clip(np.round(ycocg_to_rgb(y, co, cg)), 0, 255) - reference) ** 2, shape (h, w, 3)."""
    dll = lib()
    if dll is None:
        return None
    y, co, cg, reference = _f64c(y), _f64c(co), _f64c(cg), _f64c(reference)
    if reference.shape != y.shape + (3,) or co.shape != y.shape or cg.shape != y.shape:
        return None
    out = np.empty(reference.shape, np.float64)
    rc = dll.gmo_ts_sqerr_ycocg(y.ctypes.data, co.ctypes.data, cg.ctypes.data, reference.ctypes.data, y.size,
                                out.ctypes.data)
    return out if rc == 0 else None


def ts_sqerr_plane(plane: np.ndarray, reference: np.ndarray) -> np.ndarray | None:
    """(np.clip(np.round(plane + 128), 0, 255) - reference) ** 2, same shape as plane."""
    dll = lib()
    if dll is None:
        return None
    plane, reference = _f64c(plane), _f64c(reference)
    if reference.shape != plane.shape:
        return None
    out = np.empty(plane.shape, np.float64)
    rc = dll.gmo_ts_sqerr_plane(plane.ctypes.data, reference.ctypes.data, plane.size, out.ctypes.data)
    return out if rc == 0 else None


def ts_sqerr_normal(x: np.ndarray, y: np.ndarray, reference: np.ndarray) -> np.ndarray | None:
    """(unit_normals(stack([px, py, normal_z(px, py)])) - reference) ** 2 with px = clip(x + 128, 0, 255)."""
    dll = lib()
    if dll is None:
        return None
    x, y, reference = _f64c(x), _f64c(y), _f64c(reference)
    if reference.shape != x.shape + (3,) or y.shape != x.shape:
        return None
    out = np.empty(reference.shape, np.float64)
    rc = dll.gmo_ts_sqerr_normal(x.ctypes.data, y.ctypes.data, reference.ctypes.data, x.size, out.ctypes.data)
    return out if rc == 0 else None


def ts_mse_ycocg(y: np.ndarray, co: np.ndarray, cg: np.ndarray, reference: np.ndarray):
    """np.mean of ts_sqerr_ycocg's array (the same float64, by numpy's own summation order) without building
    the array; None when the DLL is not there."""
    dll = lib()
    if dll is None:
        return None
    y, co, cg, reference = _f64c(y), _f64c(co), _f64c(cg), _f64c(reference)
    if reference.shape != y.shape + (3,) or co.shape != y.shape or cg.shape != y.shape:
        return None
    out = ctypes.c_double()
    rc = dll.gmo_ts_mse_ycocg(y.ctypes.data, co.ctypes.data, cg.ctypes.data, reference.ctypes.data, y.size,
                              ctypes.byref(out))
    return np.float64(out.value) if rc == 0 else None


def ts_mse_plane(plane: np.ndarray, reference: np.ndarray):
    """np.mean of ts_sqerr_plane's array."""
    dll = lib()
    if dll is None:
        return None
    plane, reference = _f64c(plane), _f64c(reference)
    if reference.shape != plane.shape:
        return None
    out = ctypes.c_double()
    rc = dll.gmo_ts_mse_plane(plane.ctypes.data, reference.ctypes.data, plane.size, ctypes.byref(out))
    return np.float64(out.value) if rc == 0 else None


def ts_mse_normal(x: np.ndarray, y: np.ndarray, reference: np.ndarray):
    """np.mean of ts_sqerr_normal's array."""
    dll = lib()
    if dll is None:
        return None
    x, y, reference = _f64c(x), _f64c(y), _f64c(reference)
    if reference.shape != x.shape + (3,) or y.shape != x.shape:
        return None
    out = ctypes.c_double()
    rc = dll.gmo_ts_mse_normal(x.ctypes.data, y.ctypes.data, reference.ctypes.data, x.size, ctypes.byref(out))
    return np.float64(out.value) if rc == 0 else None
