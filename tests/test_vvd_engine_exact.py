"""Tests for engine-exact VVD normal/tangent snapping (vvd_engine_exact.py)."""
from __future__ import annotations

import lzma
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vvd_engine_exact as ve  # noqa: E402

F = np.float32


def sdk_pack(nx, ny, nz, is_tangent=False, binormal_sign=1.0):
    """Literal scalar transcription of PackNormal_UBYTE4 (Source SDK 2013, mathlib.h), float32."""
    nx, ny, nz, ts = F(nx), F(ny), F(nz), F(binormal_sign)
    xs = F(-1) if nx < 0 else F(1)
    ys = F(-1) if ny < 0 else F(1)
    zs = F(-1) if nz < 0 else F(1)
    xsb = F(0.5) * (F(1) - xs); ysb = F(0.5) * (F(1) - ys); zsb = F(0.5) * (F(1) - zs); tsb = F(0.5) * (F(1) - ts)
    ax, ay, az = xs * nx, ys * ny, zs * nz
    xb = ax / ((ax + ay) + az); yb = ay / ((ax + ay) + az)
    xb = xb * F(63); yb = yb * F(63)
    xb = xb * xs - xsb; yb = yb * ys - ysb
    xb = xb + F(64); yb = yb + F(64)
    xb = xb * zs - zsb; yb = yb * ts - tsb
    xb = xb + F(128); yb = yb + F(128)
    return int(xb), int(yb)


def random_units(n, seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(F)


def test_vectorised_pack_matches_the_sdk_code():
    v = random_units(3000, 1)
    w = np.where(np.random.default_rng(2).random(3000) < 0.5, F(-1), F(1))
    cx, cy, _, _ = ve.pack_ubyte4(v, w)
    for i in range(0, 3000, 7):
        assert (int(cx[i]), int(cy[i])) == sdk_pack(*v[i], is_tangent=True, binormal_sign=w[i])


def test_snapped_directions_keep_the_engine_code_and_get_round():
    v = random_units(20000, 3)
    out, st = ve.snap_directions(v)
    a = ve.pack_ubyte4(v); b = ve.pack_ubyte4(out)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    assert st["changed"] > 0.95 * len(v)
    # the replacements sit on coarse grids: many zero low mantissa bits
    low = out.view(np.uint32) & 0xFFF
    assert (low == 0).mean() > 0.9


def test_originals_on_a_step_edge_are_left_alone():
    # build a vector whose x step value is a hair above an integer (ambiguous under x87/FMA)
    v = np.array([[0.30, 0.45, 0.25]], F)
    _, _, xb, _ = ve.pack_ubyte4(v)
    target = np.floor(xb[0]) + F(0.0005)
    ax = (target - F(128) - F(64)) / F(63)          # positive octant: xb = 63*ax/(sum) + 192
    v2 = np.array([[ax * 1.0, 0.45, 1.0 - ax - 0.45]], F)
    m = ve._margin(*ve.pack_ubyte4(v2)[2:])
    if 0 < m[0] < ve.ORIGINAL_EDGE:
        out, st = ve.snap_directions(v2)
        assert np.array_equal(out, v2) and st["changed"] == 0


def synthetic_vvd(n=4000, seed=5) -> bytes:
    rng = np.random.default_rng(seed)
    vstart = 64
    hdr = bytearray(64)
    hdr[0:4] = b"IDSV"
    struct.pack_into("<iii", hdr, 4, 4, 1234, 1)
    struct.pack_into("<8i", hdr, 16, n, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<4i", hdr, 48, 0, 64, vstart, vstart + n * 48)
    verts = np.zeros((n, 48), np.uint8)
    w = np.zeros((n, 3), F); w[:, 0] = 1
    verts[:, 0:12] = w.view(np.uint8).reshape(n, 12)
    verts[:, 15] = 1
    f = np.zeros((n, 8), F)
    f[:, 0:3] = rng.normal(size=(n, 3)) * 30
    f[:, 3:6] = random_units(n, seed + 1)
    f[:, 6:8] = rng.random((n, 2))
    verts[:, 16:48] = f.view(np.uint8).reshape(n, 32)
    tan = np.zeros((n, 4), F); tan[:, 0:3] = random_units(n, seed + 2); tan[:, 3] = 1
    tan[::50, 0:3] = 0          # some zero tangents must survive untouched
    return bytes(hdr) + verts.tobytes() + tan.tobytes()


def test_vvd_keeps_layout_and_everything_but_directions():
    raw = synthetic_vvd()
    new, st = ve.snap_vvd(raw)
    assert len(new) == len(raw) and new[:64] == raw[:64]
    n = 4000
    a = np.frombuffer(raw, np.uint8, n * 48, 64).reshape(n, 48); b = np.frombuffer(new, np.uint8, n * 48, 64).reshape(n, 48)
    assert np.array_equal(a[:, 0:28], b[:, 0:28])          # weights, bones, position
    assert np.array_equal(a[:, 40:48], b[:, 40:48])        # uv
    ta = np.frombuffer(raw, F, n * 4, 64 + n * 48).reshape(n, 4); tb = np.frombuffer(new, F, n * 4, 64 + n * 48).reshape(n, 4)
    assert np.array_equal(ta[:, 3], tb[:, 3])              # binormal sign
    assert np.array_equal(ta[::50], tb[::50])              # zero tangents untouched
    assert len(lzma.compress(new)) < 0.85 * len(lzma.compress(raw))


def test_tree_rewrites_in_place_and_reports(tmp_path):
    d = tmp_path / "models" / "x"
    d.mkdir(parents=True)
    (d / "a.vvd").write_bytes(synthetic_vvd())
    (d / "bad.vvd").write_bytes(b"NOPE" + bytes(100))
    s = ve.optimize_tree(tmp_path / "models", report_path=tmp_path / "r.json")
    assert (s.scanned_files, s.changed_files, s.failed_files) == (2, 1, 1)
    assert (d / "bad.vvd").read_bytes().startswith(b"NOPE")
    assert (tmp_path / "r.json").exists()


def test_parallel_tree_pass_writes_the_same_files_and_report(tmp_path):
    for name in ("seq", "par"):
        for i in range(4):
            d = tmp_path / name / "models" / f"m{i}"
            d.mkdir(parents=True)
            (d / "a.vvd").write_bytes(synthetic_vvd(seed=10 * i))
        (tmp_path / name / "models" / "bad.vvd").write_bytes(b"NOPE" + bytes(100))
    ve.optimize_tree(tmp_path / "seq" / "models", report_path=tmp_path / "seq.json")
    ve.optimize_tree(tmp_path / "par" / "models", report_path=tmp_path / "par.json", jobs=3)
    for f in sorted((tmp_path / "seq").rglob("*.vvd")):
        assert f.read_bytes() == (tmp_path / "par" / f.relative_to(tmp_path / "seq")).read_bytes()
    import json
    def norm(name):   # same report apart from the root folder in the paths
        text = (tmp_path / f"{name}.json").read_text(encoding="utf-8")
        return text.replace(json.dumps(str(tmp_path / name))[1:-1], "ROOT")
    assert norm("seq") == norm("par")


def test_packaging_flag_is_on_by_default():
    import build_optimized_addon as boa
    args = boa._build_argument_parser().parse_args(["x"])
    assert args.no_vvd_exact_normals is False
    assert boa._build_argument_parser().parse_args(["x", "--no-vvd-exact-normals"]).no_vvd_exact_normals is True
