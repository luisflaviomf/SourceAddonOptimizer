"""Engine-exact normal/tangent snapping for compiled Source models (.vvd).

Why it is lossless on screen
----------------------------
VertexLitGeneric (and Refract, Skin, UnlitTwoTexture...) request VERTEX_FORMAT_COMPRESSED, so the
engine packs every model normal and tangent into 2 bytes with PackNormal_UBYTE4
(src/public/mathlib/mathlib.h, Source SDK 2013) before the mesh reaches the GPU: 63 steps per
octant axis (about 0.7 degrees). Any float vector inside the same step gives the GPU the very
same bytes. This module replaces each normal/tangent by the "roundest" float vector that packs
to the same code, with a safety margin from the step edges. The VVD keeps its size and layout;
only low mantissa bits become zero, so the Workshop LZMA download shrinks (about -40% for model
files on the LVS cars pack) while the rendered image stays identical (verified in game, 163
models).

Safety rules
------------
- Positions, UVs, bone weights and the tangent sign are never touched.
- A replacement must pack to the original code AND stay at least MARGIN of a step away from
  the step edges.
- Originals that lie almost on a step edge (but not exactly, e.g. zero components) are left
  untouched: a 32-bit x87 build or FMA could have packed them into the neighbouring step.
- Zero/invalid vectors are left untouched. Every result is re-packed and checked.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

F = np.float32
GRIDS = [2.0 ** -k for k in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14)]   # coarsest first
MARGIN = 0.10          # replacements stay at least 10% of a step away from its edges
ORIGINAL_EDGE = 0.002  # originals closer than this to an edge (and not exactly on it) stay as they are
VVD_VERTEX = 48


def pack_ubyte4(n: np.ndarray, binormal_sign: np.ndarray | None = None):
    """Vectorised float32 replica of PackNormal_UBYTE4.

    Returns (cX, cY, xbits, ybits): the two packed bytes and the continuous values the engine
    truncates to get them."""
    nx, ny, nz = (n[:, 0].astype(F), n[:, 1].astype(F), n[:, 2].astype(F))
    one, half = F(1), F(0.5)
    xs = np.where(nx < 0, F(-1), one)
    ys = np.where(ny < 0, F(-1), one)
    zs = np.where(nz < 0, F(-1), one)
    ts = np.ones_like(nx) if binormal_sign is None else binormal_sign.astype(F)
    xsb = half * (one - xs); ysb = half * (one - ys); zsb = half * (one - zs); tsb = half * (one - ts)
    ax = xs * nx; ay = ys * ny; az = zs * nz
    s = (ax + ay) + az
    with np.errstate(divide="ignore", invalid="ignore"):
        xb = ax / s
        yb = ay / s
    xb = xb * F(63); yb = yb * F(63)
    xb = xb * xs - xsb; yb = yb * ys - ysb
    xb = xb + F(64); yb = yb + F(64)
    xb = xb * zs - zsb; yb = yb * ts - tsb
    xb = xb + F(128); yb = yb + F(128)
    return np.trunc(xb), np.trunc(yb), xb, yb


def _margin(xb, yb):
    fx = xb - np.floor(xb)
    fy = yb - np.floor(yb)
    return np.minimum(np.minimum(fx, 1 - fx), np.minimum(fy, 1 - fy))


def _step_centre(n: np.ndarray) -> np.ndarray:
    """Direction at the centre of the step that n falls in (same signs)."""
    a = np.abs(n.astype(np.float64))
    s = a.sum(axis=1, keepdims=True)
    p = a / np.where(s == 0, 1, s)
    px = (np.floor(p[:, 0] * 63) + 0.5) / 63
    py = (np.floor(p[:, 1] * 63) + 0.5) / 63
    over = px + py > 1 - 1e-3
    scale = np.where(over, (1 - 1e-3) / (px + py), 1.0)
    px *= scale
    py *= scale
    c = np.stack([px, py, 1 - px - py], axis=1)
    c /= np.linalg.norm(c, axis=1, keepdims=True)
    return c * np.where(n < 0, -1.0, 1.0)


def snap_directions(n: np.ndarray, w: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Roundest vectors with the same engine code. w: tangent binormal sign (None for normals)."""
    n = n.astype(F)
    valid = np.isfinite(n).all(axis=1) & (np.abs(n).sum(axis=1) > 1e-6)
    if w is not None:
        valid &= np.isin(w, (F(1), F(-1)))
    cx0, cy0, xb0, yb0 = pack_ubyte4(n, w)
    m0 = _margin(xb0, yb0)
    movable = valid & ((m0 >= ORIGINAL_EDGE) | (m0 == 0))
    out = n.copy()
    done = ~movable
    centre = _step_centre(n)
    for g in GRIDS:
        for base in (centre, n.astype(np.float64)):
            cand = (np.round(base / g) * g).astype(F)
            cand = np.where((np.sign(cand) != np.sign(n)) & (n != 0), n, cand)   # never flip a sign
            cx, cy, xb, yb = pack_ubyte4(cand, w)
            ok = (~done) & (cx == cx0) & (cy == cy0) & (_margin(xb, yb) >= MARGIN) & (np.abs(cand).sum(axis=1) > 1e-6)
            out[ok] = cand[ok]
            done |= ok
    cx1, cy1, _, _ = pack_ubyte4(out, w)
    if not (np.array_equal(cx1[valid], cx0[valid]) and np.array_equal(cy1[valid], cy0[valid])):
        raise AssertionError("engine code changed")   # cannot happen; keeps the file untouched
    changed = int((out != n).any(axis=1).sum())
    return out, {"total": int(len(n)), "changed": changed, "untouched": int(len(n) - changed)}


def snap_vvd(raw: bytes) -> tuple[bytes, dict]:
    """Return the VVD with engine-exact normals/tangents (same size and layout)."""
    if raw[:4] != b"IDSV":
        raise ValueError("not a VVD file")
    version = struct.unpack_from("<i", raw, 4)[0]
    if version != 4:
        raise ValueError(f"unsupported VVD version {version}")
    num_lods = struct.unpack_from("<i", raw, 12)[0]
    lod0 = struct.unpack_from("<i", raw, 16)[0]
    num_fixups, _fix, vstart, tstart = struct.unpack_from("<4i", raw, 48)
    nverts = (tstart - vstart) // VVD_VERTEX if tstart > vstart else (len(raw) - vstart) // VVD_VERTEX
    if nverts <= 0 or vstart + nverts * VVD_VERTEX > len(raw):
        raise ValueError("VVD vertex block out of range")
    out = bytearray(raw)
    v = np.frombuffer(raw, np.uint8, nverts * VVD_VERTEX, vstart).reshape(nverts, VVD_VERTEX).copy()
    f = v[:, 16:48].copy().view(np.float32).reshape(nverts, 8)
    f[:, 3:6], st_n = snap_directions(f[:, 3:6])
    v[:, 16:48] = f.view(np.uint8).reshape(nverts, 32)
    out[vstart:vstart + v.nbytes] = v.tobytes()
    st_t = None
    if tstart and tstart + nverts * 16 <= len(raw):
        t = np.frombuffer(raw, np.float32, nverts * 4, tstart).reshape(nverts, 4).copy()
        t[:, 0:3], st_t = snap_directions(t[:, 0:3], t[:, 3])
        out[tstart:tstart + t.nbytes] = t.tobytes()
    return bytes(out), {"vertices": int(nverts), "lods": int(num_lods), "lod0": int(lod0),
                        "fixups": int(num_fixups), "normals": st_n, "tangents": st_t}


@dataclass
class TreeSummary:
    scanned_files: int = 0
    changed_files: int = 0
    failed_files: int = 0
    vertices: int = 0
    failures: list = field(default_factory=list)


def _snap_file(path) -> dict:
    """Snap one .vvd in place. Returns {"row", "changed"} or {"failure"} (the file is then left as it was)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        new, st = snap_vvd(raw)
        changed = new != raw
        if changed:
            tmp = path.with_name(path.name + ".exact.tmp")
            tmp.write_bytes(new)
            tmp.replace(path)
        return {"changed": changed,
                "row": {"file": str(path), **{k: st[k] for k in ("vertices",)},
                        "normals_changed": st["normals"]["changed"],
                        "tangents_changed": (st["tangents"] or {}).get("changed", 0)}}
    except Exception as exc:  # leave the file as it was
        return {"failure": {"file": str(path), "error": f"{type(exc).__name__}: {exc}"}}


def optimize_tree(models_root: Path, report_path: Path | None = None, jobs: int = 1,
                  executor=None) -> TreeSummary:
    """Snap every .vvd under models_root in place (atomic replace; failures keep the original).

    Files are independent: with jobs > 1 (or a given process-pool executor) they run in parallel;
    results keep the sorted path order, so files and report match a sequential run."""
    summary = TreeSummary()
    rows = []
    paths = sorted(Path(models_root).rglob("*.vvd"))
    if executor is not None and len(paths) > 1:
        results = list(executor.map(_snap_file, [str(p) for p in paths], chunksize=1))
    elif jobs > 1 and len(paths) > 1:
        from texlab.jobs import process_pool
        with process_pool(min(jobs, len(paths))) as pool:
            results = list(pool.map(_snap_file, [str(p) for p in paths], chunksize=1))
    else:
        results = [_snap_file(p) for p in paths]
    for res in results:
        summary.scanned_files += 1
        if "failure" in res:
            summary.failed_files += 1
            summary.failures.append(res["failure"])
            continue
        summary.changed_files += int(res["changed"])
        summary.vertices += res["row"]["vertices"]
        rows.append(res["row"])
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"summary": {k: v for k, v in summary.__dict__.items() if k != "failures"},
                                           "failures": summary.failures, "files": rows}, indent=1), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import sys
    s = optimize_tree(Path(sys.argv[1]))
    print(f"scanned={s.scanned_files} changed={s.changed_files} failed={s.failed_files} vertices={s.vertices}")
