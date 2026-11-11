"""The Rust hot paths (tools/native/gmo_native.dll) must return exactly what the numpy code returns."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from texlab import allocate, bcnp, native  # noqa: E402

pytestmark = pytest.mark.skipif(not native.available(), reason="gmo_native.dll not built")

LVS = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS/lvs_cars_pack")


@pytest.mark.parametrize("fmt", [13, 20, 14, 15])
def test_decode_bc_matches_numpy_on_random_blocks(fmt):
    rng = np.random.default_rng(fmt)
    bsize = 8 if fmt in (13, 20) else 16
    for w, h in [(1, 1), (2, 2), (3, 5), (4, 4), (5, 3), (13, 7), (64, 32)]:
        nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
        for _ in range(15):
            data = rng.integers(0, 256, nb * bsize, dtype=np.uint8).tobytes()
            assert np.array_equal(native.decode_bc(data, w, h, fmt), bcnp.decode_bc_numpy(data, w, h, fmt))


def test_decode_bc_rejects_truncated_data_like_numpy():
    assert native.decode_bc(b"\0" * 8, 8, 8, 13) is None
    with pytest.raises(ValueError):
        bcnp.decode_bc(b"\0" * 8, 8, 8, 13)


@pytest.mark.parametrize("contrast", [True, False])
def test_band_energy_matches_numpy(contrast):
    rng = np.random.default_rng(7)
    for hl, wl in [(1, 1), (1, 4), (3, 2), (8, 8), (32, 16)]:
        hi = rng.integers(0, 256, (2 * hl, 2 * wl, 4), dtype=np.uint8)
        lo = rng.integers(0, 256, (hl, wl, 4), dtype=np.uint8)
        ref = allocate._band_energy_maps_numpy([hi, lo], contrast)[0]
        assert native.band_energy(hi, lo, contrast).tobytes() == ref.tobytes()


def test_sample_map_matches_numpy():
    rng = np.random.default_rng(11)
    for h, w in [(1, 1), (1, 3), (2, 2), (5, 7), (16, 8), (64, 64)]:
        m = (rng.random((h, w)) * 1e3).astype(np.float32)
        uv = rng.normal(0.5, 2.0, (300, 2))
        assert native.sample_map(m, uv).tobytes() == allocate._sample_map_numpy(m, uv).tobytes()


@pytest.mark.skipif(not LVS.exists(), reason="LVS sample textures not on this machine")
def test_real_textures_band_maps_match_numpy():
    from texlab.vtf import decode_level, read_vtf
    paths = sorted((LVS / "materials").rglob("*.vtf"))[::40]
    checked = 0
    for p in paths:
        v = read_vtf(p)
        if v.fmt not in (13, 14, 15, 20) or v.mip_count < 2:
            continue
        raw = [decode_level(v, l) for l in range(min(v.mip_count, 4))]
        for l in range(len(raw)):
            assert np.array_equal(raw[l], bcnp.decode_bc_numpy(v.level_data(l), *v.mip_size(l), v.fmt))
        ref = allocate._band_energy_maps_numpy(raw)
        got = allocate.band_energy_maps(p, max_levels=3, vtf=v)
        assert [m.tobytes() for m in got] == [m.tobytes() for m in ref]
        checked += 1
    assert checked > 5


# ----------------------------------------------------------------------------- texture stream step search
def _flat(ll, bands):
    return np.concatenate([ll.ravel()] + [b.ravel() for trio in bands for b in trio])


@pytest.mark.parametrize("shape", [(64, 64), (128, 64), (64, 256), (96, 160), (512, 512)])
def test_stream_reconstruction_matches_numpy(shape):
    """texture_stream.choose._recon (quantize, inverse 5/3 wavelet) for luma and chroma steps, with and
    without the finest levels."""
    from texture_stream import choose, codec
    h, w = shape
    levels = codec.levels_for(w, h)
    rng = np.random.default_rng(h * 31 + w)
    for scale in (0.3, 1.0, 6.0):
        ll, bands = codec.dwt(rng.normal(0, 40, (h, w)) * scale, levels)
        for step in (2, 3, 7, 16, 66, 132):
            for drop in range(min(levels, 2) + 1):
                ref = choose._recon(ll, bands, step, drop, levels)
                got = native.ts_recon(_flat(ll, bands), h, w, levels, step, drop)
                assert got.tobytes() == np.ascontiguousarray(ref, np.float64).tobytes()


def _same_float(a, b) -> bool:
    return isinstance(a, np.float64) and np.float64(a).tobytes() == np.float64(b).tobytes()


@pytest.mark.parametrize("shape", [(1, 1), (1, 3), (5, 1), (7, 9), (8, 8), (13, 10), (64, 64), (100, 37), (256, 512),
                                   (333, 1025)])
def test_stream_mean_follows_numpy_summation(shape):
    """np.mean of the squared errors (pairwise summation over the whole array) for sizes on every side of
    its block limits: fewer than 8 values, up to 128, odd halves."""
    h, w = shape
    rng = np.random.default_rng(h * 1000 + w)
    plane = rng.normal(0, 60, (h, w))
    reference = rng.integers(0, 256, (h, w)).astype(np.float64)
    assert _same_float(native.ts_mse_plane(plane, reference), np.mean(native.ts_sqerr_plane(plane, reference)))
    y, co, cg = rng.normal(0, 70, (3, h, w))
    rgb = rng.integers(0, 256, (h, w, 3)).astype(np.float64)
    assert _same_float(native.ts_mse_ycocg(y, co, cg, rgb), np.mean(native.ts_sqerr_ycocg(y, co, cg, rgb)))
    assert _same_float(native.ts_mse_normal(y, co, rgb), np.mean(native.ts_sqerr_normal(y, co, rgb)))
    # an exact reconstruction: the mean is zero
    exact = reference - 128.0
    assert _same_float(native.ts_mse_plane(exact, reference), np.float64(0.0))


def test_stream_squared_errors_match_numpy():
    """The arrays numpy averages in codec.psnr: colour from Y/Co/Cg, a single plane, unit normals."""
    from texture_stream import codec
    rng = np.random.default_rng(5)
    for h, w in [(8, 8), (64, 32), (130, 70)]:
        y, co, cg = rng.normal(0, 70, (3, h, w))
        rgb = rng.integers(0, 256, (h, w, 3)).astype(np.float64)
        ref = (np.clip(np.round(codec.ycocg_to_rgb(y, co, cg)), 0, 255) - rgb) ** 2
        assert native.ts_sqerr_ycocg(y, co, cg, rgb).tobytes() == ref.tobytes()
        assert _same_float(native.ts_mse_ycocg(y, co, cg, rgb), np.mean(ref))
        # values exactly on .5 round to even, like np.round
        half = np.round(y) + 0.5
        ref = (np.clip(np.round(half + 128), 0, 255) - rgb[..., 0]) ** 2
        assert native.ts_sqerr_plane(half, rgb[..., 0]).tobytes() == ref.tobytes()
        assert _same_float(native.ts_mse_plane(half, rgb[..., 0]), np.mean(ref))
        n0 = codec.unit_normals_numpy(rng.integers(0, 256, (h, w, 3)).astype(np.float64))
        for scale in (0.5, 2.0, 4.0):           # 4.0: many vectors off the unit disc (clipped to z = 0)
            px, py = [np.clip(r + 128, 0, 255) for r in (y * scale, co * scale)]
            ref = (codec.unit_normals_numpy(np.stack([px, py, codec.normal_z(px, py)], -1)) - n0) ** 2
            assert native.ts_sqerr_normal(y * scale, co * scale, n0).tobytes() == ref.tobytes()
            assert _same_float(native.ts_mse_normal(y * scale, co * scale, n0), np.mean(ref))


def test_stream_choices_are_the_same_with_and_without_the_library(monkeypatch):
    """choose() end to end on a colour texture, a normal map and one with alpha: same steps and the same
    measured qualities, to the last bit, as the numpy path."""
    from texture_stream import choose as choose_module
    from texture_stream import codec

    class V:
        def __init__(self, w, h, fmt):
            self.width, self.height, self.fmt = w, h, fmt

    rng = np.random.default_rng(9)
    yy, xx = np.mgrid[0:256, 0:256]
    smooth = np.stack([128 + 90 * np.sin(xx / 17.0), 128 + 70 * np.cos(yy / 11.0), 128 + 60 * np.sin((xx + yy) / 23.0)], -1)
    colour = np.concatenate([np.clip(smooth + rng.normal(0, 6, smooth.shape), 0, 255).round(),
                             np.clip(128 + 100 * np.sin(xx / 9.0), 0, 255).round()[..., None]], -1)
    nx, ny = 0.4 * np.sin(xx / 13.0), 0.4 * np.cos(yy / 19.0)
    normal = np.concatenate([np.round((np.stack([nx, ny, np.sqrt(1 - nx * nx - ny * ny)], -1) + 1) * 127.5),
                             np.full((256, 256, 1), 255.0)], -1)
    cases = [(V(256, 256, 15), colour, {"w": 1.0, "roles": {"base"}}),
             (V(256, 256, 13), colour, None),
             (V(256, 256, 15), normal, {"w": None, "roles": {"normal"}})]
    with_library = [choose_module.choose(v, rgba, use, 34.0) for v, rgba, use in cases]
    monkeypatch.setattr(choose_module.native, "available", lambda: False)
    without = [choose_module.choose(v, rgba, use, 34.0) for v, rgba, use in cases]
    assert with_library == without
    assert with_library[2]["normal"] and with_library[0]["alpha"] and not with_library[1]["normal"]


@pytest.mark.parametrize("shape", [(64, 64), (128, 64), (64, 256), (96, 160), (512, 512)])
def test_stream_forward_transform_matches_numpy(shape):
    """texture_stream.codec.dwt (5/3 lifting, horizontal then vertical, every level)."""
    from texture_stream import codec
    h, w = shape
    levels = codec.levels_for(w, h)
    rng = np.random.default_rng(h * 7 + w)
    for scale in (0.5, 30.0):
        plane = rng.normal(0, 50, (h, w)) * scale
        ll_ref, bands_ref = codec.dwt_numpy(plane, levels)
        ll, bands = native.ts_dwt(plane, levels)
        assert ll.tobytes() == ll_ref.tobytes()
        assert len(bands) == len(bands_ref) == levels
        for trio, trio_ref in zip(bands, bands_ref):
            for band, band_ref in zip(trio, trio_ref):
                assert band.shape == band_ref.shape
                assert band.tobytes() == np.ascontiguousarray(band_ref).tobytes()
        # and what the encoder calls
        ll2, _bands2 = codec.dwt(plane, levels)
        assert ll2.tobytes() == ll_ref.tobytes()


def test_stream_band_bytes_match_numpy():
    """codec.put_band(quant()) and codec.put_band_nf(noise_codes()): int8 and int16 bands, bands of zeros,
    bands smaller than a noise tile and with sides that are not multiples of one."""
    from texture_stream import codec
    rng = np.random.default_rng(11)
    for h, w in [(1, 1), (4, 4), (8, 8), (16, 40), (12, 20), (64, 64), (33, 70)]:
        for scale in (0.01, 3.0, 40.0, 900.0):
            band = rng.normal(0, 1, (h, w)) * scale
            band[rng.random((h, w)) < 0.3] = 0.0
            for st in (2, 3.0, 7, 16.0, 132):
                q = codec.quant(band, st)
                ref = bytearray()
                codec.put_band(ref, q)
                assert native.ts_put_band(band, st, False) == bytes(ref)
                ref = bytearray()
                codec.put_band_nf(ref, q, codec.noise_codes(band, q, st))
                assert native.ts_put_band(band, st, True) == bytes(ref)
                for noise in (False, True):
                    out = bytearray(b"x")
                    codec.put_quant(out, band, st, noise=noise)
                    assert bytes(out[1:]) == native.ts_put_band(band, st, noise)
    # values right on the edge of a quantization bin and of a noise code
    edge = (np.arange(64 * 64).reshape(64, 64) % 41 - 20) * 0.3
    for st in (1.0, 2, 3.0):
        q = codec.quant(edge, st)
        ref = bytearray()
        codec.put_band_nf(ref, q, codec.noise_codes(edge, q, st))
        assert native.ts_put_band(edge, st, True) == bytes(ref)
    # out of the int16 range: the Python code raises, the library steps aside
    assert native.ts_put_band(np.full((8, 8), 1e6), 2, False) is None
    with pytest.raises(ValueError):
        codec.put_quant(bytearray(), np.full((8, 8), 1e6), 2)


def test_stream_colour_planes_match_numpy():
    """codec.rgb_to_ycocg and codec.unit_normals on the layouts the encoder hands over: the decoded RGBA
    image, its [..., :3] view, a copy, and views the library has to copy first."""
    from texture_stream import codec
    rng = np.random.default_rng(13)
    for h, w in [(4, 4), (64, 32), (130, 70)]:
        rgba = rng.integers(0, 256, (h, w, 4)).astype(np.float64)
        for image in (rgba, rgba[..., :3], np.ascontiguousarray(rgba[..., :3]), rgba[::-1, :, :3], rgba[:, ::2, :3]):
            got, ref = native.ts_ycocg(image), codec.rgb_to_ycocg_numpy(image)
            assert len(got) == len(ref) == 3
            for a, b in zip(got, ref):
                assert a.shape == b.shape and a.tobytes() == np.ascontiguousarray(b).tobytes()
            for a, b in zip(codec.rgb_to_ycocg(image), ref):
                assert a.tobytes() == np.ascontiguousarray(b).tobytes()
        for image in (rgba[..., :3], np.ascontiguousarray(rgba[..., :3]), rgba[::-1, :, :3], rgba[:, ::2, :3]):
            ref = codec.unit_normals_numpy(image)
            assert native.ts_unit_normals(image).tobytes() == ref.tobytes()
            assert codec.unit_normals(image).tobytes() == ref.tobytes()
    # the vector of length zero (the clamp on the length) and uint8 input, which stays with numpy
    grey = np.full((4, 4, 3), 127.5)
    assert native.ts_unit_normals(grey).tobytes() == codec.unit_normals_numpy(grey).tobytes()
    small = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
    assert native.ts_ycocg(small) is None
    for a, b in zip(codec.rgb_to_ycocg(small), codec.rgb_to_ycocg_numpy(small)):
        assert a.tobytes() == b.tobytes()


def test_stream_plane_reuses_the_transform_array():
    """The step search reads the coefficients straight from the array the transform filled."""
    from texture_stream import choose, codec
    plane = np.random.default_rng(3).normal(0, 40, (128, 64))
    levels = codec.levels_for(64, 128)
    ll, bands = native.ts_dwt(plane, levels)
    flat = choose._flat_view(ll, bands)
    assert flat is ll.base and flat.tobytes() == _flat(ll, bands).tobytes()
    # arrays of their own (the numpy transform), or the same pieces in another order: no shared array
    assert choose._flat_view(*codec.dwt_numpy(plane, levels)) is None
    assert choose._flat_view(ll, list(reversed(bands))) is None
