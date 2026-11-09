"""Tests for the texture stream (texture_stream package): GMOT codec, coding choices, addon conversion and
the Lua decoder/runtime (run in LuaJIT through lupa when it is installed)."""
from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import texture_stream  # noqa: E402
from texture_stream import choose, codec  # noqa: E402
from texlab.vtf import decode_level, read_vtf, write_vtf  # noqa: E402


def smooth_image(w, h, alpha=False, seed=0):
    """a texture-like image: gradients, a few soft shapes and mild noise (compresses like real art)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / max(w, h)
    r = 120 + 80 * np.sin(6 * x) * np.cos(4 * y)
    g = 100 + 60 * np.cos(5 * x + 2 * y)
    b = 90 + 70 * (x * y)
    img = np.stack([r, g, b, 255 * np.ones_like(r)], -1)
    img[..., :3] += rng.normal(0, 3, (h, w, 3))
    if alpha:
        img[..., 3] = 128 + 120 * np.sin(9 * x)
    return np.clip(img, 0, 255)


def make_dxt_vtf(img, fmt=13, flags=0) -> bytes:
    """DXT1/DXT5 VTF 7.2 of `img` with a full mip chain (encoded with the decoder's own DXT encoder)."""
    h, w = img.shape[:2]
    mips = max(w, h).bit_length()
    header = bytearray(80)
    header[0:4] = b"VTF\0"
    struct.pack_into("<3I", header, 4, 7, 2, 80)
    struct.pack_into("<HHIHH", header, 16, w, h, flags, 1, 0)
    struct.pack_into("<f", header, 48, 1.0)
    struct.pack_into("<iBiBB", header, 52, fmt, mips, -1, 0, 0)
    struct.pack_into("<H", header, 63, 1)
    template = read_vtf(bytes(header) + b"\0" * 10_000_000)
    levels, cur, cw, ch = [], img.astype(np.float64), w, h
    for m in range(mips):
        cb = codec.lua_dxt1(codec.blocks(cur[..., :3], cw, ch))
        if fmt == 15:
            ab = codec.lua_alpha(codec.blocks(cur[..., 3:4], cw, ch)[..., 0])
            levels.append(np.concatenate([ab, cb], 1).tobytes())
        else:
            levels.append(cb.tobytes())
        if m < mips - 1:
            nxt = [codec.down2(cur[..., c], cw, ch) for c in range(4)]
            cur, cw, ch = np.stack([n[0] for n in nxt], -1), nxt[0][1], nxt[0][2]
    return write_vtf(template, fmt, w, h, levels, flags=flags)


def vtf_and_rgba(img, fmt=13):
    v = read_vtf(make_dxt_vtf(img, fmt))
    return v, decode_level(v, 0).astype(np.float64)


# ------------------------------------------------------------------ codec
def test_lcg_noise_matches_the_sequential_generator():
    seed = codec.band_seed(codec.seed_index(1, 3, 2))
    x, seq = seed, []
    for _ in range(50):
        x = (codec.LCG_A * x + codec.LCG_C) % codec.M32
        seq.append(x / codec.M32)
    assert np.array_equal(codec.lcg_uniform(seed, 50), np.array(seq))


def test_roundtrip_keeps_the_target_quality_and_writes_a_valid_vtf():
    v, rgba = vtf_and_rgba(smooth_image(256, 256))
    c = choose.choose(v, rgba, None, 34.0)
    assert not c["normal"] and not c["alpha"] and c["rgb_db"] >= 34.0
    blob = codec.encode(v, rgba, c["step"], chroma_drop=c["drop"], fill_levels=2)
    assert blob[:5] == b"GMOT\x02"
    out = read_vtf(codec.lua_vtf(blob))
    assert (out.width, out.height, out.fmt, out.mip_count) == (256, 256, 13, v.mip_count)
    assert codec.psnr(decode_level(out, 0)[..., :3], rgba[..., :3]) >= 30.0
    assert len(blob) < len(make_dxt_vtf(smooth_image(256, 256))) / 3


def test_preview_is_the_texture_at_512_with_its_lower_mips():
    v, rgba = vtf_and_rgba(smooth_image(1024, 1024))
    blob = codec.encode(v, rgba, 8, chroma_drop=1, fill_levels=2)
    nchunks = blob[5]
    assert nchunks > 1                               # the finest level comes in its own chunks
    full, prev = read_vtf(codec.lua_vtf(blob)), read_vtf(codec.lua_vtf(blob, preview=True))
    assert (full.width, full.mip_count) == (1024, v.mip_count)
    assert (prev.width, prev.height, prev.mip_count) == (512, 512, v.mip_count - 1)
    # the preview's top level is the full texture's second mip, up to coding noise
    assert codec.psnr(decode_level(prev, 0), decode_level(full, 1)) > 30.0


def test_alpha_no_material_reads_is_dropped_and_read_alpha_is_kept():
    v, rgba = vtf_and_rgba(smooth_image(128, 128, alpha=True), fmt=15)
    unused = choose.choose(v, rgba, {"w": None, "roles": {"base"}}, 34.0)
    used = choose.choose(v, rgba, {"w": 1.0, "roles": {"base"}}, 34.0)
    assert not unused["alpha"] and used["alpha"] and used["alpha_db"] >= 30.0
    out = read_vtf(codec.lua_vtf(codec.encode(v, rgba, used["step"], alpha_step=used["alpha_step"],
                                              chroma_drop=used["drop"], keep_alpha=True)))
    assert out.fmt == 15
    assert read_vtf(codec.lua_vtf(codec.encode(v, rgba, unused["step"], keep_alpha=False))).fmt == 13


def test_normal_maps_are_coded_as_unit_vectors():
    y, x = np.mgrid[0:128, 0:128] / 128.0
    nx, ny = 0.4 * np.sin(8 * x), 0.4 * np.cos(6 * y)
    nz = np.sqrt(1 - nx ** 2 - ny ** 2)
    img = np.stack([(nx + 1) * 127.5, (ny + 1) * 127.5, (nz + 1) * 127.5, np.full_like(nx, 255)], -1)
    v, rgba = vtf_and_rgba(img)
    c = choose.choose(v, rgba, {"w": None, "roles": {"normal"}}, 34.0)
    assert c["normal"]
    planes, mode, *_ = codec.decode_planes(codec.encode(v, rgba, c["step"], normal=True))
    assert mode == 1 and len(planes) == 2


def test_alpha_weights_follow_the_materials():
    vmts = [("a", "vertexlitgeneric", {"$basetexture": "t/see", "$translucent": "1"}),
            ("b", "vertexlitgeneric", {"$basetexture": "t/plain"}),
            ("c", "vertexlitgeneric", {"$basetexture": "t/env", "$basealphaenvmapmask": "1", "$envmap": "env_cubemap",
                                       "$envmaptint": "[.3 .3 .3]"}),
            ("d", "vertexlitgeneric", {"$basetexture": "t/plain2", "$bumpmap": "t/nm", "$phong": "1"})]
    u = choose.alpha_uses(vmts, [])
    assert u["t/see"]["w"] == 1.0 and u["t/plain"]["w"] is None
    assert abs(u["t/env"]["w"] - 0.3) < 1e-9 and u["t/nm"]["w"] == 0.7 and u["t/nm"]["roles"] == {"normal"}


# ------------------------------------------------------------------ addon conversion
def _addon(tmp_path: Path) -> Path:
    """body: drawn by a model; hud: named in Lua; effects/flash: a sprite; unused: nothing names it."""
    root = tmp_path / "car_addon"
    mat = root / "materials" / "models" / "car"
    mat.mkdir(parents=True)
    (mat / "body.vtf").write_bytes(make_dxt_vtf(smooth_image(256, 256)))
    (mat / "hud.vtf").write_bytes(make_dxt_vtf(smooth_image(128, 128, seed=1)))
    (mat / "unused.vtf").write_bytes(make_dxt_vtf(smooth_image(128, 128, seed=2)))
    (mat / "body.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/car/body" }')
    fx = root / "materials" / "effects"
    fx.mkdir(parents=True)
    (fx / "flash.vtf").write_bytes(make_dxt_vtf(smooth_image(128, 128, seed=3)))
    (fx / "flash.vmt").write_text('"Sprite" { "$basetexture" "effects/flash" }')
    (mat / "small.vtf").write_bytes(make_dxt_vtf(smooth_image(32, 32)))          # too small: stays a VTF
    (root / "models").mkdir()
    (root / "models" / "car.mdl").write_bytes(b"IDST")
    (root / "lua" / "autorun").mkdir(parents=True)
    (root / "lua" / "autorun" / "car_hud.lua").write_text('local HUD = Material("models/car/hud")')
    return root


def _convert(root, monkeypatch):
    monkeypatch.setattr(texture_stream, "_model_textures",
                        lambda mdl, resolver: {"models/car/body"} if mdl.name == "car.mdl" else set())
    return texture_stream.convert(root, jobs=1, siblings=[])


def test_convert_streams_every_dxt_texture_with_how_the_game_will_need_it(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    small_before = (root / "materials/models/car/small.vtf").read_bytes()
    s = _convert(root, monkeypatch)
    assert s["streamed"] == 4 and s["kept"] == {"size": 1}
    slug = texture_stream.slug_for("car_addon")
    blobs = sorted((root / "data_static" / "gmot" / slug).glob("*.txt"))
    assert len(blobs) == 4 and all(b.read_bytes()[:4] == b"GMOT" for b in blobs)
    assert s["blobs"] == sum(b.stat().st_size for b in blobs)
    ph = read_vtf(root / "materials/models/car/body.vtf")
    assert (ph.width, ph.height, ph.fmt) == (16, 16, 13) and ph.mip_count == 5
    assert (root / "materials/models/car/small.vtf").read_bytes() == small_before
    lua = texture_stream.runtime_path(root, slug).read_text(encoding="utf-8")
    assert "if SERVER then AddCSLuaFile() return end" in lua and "GMOT.Register(MANIFEST)" in lua
    payload = lua.split("util.JSONToTable([==[", 1)[1].split("]==])", 1)[0]
    man = json.loads(payload)
    by_ref = {t[1]: t for t in man["tex"]}
    i = man["tex"].index(by_ref["models/car/body"]) + 1
    assert man["slug"] == slug and man["models"] == {"models/car.mdl": [i]}
    assert by_ref["models/car/body"][0] == f"data_static/gmot/{slug}/{i}.txt"
    assert by_ref["models/car/body"][3:] == [["models/car/body", "$basetexture"], texture_stream.CLASS_MODEL]
    assert by_ref["effects/flash"][4] == texture_stream.CLASS_EFFECT
    assert by_ref["models/car/hud"][4] == texture_stream.CLASS_LUA
    assert by_ref["models/car/unused"][4] == texture_stream.CLASS_OTHER
    assert not list((root / "data_static").rglob("*.tmp"))
    # a second run leaves an already streamed addon alone
    again = texture_stream.convert(root, jobs=1, siblings=[])
    assert again.get("skipped") == "already streamed" and all(b.exists() for b in blobs)


def test_texscreen_has_a_stream_profile_built_on_forte():
    from texlab import screen_compress as sc
    assert sc.PROFILES["stream"] == sc.PROFILES["strong"]
    assert sc.PROFILE_OPTIONS["stream"] is sc.PROFILE_OPTIONS["strong"]


# ------------------------------------------------------------------ Lua (LuaJIT through lupa)
lupa = pytest.importorskip("lupa")


def _lua():
    from lupa import luajit21
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    rt.globals().SysTime = time.perf_counter
    rt.execute(b"util = {}; timer = { Simple = function(t, f) TIMERS = TIMERS or {}; TIMERS[#TIMERS + 1] = f end }")
    rt.execute(b"render = { SetMaterial = function() end, MaterialOverride = function() end };"
               b"surface = { SetMaterial = function() end, GetTextureID = function() return 1 end };"
               b"RealFrameTime = function() return 0.02 end;"
               b"math.Clamp = function(v, lo, hi) return math.min(math.max(v, lo), hi) end")
    rt.globals().util.Decompress = lambda s: codec.unlzma(bytes(s))
    return rt


def test_lua_decoder_matches_the_python_mirror_byte_for_byte():
    rt = _lua()
    rt.execute((ROOT / "texture_stream" / "decoder.lua").read_bytes())
    v, rgba = vtf_and_rgba(smooth_image(1024, 512, alpha=True), fmt=15)
    blob = codec.encode(v, rgba, 6, alpha_step=4, chroma_drop=1, keep_alpha=True, fill_levels=2)
    for preview in (False, True):
        out = rt.globals().GMOT.Decode(blob, preview)
        lua_bytes = bytes(out[0] if isinstance(out, tuple) else out)
        assert lua_bytes == codec.lua_vtf(blob, preview=preview)


GMOD_STUBS = rb"""
SERVER, CLIENT = false, true
LOG, WRITTEN, MOUNTED, HOOKS, CMDS = {}, {}, {}, {}, {}
IMAT = {}; IMAT.__index = IMAT
ENTM = {}; ENTM.__index = ENTM
MATS = {}
function IMAT:GetName() return self.name end
function IMAT:IsError() return false end
function IMAT:GetTexture(p) local t = self.tex[p]; if not t then return nil end return { GetName = function() return t end } end
function IMAT:SetTexture(p, t) self.tex[p] = t; LOG[#LOG + 1] = self.name .. " " .. p .. " " .. tostring(t) end
function ENTM:GetModel() return self.model end
function ENTM:GetMaterial() return "" end
function ENTM:GetMaterials() return {} end
function ENTM:GetSubMaterial() return "" end
function ENTM:SetMaterial() end
function ENTM:SetSubMaterial() end
function ENTM:SetModel(m) self.model = m end
function FindMetaTable(n) if n == "IMaterial" then return IMAT end return ENTM end
function Material(name)
    local m = MATS[name]
    if not m then m = setmetatable({ name = name, tex = {} }, IMAT); MATS[name] = m end
    return m
end
function CreateMaterial(name) return Material(name) end
function ClientsideModel() end
ents = { CreateClientProp = function() end, GetAll = function() return ENTS or {} end }
function CreateClientConVar() return { GetFloat = function() return 50 end, GetBool = function() return true end } end
function IsValid(e) return e ~= nil and e ~= false and e.valid == true end
function isstring(s) return type(s) == "string" end
function LocalPlayer() return nil end
SortedPairs = pairs
hook = { Add = function(ev, name, f) HOOKS[ev .. "/" .. name] = f end }
concommand = { Add = function(n, f) CMDS[n] = f end }
game = { MountGMA = function(p) MOUNTED[#MOUNTED + 1] = p; return true, {} end, GetMap = function() return "gm_test" end,
         GetWorld = function() return nil end }
file = {
    Exists = function(p) return false end,
    Read = function(p) return FILES[p] end,
    Find = function() return {}, {} end,
    Delete = function() end, CreateDir = function() end,
    Open = function(p) local buf = {}; return { Write = function(_, s) buf[#buf + 1] = s end,
                                                Close = function() WRITTEN[p] = table.concat(buf) end } end,
    Rename = function(a, b) WRITTEN[b] = WRITTEN[a]; WRITTEN[a] = nil end,
}
"""


def _gma_files(data: bytes) -> dict:
    """name -> bytes of a GMAD v3 file as the runtime writes it."""
    assert data[:5] == b"GMAD\x03"
    pos = 5 + 8 + 8
    for _ in range(4):                                  # required content, name, description, author
        pos = data.index(b"\0", pos) + 1
    pos += 4
    entries = []
    while True:
        (k,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if k == 0:
            break
        end = data.index(b"\0", pos)
        name = data[pos:end].decode()
        size, _crc = struct.unpack_from("<qI", data, end + 1)
        pos = end + 1 + 12
        entries.append((name, size))
    out = {}
    for name, size in entries:
        out[name] = data[pos:pos + size]
        pos += size
    return out


def test_lua_runtime_decodes_mounts_and_points_the_material_at_the_stream(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    _convert(root, monkeypatch)
    slug = texture_stream.slug_for("car_addon")
    rt = _lua()
    g = rt.globals()
    rt.execute(GMOD_STUBS)
    files = rt.table()
    for p in (root / "data_static").rglob("*.txt"):
        files[p.relative_to(root).as_posix().encode()] = p.read_bytes()
    g.FILES = files
    g.util.JSONToTable = lambda s: _to_lua(rt, json.loads(bytes(s).decode()))
    Material = g.Material
    Material(b"models/car/body").tex[b"$basetexture"] = b"models/car/body"     # as the VMT loads it
    rt.execute(texture_stream.runtime_path(root, slug).read_bytes())
    assert g.GMOT.CORE == texture_stream.CORE_VERSION
    ids = {bytes(e.ref).decode(): e.i for e in g.GMOT.Entries.values()}
    body = f"gmot/{slug}/{ids['models/car/body']}".encode()
    car = rt.eval(b"setmetatable({ model = 'Models/Car.mdl', valid = true }, ENTM)")
    g.ENTS = rt.table(car)
    think = g.HOOKS[b"Think/gmot_stream"]
    for _ in range(200):
        think()
        if g.MATS[b"models/car/body"].tex[b"$basetexture"] == body:
            break
    assert g.MATS[b"models/car/body"].tex[b"$basetexture"] == body
    assert len(g.MOUNTED) == 1
    gma = _gma_files(bytes(g.WRITTEN[g.MOUNTED[1][5:]]))
    assert list(gma) == [f"materials/{body.decode()}.vtf"]
    blob = (root / "data_static" / "gmot" / slug / f"{ids['models/car/body']}.txt").read_bytes()
    assert gma[f"materials/{body.decode()}.vtf"] == codec.lua_vtf(blob)
    # Lua that names the texture later gets the decoded one directly
    copy = Material(b"hud_copy")
    copy.SetTexture(copy, b"$basetexture", b"models/car/body")
    assert copy.tex[b"$basetexture"] == body
    # a material drawn every frame by Lua (no VMT: named like its texture) is seen and pointed at the stream
    hud = Material(b"models/car/hud")
    hud.tex[b"$basetexture"] = b"models/car/hud"
    g.render.SetMaterial(hud)
    for _ in range(200):
        think()
    assert hud.tex[b"$basetexture"] == f"gmot/{slug}/{ids['models/car/hud']}".encode()
    # the sprite no Lua call shows is prefetched after the map loads (InitPostEntity -> timer)
    flash = Material(b"effects/flash")
    flash.tex[b"$basetexture"] = b"effects/flash"
    g.HOOKS[b"InitPostEntity/gmot_stream"]()
    g.TIMERS[1]()
    for _ in range(200):
        think()
    assert flash.tex[b"$basetexture"] == f"gmot/{slug}/{ids['effects/flash']}".encode()
    assert {bytes(e.ref).decode(): e.state for e in g.GMOT.Entries.values()}["models/car/unused"] is None


def _to_lua(rt, obj):
    if isinstance(obj, dict):
        return rt.table_from({k.encode(): _to_lua(rt, v) for k, v in obj.items()})
    if isinstance(obj, list):
        return rt.table_from([_to_lua(rt, v) for v in obj])
    if isinstance(obj, str):
        return obj.encode()
    return obj


# ------------------------------------------------------------------ uncompressed and DXT3 sources
def make_raw_vtf(img, fmt) -> bytes:
    """uncompressed VTF (codec.RAW_ORDER formats) of `img` with a box-filtered mip chain."""
    h, w = img.shape[:2]
    mips = max(w, h).bit_length()
    template = read_vtf(make_dxt_vtf(img[:4, :4] * 0 + 128))
    levels, cur, cw, ch = [], img.astype(np.float64), w, h
    for m in range(mips):
        px = np.concatenate([np.floor(np.clip(cur, 0, 255) + 0.5), np.full((ch, cw, 1), 255.0)], -1).astype(np.uint8)
        levels.append(np.ascontiguousarray(px[..., list(codec.RAW_ORDER[fmt])]).tobytes())
        if m < mips - 1:
            nxt = [codec.down2(cur[..., c], cw, ch) for c in range(4)]
            cur, cw, ch = np.stack([n[0] for n in nxt], -1), nxt[0][1], nxt[0][2]
    return write_vtf(template, fmt, w, h, levels, flags=0x2000 if len(codec.RAW_ORDER[fmt]) == 4 else 0)


def make_dxt3_vtf(img) -> bytes:
    h, w = img.shape[:2]
    template = read_vtf(make_dxt_vtf(img[:4, :4]))
    px = codec.blocks(img, w, h)
    a4 = np.clip(np.floor(px[..., 3] / 17 + 0.5), 0, 15).astype(np.uint8)
    alpha = (a4[:, 0::2] | (a4[:, 1::2] << 4)).astype(np.uint8)
    top = np.concatenate([alpha, codec.lua_dxt1(px[..., :3])], 1).tobytes()
    return write_vtf(template, 14, w, h, [top], flags=0x2300)


@pytest.mark.parametrize("fmt", [12, 2])
def test_uncompressed_textures_come_back_in_their_own_format(fmt):
    img = smooth_image(256, 128, alpha=(fmt == 12))
    v = read_vtf(make_raw_vtf(img, fmt))
    rgba = decode_level(v, 0).astype(np.float64)
    c = choose.choose(v, rgba, None, 34.0)
    assert c["rgb_db"] >= choose.RAW_TARGET and c["alpha"] == (fmt == 12)
    blob = codec.encode(v, rgba, c["step"], alpha_step=c["alpha_step"], chroma_drop=c["drop"], keep_alpha=c["alpha"])
    out = read_vtf(codec.lua_vtf(blob))
    assert (out.fmt, out.width, out.height, out.mip_count) == (fmt, 256, 128, v.mip_count)
    assert codec.psnr(decode_level(out, 0)[..., :3], rgba[..., :3]) >= 38.0
    assert len(blob) < len(make_raw_vtf(img, fmt)) / 4


def test_dxt3_without_mips_becomes_dxt5():
    img = smooth_image(256, 256, alpha=True)
    v = read_vtf(make_dxt3_vtf(img))
    assert choose.eligible(v) is None and v.mip_count == 1
    rgba = decode_level(v, 0).astype(np.float64)
    c = choose.choose(v, rgba, {"w": 1.0, "roles": {"base"}}, 34.0)
    out = read_vtf(codec.lua_vtf(codec.encode(v, rgba, c["step"], alpha_step=c["alpha_step"], keep_alpha=c["alpha"])))
    assert (out.fmt, out.mip_count) == (15, 1)


@pytest.mark.parametrize("fmt", [12, 1, 3, 16])
def test_lua_raw_output_matches_the_python_mirror(fmt):
    rt = _lua()
    rt.execute((ROOT / "texture_stream" / "decoder.lua").read_bytes())
    img = smooth_image(128, 64, alpha=True)
    v = read_vtf(make_raw_vtf(img, fmt))
    rgba = decode_level(v, 0).astype(np.float64)
    keep = len(codec.RAW_ORDER[fmt]) == 4 and fmt != 16
    blob = codec.encode(v, rgba, 3, alpha_step=2, keep_alpha=keep, fill_levels=2)
    out = rt.globals().GMOT.Decode(blob, False)
    assert bytes(out[0] if isinstance(out, tuple) else out) == codec.lua_vtf(blob)
