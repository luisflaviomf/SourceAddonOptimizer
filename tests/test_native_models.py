"""The Rust seam pass (gmo_native.dll, gmo_seams) must give exactly what the Python code gives."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model_screen import simplify  # noqa: E402
from texlab import native  # noqa: E402

pytestmark = pytest.mark.skipif(not native.available(), reason="gmo_native.dll not built")

LVS = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS/lvs_cars_pack")


def test_set_emulation_iterates_like_python_sets():
    rnd = random.Random(5)
    for trial in range(3000):
        keys = [rnd.randrange(rnd.choice([8, 64, 1000, 10 ** 6, 2 ** 40])) for _ in range(rnd.choice([1, 2, 5, 9, 40, 400]))]
        if trial % 3 == 0:
            keys.sort()
        s = set()
        for k in keys:
            s.add(k)
        assert native.pyset_order(keys) == list(s)


def _island_mesh(seed: int, n: int = 14, islands: int = 4):
    """A wavy n x n grid cut into vertical UV islands: border vertices are duplicated (welded by
    position, different UVs), the layout compiled Source models have."""
    rng = np.random.default_rng(seed)
    xs = np.linspace(0.0, 30.0, n)
    cut = set(np.linspace(0, n - 1, islands + 1).astype(int)[1:-1].tolist())
    verts, ids = [], {}
    for i in range(n):
        for j in range(n):
            owners = [j // max(1, (n - 1) // islands)] + ([j // max(1, (n - 1) // islands) - 1] if j in cut else [])
            z = np.sin(xs[i] * 0.3) * 2.0 + rng.normal(0, 0.002)   # one position per point: seams weld
            for isl in owners:
                nrm = np.array([rng.normal(0, 0.02), rng.normal(0, 0.02), 1.0])
                nrm /= np.linalg.norm(nrm)
                uv = (xs[j] / 30.0 + isl * 0.01, xs[i] / 30.0)
                ids[(i, j, isl)] = len(verts)
                verts.append([xs[j], xs[i], z, *nrm, *uv])
    f = np.array(verts, np.float32)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            isl = j // max(1, (n - 1) // islands)
            a, b = ids.get((i, j, isl)), ids.get((i, j + 1, isl))
            c, d = ids.get((i + 1, j, isl)), ids.get((i + 1, j + 1, isl))
            if None in (a, b, c, d):
                continue
            tris += [[a, b, d], [a, d, c]]
    T = np.array(tris, np.int64)
    bkey = np.zeros(len(f), np.int64)
    bkey[: len(f) // 7] = 3   # a bone change makes some corners
    return f, bkey, T


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("profile", ["balanced", "strong"])
def test_seams_native_matches_python_on_synthetic_islands(seed, profile):
    f, bkey, T = _island_mesh(seed)
    wid = simplify.weld_ids(f[:, 0:3])
    uvtol = np.full(len(f), 1.0 / 256.0)
    p = simplify.PROFILES[profile]
    T_py, T_rs = T.copy(), T.copy()
    a, ra = simplify._seams(f, bkey, wid, T_py, uvtol, p, use_native=False)
    b, rb = simplify._seams(f, bkey, wid, T_rs, uvtol, p, use_native=True)
    assert ra == rb
    assert np.array_equal(a, b) and np.array_equal(T_py, T_rs)


@pytest.mark.skipif(not LVS.is_dir(), reason="LVS cars pack not present")
@pytest.mark.parametrize("rel", ["models/diggercars/mb_w123/w123.mdl", "models/diggercars/dodge_charger/charger.mdl"])
def test_simplify_model_native_matches_python_on_real_cars(rel, monkeypatch):
    from model_screen.uvtol import mesh_uv_tolerance
    from texlab.materials import MaterialResolver
    mdl = LVS / rel
    if not mdl.exists():
        pytest.skip("model not in this copy of the pack")
    uvt = mesh_uv_tolerance(mdl, MaterialResolver([LVS]))
    for profile in ("balanced", "strong"):
        p = simplify.PROFILES[profile]
        fast, st_fast = simplify.simplify_model(mdl, p, uvt)
        orig = simplify._seams
        monkeypatch.setattr(simplify, "_seams", lambda *a, **k: orig(*a, use_native=False))
        slow, st_slow = simplify.simplify_model(mdl, p, uvt)
        monkeypatch.setattr(simplify, "_seams", orig)
        assert st_fast == st_slow
        assert fast.keys() == slow.keys()
        assert all(np.array_equal(fast[k], slow[k]) for k in fast)
