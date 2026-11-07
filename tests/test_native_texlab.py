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
