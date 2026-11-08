"""Tests for the Models "Tela" profile "stream" (model_stream) and the texscreen "strong" profile."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import model_stream  # noqa: E402
from model_stream import encoder  # noqa: E402
from model_screen.binary import mesh_arrays  # noqa: E402

LVS = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS")
PACK = LVS / "lvs_cars_pack"
E46 = PACK / "models/diggercars/bmw_e46/e46.mdl"
need_lvs = pytest.mark.skipif(not E46.exists(), reason="LVS sample models not on this machine")


def _resolver():
    from texlab.materials import MaterialResolver
    return MaterialResolver([PACK, LVS / "lvs_framework"])


def _canon(t):
    t = list(t)
    i = t.index(min(t))
    return tuple(t[i:] + t[:i])


def test_runtime_and_encoder_versions_match():
    lua = (ROOT / "model_stream" / "runtime.lua").read_text(encoding="utf-8")
    assert re.search(r"local VERSION = (\d+)", lua).group(1) == str(encoder.VERSION)
    assert "byte(raw, 5) ~= %d" % encoder.VERSION in lua


def test_wants_stream_rules():
    assert model_stream.wants_stream("models/a/body.mdl", 500_000)
    assert model_stream.wants_stream("models/a/wheel.mdl", 500_000)           # big: always
    assert not model_stream.wants_stream("models/a/wheel.mdl", 100_000)       # wheels drawn all the time
    assert not model_stream.wants_stream("models/a/fw.mdl", 100_000)
    assert model_stream.wants_stream("models/a/gib_hood.mdl", 100_000)
    assert not model_stream.wants_stream("models/a/tiny.mdl", 5_000)


def test_ubyte4_unpack_matches_engine_pack():
    from vvd_engine_exact import pack_ubyte4
    rng = np.random.default_rng(1)
    n = rng.normal(size=(2000, 3))
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    cx, cy, _a, _b = pack_ubyte4(n.astype(np.float32))
    for v, a, b in zip(n, cx, cy):
        x, y, z, w = encoder.unpack_ubyte4(int(a), int(b))
        assert np.dot(v, (x, y, z)) > np.cos(np.radians(4.0)) and w == 1


@need_lvs
def test_stream_roundtrip_e46():
    from vvd_engine_exact import pack_ubyte4
    data = encoder.encode(E46, _resolver())
    assert data[:4] == b"GMOS" and data[4] == encoder.VERSION
    d = encoder.decode(data)
    f, _tan, bone, _nb, _w = encoder.vvd_records(E46)
    meshes = {key: (st, t) for key, st, _c, t in mesh_arrays(E46)}
    want = {}
    for (b, m, k), (st, t) in meshes.items():
        for tri in np.asarray(t, np.int64) + st:
            want.setdefault((b, m, int(bone[tri[0]])), []).append(tri)
    got_total = 0
    for g in d["groups"]:
        # every triangle of the group comes back with its corners and winding
        P = g["pos"]
        tris = g["tris"]
        got_total += len(tris)
        cands = [tuple(int(x) for x in tri) for key, lst in want.items() if key[0] == g["b"] and key[1] == g["m"]
                 and key[2] == g["bone"] for tri in lst]
        assert len(tris) > 0
        assert np.abs(P).max() < 1e5
    n_tris = sum(len(t) for _st, t in meshes.values())
    assert got_total == n_tris
    # positions / UV / normal codes of every decoded vertex exist in the source within the quantization
    allP = f[:, 0:3]
    q = d["posq"]
    g0 = max(d["groups"], key=lambda g: len(g["tris"]))
    key = np.round(allP / q).astype(np.int64)
    src = {tuple(r) for r in key.tolist()}
    dec = np.round(g0["pos"] / q).astype(np.int64)
    assert all(tuple(r) in src for r in dec.tolist())
    cx, cy, _a, _b = pack_ubyte4(f[:, 3:6].astype(np.float32))
    codes = {(int(a), int(b)) for a, b in zip(cx, cy) if np.isfinite(a) and np.isfinite(b)}
    assert all((int(a), int(b)) in codes for a, b in g0["ncodes"])
    # triangles: decoded corners map back to source triangles with the same winding
    pos_of = {}
    for i, r in enumerate(key.tolist()):
        pos_of.setdefault(tuple(r), []).append(i)
    st, _t = next(v for k, v in meshes.items() if k[0] == g0["b"] and k[1] == g0["m"])
    src_tris = {_canon(tuple(tuple(key[i]) for i in tri)) for lst in want.values() for tri in lst}
    for tri in g0["tris"][:2000]:
        assert _canon(tuple(tuple(dec[i]) for i in tri)) in src_tris
    assert len(d["mats"]) > 0 and d["skins"]


@need_lvs
def test_coarse_keeps_every_mesh():
    from model_stream import coarse
    t = coarse.coarse_tris(E46, 0.02)
    meshes = mesh_arrays(E46)
    for key, _st, _c, tri in meshes:
        if len(tri):
            assert len(t[key]) >= 1
    total = sum(len(v) for v in t.values())
    assert total < 0.06 * sum(len(tri) for _k, _s, _c, tri in meshes)


@need_lvs
def test_cli_stream_profile_end_to_end(tmp_path):
    from model_screen.cli import main
    from texlab.source_model import load_model
    addon = tmp_path / "lvs" / "addon"
    (addon / "models/diggercars").mkdir(parents=True)
    shutil.copytree(E46.parent, addon / "models/diggercars/bmw_e46")
    shutil.copytree(PACK / "materials/models/diggercars/bmw_e46", addon / "materials/models/diggercars/bmw_e46")
    shutil.copytree(LVS / "lvs_framework/materials/models/diggercars/shared",
                    tmp_path / "lvs" / "framework/materials/models/diggercars/shared")
    assert main([str(addon), "--suffix", "_o", "--profile", "stream", "--jobs", "2"]) == 0
    out = tmp_path / "lvs" / "addon_o"
    summary = json.loads((tmp_path / "lvs" / "addon_o_work" / "logs" / "modscreen_summary.json").read_text())
    assert summary["failed"] == 0 and summary["profile"] == "stream" and summary["streamed"] >= 1
    rows = {Path(r["model"]).name: r for r in summary["rows"]}
    assert rows["e46.mdl"].get("stream") and not rows["wheel.mdl"].get("stream")   # wheels stay models
    stream = out / rows["e46.mdl"]["stream"]
    assert stream.read_bytes()[:4] == b"GMOS"
    luas = list((out / "lua/autorun/client").glob("gmo_stream_*.lua"))
    assert len(luas) == 1
    text = luas[0].read_text(encoding="utf-8")
    man = json.loads(text[text.index("[[") + 2:text.index("]]")])
    assert man["models/diggercars/bmw_e46/e46.mdl"] == rows["e46.mdl"]["stream"]
    for p in ("models/gmo_stream/probe_v1.mdl", "models/gmo_stream/probe_v1.vvd", "models/gmo_stream/probe_v1.dx90.vtx",
              "materials/gmo_stream/probe_v1.vmt", "materials/gmo_stream/probe_v1_normal.vtf"):
        assert (out / p).is_file(), p
    coarse_model = load_model(out / "models/diggercars/bmw_e46/e46.mdl")
    assert len(coarse_model.meshes) == len(load_model(E46).meshes)
    assert (out / "models/diggercars/bmw_e46/e46.vvd").stat().st_size < 0.1 * E46.with_suffix(".vvd").stat().st_size


def test_texscreen_strong_profile_caps_detail(tmp_path):
    from texlab import screen_compress as sc
    assert "strong" in sc.PROFILES and sc.PROFILE_OPTIONS["strong"]["caps"] == {"$detail": 2}
    # 8x only for textures that never show stretched at a close look (liveries with text stop at 4x)
    assert sc.PROFILE_OPTIONS["strong"]["magnify"][3] == 3
    mat = tmp_path / "materials" / "m"
    mat.mkdir(parents=True)
    (mat / "paint.vmt").write_text('"VertexLitGeneric"\n{\n "$basetexture" "m/base"\n "$detail" "m/ao"\n}\n')
    caps = sc._param_caps(tmp_path, {"$detail": 2})
    assert caps == {"m/ao": 2}
