"""Tests for the texlab screen-aware VTF engine core (numpy-only modules shipped in the worker)."""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from texlab import bcnp, npsolve  # noqa: E402
from texlab.screen_compress import plan_addon, run  # noqa: E402
from texlab.vtf import image_bytes, read_vtf, truncate_mips, write_vtf, decode_level  # noqa: E402

VTFCMD = Path(os.environ.get("LOCALAPPDATA", "")) / "GmodAddonOptimizer/tools/VTFEdit/1/VTFEdit/VTFCmd.exe"


def random_blocks(rng, n, fmt):
    size = 8 if fmt == 13 else 16
    return rng.integers(0, 256, size=n * size, dtype=np.uint8).tobytes()


def make_vtf(width, height, fmt, rng, flags=0, mips=None):
    """Minimal VTF 7.2 with random BC blocks for every mip level."""
    mips = mips or (max(width, height).bit_length())
    header = bytearray(80)
    header[0:4] = b"VTF\0"
    struct.pack_into("<3I", header, 4, 7, 2, 80)
    struct.pack_into("<HHIHH", header, 16, width, height, flags, 1, 0)
    struct.pack_into("<f", header, 48, 1.0)
    struct.pack_into("<iBiBB", header, 52, fmt, mips, -1, 0, 0)
    struct.pack_into("<H", header, 63, 1)
    data = b""
    for level in reversed(range(mips)):
        w, h = max(1, width >> level), max(1, height >> level)
        nblocks = image_bytes(fmt, w, h) // (8 if fmt == 13 else 16)
        data += random_blocks(rng, nblocks, fmt)
    return bytes(header) + data


def test_bc1_decode_known_block():
    # c0 = pure red (0xF800), c1 = pure blue (0x001F), all indices = 0 then 1 then 2 then 3 per row
    block = struct.pack("<HHI", 0xF800, 0x001F, 0b11111111_10101010_01010101_00000000)
    img = bcnp.decode_bc(block, 4, 4, 13)
    assert tuple(img[0, 0]) == (255, 0, 0, 255)
    assert tuple(img[1, 0]) == (0, 0, 255, 255)
    assert tuple(img[2, 0]) == ((2 * 255 + 0) // 3, 0, (0 + 2 * 0 + 255) // 3, 255)
    assert tuple(img[3, 0]) == (255 // 3, 0, (2 * 255) // 3, 255)


def test_bc3_to_bc1_is_exact_for_rgb():
    rng = np.random.default_rng(1)
    data = random_blocks(rng, 4096, 15)
    # force some equal-endpoint blocks
    arr = bytearray(data)
    for i in range(0, len(arr), 16 * 7):
        arr[i + 10:i + 12] = arr[i + 8:i + 10]
    data = bytes(arr)
    rgb_bc3 = bcnp.decode_bc(data, 256, 64, 15)[..., :3]
    bc1 = bcnp.bc3_to_bc1(data)
    out = bcnp.decode_bc(bc1, 256, 64, 13)
    assert np.array_equal(out[..., :3], rgb_bc3)
    assert (out[..., 3] == 255).all()


def test_truncate_and_write_roundtrip():
    rng = np.random.default_rng(2)
    raw = make_vtf(64, 32, 15, rng)
    v = read_vtf(raw)
    assert (v.width, v.height, v.mip_count) == (64, 32, 7)
    t = read_vtf(truncate_mips(v, 2))
    assert (t.width, t.height, t.mip_count) == (16, 8, 5)
    for level in range(t.mip_count):
        assert t.level_data(level) == v.level_data(level + 2)
    levels = [bcnp.bc3_to_bc1(v.level_data(l)) for l in range(v.mip_count)]
    w = read_vtf(write_vtf(v, 13, v.width, v.height, levels, v.flags))
    assert w.fmt == 13 and w.mip_count == v.mip_count
    assert np.array_equal(decode_level(w, 0)[..., :3], decode_level(v, 0)[..., :3])


@pytest.mark.parametrize("factor", [2, 4, 8])
@pytest.mark.parametrize("wrap", [True, False])
def test_upsample_adjoint(factor, wrap):
    rng = np.random.default_rng(factor)
    x = rng.random((5, 7, 3)).astype(np.float32)
    y = rng.random((5 * factor, 7 * factor, 3)).astype(np.float32)
    lhs = float((npsolve.up(x, factor, wrap, wrap) * y).sum())
    rhs = float((x * npsolve.up_t(y, factor, wrap, wrap)).sum())
    assert abs(lhs - rhs) / abs(lhs) < 1e-5


def test_solver_improves_magnified_reconstruction():
    rng = np.random.default_rng(3)
    H = rng.random((64, 64, 4)).astype(np.float32)
    M1 = H.reshape(32, 2, 32, 2, 4).mean(axis=(1, 3))      # box mip
    L = npsolve.solve(H, M1, 2, alpha=4.0, beta=1.0)
    err_box = float(((npsolve.up(M1, 2, True, True) - H) ** 2).mean())
    err_opt = float(((npsolve.up(L, 2, True, True) - H) ** 2).mean())
    assert err_opt < err_box
    assert L.min() >= 0.0 and L.max() <= 1.0


def _material_only_addon(tmp_path: Path, rng) -> Path:
    root = tmp_path / "addon"
    mat = root / "materials" / "models" / "texlab_test"
    mat.mkdir(parents=True)
    (mat / "opaque.vtf").write_bytes(make_vtf(64, 64, 13, rng))
    (mat / "masked.vtf").write_bytes(make_vtf(64, 64, 15, rng))
    (mat / "plain.vtf").write_bytes(make_vtf(64, 64, 15, rng))
    (mat / "masked.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/texlab_test/masked" '
                                    '"$blendtintbybasealpha" "1" }')
    (mat / "plain.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/texlab_test/plain" }')
    (mat / "nomip.vtf").write_bytes(make_vtf(64, 64, 13, rng, flags=0x100, mips=1))
    return root


def test_plan_keeps_alpha_masks_and_routes_fallbacks(tmp_path):
    root = _material_only_addon(tmp_path, np.random.default_rng(4))
    plan = {Path(i.rel).stem: i for i in plan_addon(root)}
    assert plan["masked"].out_fmt == 15            # $blendtintbybasealpha reads the alpha
    assert plan["plain"].out_fmt == 13             # alpha unread -> DXT1
    assert plan["opaque"].drop == 1 and plan["opaque"].reason == "not_seen_by_models"
    assert plan["nomip"].action == "fallback"


@pytest.mark.skipif(not VTFCMD.exists(), reason="VTFCmd not extracted on this machine")
def test_engine_rewrites_valid_smaller_vtfs(tmp_path):
    root = _material_only_addon(tmp_path, np.random.default_rng(5))
    before = {p.name: p.stat().st_size for p in root.rglob("*.vtf")}
    report = run(root, VTFCMD, "q50", jobs=2, report_path=tmp_path / "report.json")
    assert report["after"] < report["before"]
    for p in root.rglob("*.vtf"):
        v = read_vtf(p)
        if p.name in ("opaque.vtf", "plain.vtf", "masked.vtf"):
            assert v.width == 32 and p.stat().st_size < before[p.name]
    assert read_vtf(root / "materials/models/texlab_test/masked.vtf").fmt == 15
    assert "materials/models/texlab_test/nomip.vtf" in report["fallback"]


def test_magnified_map_stays_a_valid_weight_with_huge_pixel_weights():
    from texlab.density import ViewModel
    from texlab.screen_compress import magnified_map
    rng = np.random.default_rng(6)
    n = 5000
    s_eff = rng.uniform(0.5, 400.0, n)
    area = rng.uniform(1e-3, 5e4, n)          # mixes tiny and enormous triangles
    uv = rng.random((n, 2))
    m = magnified_map((s_eff, s_eff * 3.0), area, uv, 1, ViewModel(screen_height=1080), 256, 128, 0.5)
    assert m.shape == (256, 128)
    assert np.isfinite(m).all() and m.min() >= 0.0 and m.max() <= 1.0
    H = rng.random((512, 256, 4)).astype(np.float32)
    M = H.reshape(256, 2, 128, 2, 4).mean(axis=(1, 3))
    L = npsolve.solve(H, M, 2, m * 4.0, np.maximum(1.0 - m, 0.05), iters=10)
    assert np.isfinite(L).all()


def test_lanczos_top_level_has_the_reduced_size_and_valid_range():
    from texlab.screen_compress import lanczos_down
    rng = np.random.default_rng(7)
    H = rng.random((64, 32, 4)).astype(np.float32)
    for factor in (2, 4, 8):
        L = lanczos_down(H, factor)
        assert L.shape == (64 // factor, 32 // factor, 4)
        assert L.min() >= 0.0 and L.max() <= 1.0


# ----------------------------------------------------------------------------- profiles "exact" and "max"

class _SeenAt:
    """texlab.density.TextureUse stand-in: every triangle shows the texture at `s` texels per unit."""

    def __init__(self, s: float, n: int = 64):
        self.s, self.n = s, n
        self.uv = [np.random.default_rng(0).random((n, 2))]

    def arrays(self, anisotropy: float = 16.0):
        return np.full(self.n, float(self.s)), np.ones(self.n)


def _profiles_addon(tmp_path: Path, rng) -> Path:
    root = tmp_path / "addon"
    mat = root / "materials" / "models" / "t"
    mat.mkdir(parents=True)
    for name in ("body", "police_sign", "sign_nm", "skin_ao", "hud", "coarse"):
        (mat / f"{name}.vtf").write_bytes(make_vtf(64, 64, 13, rng))
    (mat / "small.vtf").write_bytes(make_vtf(32, 32, 13, rng))
    (mat / "tiny.vtf").write_bytes(make_vtf(16, 16, 13, rng))
    (mat / "paint.vtf").write_bytes(make_vtf(64, 64, 15, rng))          # DXT5, alpha read by no material
    (mat / "nomip.vtf").write_bytes(make_vtf(64, 64, 13, rng, flags=0x100, mips=1))
    (mat / "rgba.vtf").write_bytes(make_vtf(64, 64, 0, rng, mips=1))     # not DXT
    (mat / "broken.vtf").write_bytes(b"VTF\0 not really")
    (mat / "body.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/t/body" "$detail" "models/t/skin_ao" }')
    (mat / "paint.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/t/paint" }')
    return root


# s = 1000 texels/unit: the finest levels are never sampled, not even up close (the density logic alone
# would take these to 8x); s = 1: magnified up close, random blocks carry a lot of detail energy
_USES = {"models/t/body": 1000.0, "models/t/police_sign": 1000.0, "models/t/sign_nm": 1000.0,
         "models/t/skin_ao": 1000.0, "models/t/paint": 1000.0, "models/t/coarse": 1.0}


def _plan(root, profile, monkeypatch):
    from texlab import screen_compress as sc
    monkeypatch.setattr(sc, "collect_usage", lambda _root: {k: _SeenAt(s) for k, s in _USES.items()})
    return {Path(i.rel).stem: i for i in plan_addon(root, profile)}


def test_exact_profile_never_reduces_and_never_falls_back(tmp_path, monkeypatch):
    from texlab import screen_compress as sc
    assert {"exact", "max"} <= set(sc.PROFILES)
    root = _profiles_addon(tmp_path, np.random.default_rng(8))
    plan = _plan(root, "exact", monkeypatch)
    assert all(i.drop == 0 for i in plan.values())
    assert not [i for i in plan.values() if i.action not in ("keep", "transcode")]
    assert plan["paint"].action == "transcode" and plan["paint"].out_fmt == 13
    assert {n for n, i in plan.items() if i.action == "transcode"} == {"paint"}
    assert plan["nomip"].reason == "nomip" and plan["rgba"].reason == "non_dxt_format"
    assert plan["broken"].reason.startswith("unreadable")
    # the default profile still routes those files to the regular path
    q50 = _plan(root, "q50", monkeypatch)
    assert {n for n, i in q50.items() if i.action == "fallback"} == {"nomip", "rgba", "broken"}


def test_exact_profile_run_is_pixel_identical(tmp_path):
    root = _profiles_addon(tmp_path, np.random.default_rng(9))
    before = {p.name: p.read_bytes() for p in root.rglob("*.vtf")}
    # nothing is reduced, so VTFCmd is never started
    report = run(root, tmp_path / "VTFCmd-not-needed.exe", "exact", jobs=2, report_path=tmp_path / "report.json")
    assert report["fallback"] == [] and all(r["drop"] == 0 for r in report["rows"])
    for p in root.rglob("*.vtf"):
        if p.name != "paint.vtf":
            assert p.read_bytes() == before[p.name], p.name
    old, new = read_vtf(before["paint.vtf"]), read_vtf(root / "materials/models/t/paint.vtf")
    assert new.fmt == 13 and (new.width, new.height, new.mip_count) == (old.width, old.height, old.mip_count)
    for level in range(old.mip_count):     # alpha is read by no material: the colours are what matters
        assert np.array_equal(decode_level(new, level)[..., :3], decode_level(old, level)[..., :3])
    assert report["after"] < report["before"]


def test_max_profile_drops_at_least_two_levels_and_caps_text_and_detail(tmp_path, monkeypatch):
    root = _profiles_addon(tmp_path, np.random.default_rng(10))
    plan = _plan(root, "max", monkeypatch)
    drops = {n: i.drop for n, i in plan.items() if i.action in ("reduce", "transcode")}
    assert drops == {
        "body": 3, "sign_nm": 3, "paint": 3,      # never sampled up close: 8x
        "police_sign": 2,                          # lettering (file name) stops at 4x
        "skin_ao": 2,                              # used as $detail: stops at 4x
        "coarse": 2, "hud": 2,                     # detail lost up close / not seen by models: 4x
        "small": 2,                                # 32x32: 4x is as far as MIN_SIDE allows
        "tiny": 1,                                 # 16x16: only 2x is possible
    }
    assert plan["paint"].out_fmt == 13
    assert {n for n, i in plan.items() if i.action == "fallback"} == {"nomip", "rgba", "broken"}
    # same textures with the default profile: 2x unless the density logic proves more
    q50 = _plan(root, "q50", monkeypatch)
    assert q50["hud"].drop == 1 and q50["police_sign"].drop == 3 and q50["coarse"].drop == 1
