"""Coarse stand-in geometry of a streamed model: what the engine itself draws (RTT shadows, beyond the
stream distance, the first frames while a stream decodes, and everything if the runtime is off).

Per (mesh, bone) group meshoptimizer's attribute-aware simplifier keeps ~ratio of the triangles
(normals and UVs weighted, attribute seams may collapse, small parts pruned); the sloppy simplifier is
the fallback when topology keeps a group too big. Every mesh that had triangles keeps at least one, so
the model keeps its material and bodygroup structure."""
from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np

from model_screen import meshopt
from model_screen.binary import mesh_arrays

_sloppy_ready = False


def _lib():
    global _sloppy_ready
    lib = meshopt._load()
    if not _sloppy_ready:
        u32p, f32p, u8p, sz = (ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_float),
                               ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t)
        lib.meshopt_simplifySloppy.restype = sz
        lib.meshopt_simplifySloppy.argtypes = [u32p, u32p, sz, f32p, sz, sz, u8p, sz, ctypes.c_float, f32p]
        _sloppy_ready = True
    return lib


def _ptr(a, t):
    return a.ctypes.data_as(ctypes.POINTER(t))


def sloppy(tris: np.ndarray, pos: np.ndarray, target: int, error: float = 1.0) -> np.ndarray:
    lib = _lib()
    idx = np.ascontiguousarray(tris, np.uint32).ravel()
    P = np.ascontiguousarray(pos, np.float32)
    dest = np.zeros_like(idx)
    err = ctypes.c_float(0)
    n = lib.meshopt_simplifySloppy(_ptr(dest, ctypes.c_uint), _ptr(idx, ctypes.c_uint), len(idx),
                                   _ptr(P, ctypes.c_float), len(P), 12, None, max(3, target * 3), float(error),
                                   ctypes.byref(err))
    return dest[:n].reshape(-1, 3).astype(np.int64)


def coarse_tris(mdl: Path, ratio: float = 0.02) -> dict:
    """{mesh key: (T,3) mesh-local record ids} keeping about ratio of each group's triangles."""
    raw = mdl.with_suffix(".vvd").read_bytes()
    n = int(np.frombuffer(raw, "<i4", 1, 16)[0])
    vstart = int(np.frombuffer(raw, "<i4", 1, 56)[0])
    V = np.frombuffer(raw, np.uint8, n * 48, vstart).reshape(n, 48)
    F = V[:, 16:48].copy().view(np.float32).reshape(n, 8)
    bone = V[:, 12].astype(np.int64)
    lib = _lib()
    w = np.array([0.5, 0.5, 0.5, 0.2, 0.2], np.float32)
    out = {}
    for key, st, _cnt, t in mesh_arrays(mdl):
        if not len(t):
            out[key] = np.asarray(t, np.int64).reshape(-1, 3)
            continue
        T = np.asarray(t, np.int64)
        tb = bone[T[:, 0] + st]
        keep = []
        for bb in np.unique(tb):
            g = T[tb == bb]
            if len(g) <= 4:
                keep.append(g)
                continue
            verts = np.unique(g)
            lt = np.searchsorted(verts, g)
            P = np.ascontiguousarray(F[verts + st, 0:3], np.float32)
            A = np.ascontiguousarray(np.c_[F[verts + st, 3:6], F[verts + st, 6:8]], np.float32)
            target = max(1, int(round(len(g) * ratio)))
            idx = np.ascontiguousarray(lt, np.uint32).ravel()
            dest = np.zeros_like(idx)
            err = ctypes.c_float(0)
            nidx = lib.meshopt_simplifyWithAttributes(
                _ptr(dest, ctypes.c_uint), _ptr(idx, ctypes.c_uint), len(idx), _ptr(P, ctypes.c_float), len(P), 12,
                _ptr(A, ctypes.c_float), 20, _ptr(w, ctypes.c_float), 5, None, target * 3, 1.0,
                meshopt.PRUNE | meshopt.PERMISSIVE, ctypes.byref(err))
            r = dest[:nidx].reshape(-1, 3).astype(np.int64)
            if len(r) > max(3, len(g) * ratio * 3):    # topology kept it big: sloppy instead
                r2 = sloppy(lt, P, target)
                if len(r2):
                    r = r2
            if len(r):
                keep.append(verts[r])
        if not keep:
            Pm = F[:, 0:3]
            areas = np.linalg.norm(np.cross(Pm[T[:, 1] + st] - Pm[T[:, 0] + st], Pm[T[:, 2] + st] - Pm[T[:, 0] + st]),
                                   axis=1)
            keep.append(T[[int(np.argmax(areas))]])
        out[key] = np.concatenate(keep)
    return out
