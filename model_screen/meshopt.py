"""ctypes binding to meshoptimizer v1.2 (tools/meshopt/meshopt_simplify.dll)."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import numpy as np

ERROR_ABSOLUTE = 1 << 2
LOCK_BORDER = 1 << 0
PRUNE = 1 << 3
PERMISSIVE = 1 << 5
V_LOCK = 1 << 0
V_PROTECT = 1 << 1

_lib = None


def _dll_path() -> Path:
    names = ("meshopt_simplify.dll",)
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        roots.append(Path(sys.executable).resolve().parent)
    repo = Path(__file__).resolve().parents[1]
    roots += [repo / "tools" / "meshopt", repo]
    for root in roots:
        for name in names:
            p = root / name
            if p.is_file():
                return p
    raise FileNotFoundError("meshopt_simplify.dll not found (tools/meshopt)")


def _load():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(str(_dll_path()))
        u32p, f32p, u8p, sz = (ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_float),
                               ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t)
        lib.meshopt_simplifyWithAttributes.restype = sz
        lib.meshopt_simplifyWithAttributes.argtypes = [u32p, u32p, sz, f32p, sz, sz, f32p, sz, f32p, sz, u8p, sz,
                                                       ctypes.c_float, ctypes.c_uint, f32p]
        _lib = lib
    return _lib


def simplify_with_attributes(tris: np.ndarray, pos: np.ndarray, attrs: np.ndarray, weights: np.ndarray,
                             lock: np.ndarray | None, error: float, options: int) -> np.ndarray:
    """tris (T,3) local indices; returns the simplified (T',3) local indices (subset of vertices)."""
    lib = _load()
    idx = np.ascontiguousarray(tris, np.uint32).ravel()
    P = np.ascontiguousarray(pos, np.float32)
    A = np.ascontiguousarray(attrs, np.float32)
    W = np.ascontiguousarray(weights, np.float32)
    L = np.ascontiguousarray(lock, np.uint8) if lock is not None else None
    dest = np.zeros_like(idx)
    out_err = ctypes.c_float(0)

    def ptr(a, t):
        return a.ctypes.data_as(ctypes.POINTER(t))

    n = lib.meshopt_simplifyWithAttributes(
        ptr(dest, ctypes.c_uint), ptr(idx, ctypes.c_uint), len(idx), ptr(P, ctypes.c_float), len(P), 12,
        ptr(A, ctypes.c_float), A.shape[1] * 4, ptr(W, ctypes.c_float), len(W),
        ptr(L, ctypes.c_ubyte) if L is not None else None, 0, float(error), options, ctypes.byref(out_err))
    return dest[:n].reshape(-1, 3).astype(np.int64)
