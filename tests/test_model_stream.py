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
    # one strip mesh: the game takes 65535 vertices, and a strip is cut at an even offset
    limit = int(re.search(r"local MAX_STRIP = (\d+)", lua).group(1))
    assert limit == encoder.MAX_STRIP and limit <= 65535 and limit % 2 == 0


def _strip_triangles(strip):
    """Triangles a strip draws: degenerate joins skipped, winding flipped on odd positions."""
    out = []
    s = [int(x) for x in strip]
    for k in range(len(s) - 2):
        a, b, c = s[k], s[k + 1], s[k + 2]
        if a != b and b != c and a != c:
            out.append(_canon((a, b, c) if k % 2 == 0 else (b, a, c)))
    return out


def test_strips_fit_one_game_mesh():
    """Triangles that share no vertex strip into 5 indices each: 20000 of them made one strip of ~100000
    vertices, which the game cannot put in a mesh (mesh.Begin raised an error and the client froze; one
    Glide car of the test bench, 2026-11-10)."""
    n = encoder.CHUNK_TRIS
    loose = np.arange(n * 3, dtype=np.int64).reshape(n, 3)
    assert len(encoder.stripify(loose, n * 3)) > 65535          # what the encoder used to write
    grid = np.array([[i, i + 1, i + 2] for i in range(3000)], np.int64) + n * 3   # a well connected run
    tris = np.concatenate([loose, grid])
    strips = encoder.strips_for(tris, n * 3 + 3002)
    assert max(len(s) for s in strips) <= encoder.MAX_STRIP
    from collections import Counter
    got = Counter(t for s in strips for t in _strip_triangles(s))
    assert got == Counter(_canon(tuple(int(x) for x in t)) for t in tris)


RUNTIME_STUBS = r"""
SERVER = false
MATERIAL_TRIANGLE_STRIP = 5
bit = require("bit")
hook = { Add = function() end }
timer = { Create = function() end, Simple = function() end }
render = { MaterialOverride = function() end, ModelMaterialOverride = function() end,
           MaterialOverrideByIndex = function() end }
function CreateClientConVar(_, default)
    return { GetBool = function() return default ~= "0" end, GetFloat = function() return tonumber(default) end }
end
local ENT = {}
ENT.__index = ENT
function ENT:DrawModel() end
function ENT:GetModel() return MODEL end
function FindMetaTable() return ENT end
function NewEntity() return setmetatable({}, ENT) end
function Vector()
    local v = { 0, 0, 0 }
    function v:SetUnpacked(x, y, z) self[1], self[2], self[3] = x, y, z end
    return v
end
MESHES = {}
local cur
function Mesh() return { n = 0 } end
mesh = {}
function mesh.Begin(obj, kind, prims)
    assert(kind == MATERIAL_TRIANGLE_STRIP and cur == nil)
    -- the game's own check
    if prims + 2 > MESH_LIMIT then
        error("called mesh.Begin with a primitive count which results in too many vertices (" .. (prims + 2) .. ")")
    end
    cur = obj
    obj.prims = prims
    MESHES[#MESHES + 1] = obj
end
function mesh.Position(v)
    local n = cur.n
    cur[n + 1], cur[n + 2], cur[n + 3] = v[1], v[2], v[3]
    cur.n = n + 3
end
function mesh.Normal() end
function mesh.TexCoord() end
function mesh.UserData() end
function mesh.AdvanceVertex() end
function mesh.End()
    assert(cur.n == (cur.prims + 2) * 3)
    cur = nil
end
file = { Read = function(path, where) return PY_READ(path, where) end }
util = { Decompress = function(s) return PY_DECOMPRESS(s) end }
function Material(name) return { IsError = function() return false end } end
function Matrix(rows) return rows end
function SysTime() return os.clock() end
function RealTime() return os.clock() end
function ErrorNoHalt(s) LAST_ERROR = s end
"""


def _runtime_meshes(stream: bytes, max_strip: int, mesh_limit: int):
    """Run the shipped runtime.lua in LuaJIT on one stream; returns (error text or None, list of meshes,
    each a flat list of positions)."""
    import lzma
    from lupa import luajit21
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    src = (ROOT / "model_stream" / "runtime.lua").read_text(encoding="utf-8")
    assert src.count("local MAX_STRIP = %d " % encoder.MAX_STRIP) == 1
    src = src.replace("local MAX_STRIP = %d " % encoder.MAX_STRIP, "local MAX_STRIP = %d " % max_strip)

    def decompress(data):
        d = bytearray(data)
        d[5:13] = bytes([255]) * 8
        return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)

    g = rt.globals()
    g[b"PY_READ"] = lambda path, where: stream
    g[b"PY_DECOMPRESS"] = decompress
    g[b"MODEL"] = b"models/test/car.mdl"
    g[b"MESH_LIMIT"] = mesh_limit
    rt.execute(RUNTIME_STUBS.encode())
    rt.execute(src.encode("utf-8"))
    rt.execute(b"""
        GMOS.AddManifest({ [MODEL] = "data_static/gmo/test.dat" })
        local ent = NewEntity()
        for _ = 1, 200000 do
            if GMOS.Ready(MODEL) then break end
            ent:DrawModel(0)
        end
        assert(GMOS.Ready(MODEL), "the stream never got ready")
    """)
    err = g[b"LAST_ERROR"]
    meshes = [[m[i] for i in range(1, int(m[b"n"]) + 1)] for m in g[b"MESHES"].values()]
    return (err.decode() if err else None), meshes


@need_lvs
def test_runtime_cuts_long_strips_into_meshes_the_game_takes():
    """The shipped Lua, run in LuaJIT: with the mesh size lowered so that this model's strips are too long,
    every mesh stays within the limit and the meshes draw exactly the triangles of the stream."""
    from collections import Counter
    data = encoder.encode(E46, _resolver())
    d = encoder.decode(data)
    q = d["posq"]
    want = Counter()
    for g in d["groups"]:
        P = np.round(g["pos"] / q).astype(np.int64)
        for tri in g["tris"]:
            a, b, c = (tuple(P[i]) for i in tri)
            if a != b and b != c and a != c:
                want[_canon((a, b, c))] += 1
    longest = max(length for g in d["groups"] for length in g["lens"])
    limit = 1000
    assert longest > 4 * limit                      # the cut really happens, several times per strip
    # uncut (limit above every strip): the reference, and the proof that the game's check would fire
    err, _meshes = _runtime_meshes(data, max_strip=10 ** 9, mesh_limit=limit + 1)
    assert err and "too many vertices" in err
    err, meshes = _runtime_meshes(data, max_strip=limit, mesh_limit=limit + 1)
    assert err is None
    got = Counter()
    for m in meshes:
        P = np.round(np.array(m, np.float64).reshape(-1, 3) / q).astype(np.int64)
        assert len(P) <= limit
        for k in range(len(P) - 2):
            a, b, c = tuple(P[k]), tuple(P[k + 1]), tuple(P[k + 2])
            if a != b and b != c and a != c:
                got[_canon((a, b, c) if k % 2 == 0 else (b, a, c))] += 1
    assert got == want


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
def test_fast_geometry_decoder_matches_reference():
    from model_stream import geometry
    assert geometry.VERSION == encoder.VERSION
    data = encoder.encode(E46, _resolver())
    ref = encoder.decode(data)["groups"]
    fast = geometry.decode_geometry(data)
    assert len(fast) == len(ref)
    for r, g in zip(ref, fast):
        assert (r["b"], r["m"], r["slot"], r["bone"]) == (g["b"], g["m"], g["slot"], g["bone"])
        assert np.array_equal(r["pos"].astype(np.float32), g["pos"])
        assert np.array_equal(r["uv"].astype(np.float32), g["uv"])
        assert np.array_equal(r["tris"], g["tris"])


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
    # the author's addon.json keeps .dat files out of the published addon: the stream takes the next type
    assert model_stream.stream_name("models/a.mdl").endswith(".dat")
    (addon / "addon.json").write_text(json.dumps({"title": "e46", "ignore": ["*.psd", "*.dat"]}))
    assert main([str(addon), "--suffix", "_o", "--profile", "stream", "--jobs", "2"]) == 0
    out = tmp_path / "lvs" / "addon_o"
    summary = json.loads((tmp_path / "lvs" / "addon_o_work" / "logs" / "modscreen_summary.json").read_text())
    assert summary["failed"] == 0 and summary["profile"] == "stream" and summary["streamed"] >= 1
    rows = {Path(r["model"]).name: r for r in summary["rows"]}
    assert rows["e46.mdl"].get("stream") and not rows["wheel.mdl"].get("stream")   # wheels stay models
    stream = out / rows["e46.mdl"]["stream"]
    assert stream.read_bytes()[:4] == b"GMOS" and stream.suffix == ".txt"
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
    # texture density of a streamed model is measured on the stream (what is drawn), not the coarse mesh
    from texlab.density import _model_usage, usage_tasks
    tasks = {Path(t[0]).name: t for t in usage_tasks(out)}
    assert Path(tasks["e46.mdl"][3]) == stream and len(tasks["wheel.mdl"]) == 3
    src_task = next(t for t in usage_tasks(addon) if Path(t[0]).name == "e46.mdl")
    def stats(rows):
        return sum(len(r[7]) for r in rows), sum(float(np.sum(r[9])) for r in rows)
    (nw, aw), (ng, ag) = stats(_model_usage(src_task)), stats(_model_usage(tasks["e46.mdl"]))
    nc, _ac = stats(_model_usage(tasks["e46.mdl"][:3]))
    # (triangles mapped to one texture point - solid colour parts - collapse at the stream's 1/4096 UV
    # quantization and drop out of the measure: ~0.6% fewer rows, ~0.4% less area on the E46)
    assert abs(ng - nw) <= 0.01 * nw and abs(ag - aw) <= 0.01 * aw and nc < 0.1 * nw


def test_texscreen_strong_profile_caps_detail(tmp_path):
    from texlab import screen_compress as sc
    assert "strong" in sc.PROFILES and sc.PROFILE_OPTIONS["strong"]["caps"] == {"$detail": 2}
    # 8x only for textures that never show stretched at a close look (liveries with text stop at 4x)
    assert sc.PROFILE_OPTIONS["strong"]["magnify"][3] == 3
    # lettering by file name and small textures stay at 2x
    assert sc.PROFILE_OPTIONS["strong"]["text_cap"] == 1 and sc.PROFILE_OPTIONS["strong"]["small_cap"] == (100 * 1024, 1)
    for name in ("police_sign_1", "gauges", "badges", "int_decals", "tx_clustertxt", "cmbplate", "dials_e"):
        assert sc.TEXT_NAMES.search(name) and not sc.NORMAL_SUFFIX.search(name), name
    assert sc.NORMAL_SUFFIX.search("badges_nm") and not sc.TEXT_NAMES.search("chassis")
    assert "small_cap" not in sc.PROFILE_OPTIONS.get("q50", {})
    mat = tmp_path / "materials" / "m"
    mat.mkdir(parents=True)
    (mat / "paint.vmt").write_text('"VertexLitGeneric"\n{\n "$basetexture" "m/base"\n "$detail" "m/ao"\n}\n')
    caps = sc._param_caps(tmp_path, {"$detail": 2})
    assert caps == {"m/ao": 2}
