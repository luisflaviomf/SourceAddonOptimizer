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


def assert_same_vtf(lua_bytes: bytes, py_bytes: bytes):
    """Lua output == the Python mirror, byte for byte (same arithmetic in the same order)."""
    assert len(lua_bytes) == len(py_bytes)
    diff = sum(x != y for x, y in zip(lua_bytes, py_bytes))
    assert diff == 0, f"{diff} bytes differ"


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
    (mat / "truck.vtf").write_bytes(make_dxt_vtf(smooth_image(1024, 1024, seed=4)))   # big: preview first
    (mat / "truck.vmt").write_text('"VertexLitGeneric" { "$basetexture" "models/car/truck" }')
    (root / "models").mkdir()
    (root / "models" / "car.mdl").write_bytes(b"IDST")
    (root / "models" / "truck.mdl").write_bytes(b"IDST")
    (root / "lua" / "autorun").mkdir(parents=True)
    (root / "lua" / "autorun" / "car_hud.lua").write_text('local HUD = Material("models/car/hud")')
    return root


def _convert(root, monkeypatch):
    uses = {"car.mdl": {"models/car/body"}, "truck.mdl": {"models/car/truck"}}
    monkeypatch.setattr(texture_stream, "_model_textures", lambda mdl, resolver: uses.get(mdl.name, set()))
    return texture_stream.convert(root, jobs=1, siblings=[])


def test_stream_files_take_a_type_the_addon_packer_keeps(tmp_path, monkeypatch):
    """gmad leaves out what addon.json "ignore" matches ("*.txt" for read-me files took every blob of an
    ARC9 addon out of its .gma): the blobs take a data_static type that the addon's own list keeps."""
    from texlab import packing
    assert packing.ignored("data_static/gmot/ab/1.txt", ["*.psd", "*.TXT"]) and packing.ignored("readme.txt", ["*.txt"])
    assert not packing.ignored("data_static/gmot/ab/1.dat", ["*.txt", ".*", "*.json", "work?older*", "arccw.jpg"])
    assert packing.ignored("workfolder/a.dat", ["work?older*"]) and not packing.ignored("a/workfolder/a.dat", ["work?older*"])
    root = _addon(tmp_path)
    (root / "addon.json").write_text(json.dumps({"title": "car", "ignore": ["*.dat", "*.txt", "*.psd"]}))
    s = _convert(root, monkeypatch)
    blobs = list((root / "data_static" / "gmot" / s["slug"]).iterdir())
    assert s["blob_ext"] == ".json" and s["packed"] and len(blobs) == 5 and all(p.suffix == ".json" for p in blobs)
    lua = texture_stream.runtime_path(root, s["slug"]).read_text(encoding="utf-8")
    man = json.loads(lua.split("util.JSONToTable([==[", 1)[1].split("]==])", 1)[0])
    assert all(t[0].endswith(".json") and (root / t[0]).is_file() for t in man["tex"])
    # nothing fits: the usual type, and the caller warns
    other = tmp_path / "other"
    other.mkdir()
    (other / "addon.json").write_text('﻿{"ignore": ["data_static/*", 7]}', encoding="utf-8")
    assert packing.stream_extension(str(other), "data_static/gmot") == (".dat", False)
    assert packing.stream_extension(str(tmp_path / "missing"), "data_static/gmo") == (".dat", True)
    (other / "addon.json").write_text("not json")
    assert packing.ignore_patterns(other) == ()


def test_convert_streams_every_dxt_texture_with_how_the_game_will_need_it(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    small_before = (root / "materials/models/car/small.vtf").read_bytes()
    s = _convert(root, monkeypatch)
    assert s["streamed"] == 5 and s["kept"] == {"size": 1}
    slug = s["slug"]
    assert slug.startswith(texture_stream.slug_for("car_addon")[:5]) and len(slug) == 12
    blobs = sorted((root / "data_static" / "gmot" / slug).glob("*.dat"))
    assert len(blobs) == 5 and all(b.read_bytes()[:4] == b"GMOT" for b in blobs)
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
    j = man["tex"].index(by_ref["models/car/truck"]) + 1
    assert man["slug"] == slug and man["models"] == {"models/car.mdl": [i], "models/truck.mdl": [j]}
    assert by_ref["models/car/body"][0] == f"data_static/gmot/{slug}/{i}.dat"
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
    assert sc.PROFILES["stream"] == sc.PROFILES["stream_max"] == sc.PROFILES["strong"]
    assert sc.PROFILE_OPTIONS["stream"] is sc.PROFILE_OPTIONS["stream_max"] is sc.PROFILE_OPTIONS["strong"]
    assert sc.STREAM_TARGETS == {"stream": 34.0, "stream_max": 31.0}


# ------------------------------------------------------------------ Lua (LuaJIT through lupa)
lupa = pytest.importorskip("lupa")


def _lua():
    from lupa import luajit21
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    rt.globals().SysTime = time.perf_counter
    rt.execute(b"util = {}; TIMERS = {}; timer = { Simple = function(t, f) TIMERS[#TIMERS + 1] = f end }")
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
        assert_same_vtf(lua_bytes, codec.lua_vtf(blob, preview=preview))


GMOD_STUBS = rb"""
SERVER, CLIENT = false, true
LOG, WRITTEN, MOUNTED, HOOKS, CMDS = {}, {}, {}, {}, {}
IMAT = {}; IMAT.__index = IMAT
ENTM = {}; ENTM.__index = ENTM
MATS = {}
function IMAT:GetName() return self.name end
function IMAT:IsError() return false end
ITEXM = {}; ITEXM.__index = ITEXM
function ITEXM:GetName() return self.name end
function IMAT:GetTexture(p) local t = self.tex[p]; if not t then return nil end return setmetatable({ name = t }, ITEXM) end
function IMAT:SetTexture(p, t)
    if type(t) == "table" then t = t.name end
    self.tex[p] = t; LOG[#LOG + 1] = self.name .. " " .. p .. " " .. tostring(t)
end
PTM = {}; PTM.__index = PTM
function PTM:SetTexture(t) if type(t) == "table" then t = t.name end self.tex = t end
function PTM:Update() self.updates = (self.updates or 0) + 1 end
EMITM = {}; EMITM.__index = EMITM
function EMITM:Add() end
META = { IMaterial = IMAT, ProjectedTexture = PTM, CLuaEmitter = EMITM, ITexture = ITEXM }
SKYNAME = ""
function GetConVar() return { GetString = function() return SKYNAME end } end
VEC = {}; VEC.__index = VEC
function VEC:Distance(o) local dx, dy, dz = self.x - o.x, self.y - o.y, self.z - o.z return math.sqrt(dx * dx + dy * dy + dz * dz) end
function Vector(x, y, z) return setmetatable({ x = x or 0, y = y or 0, z = z or 0 }, VEC) end
function EyePos() return Vector(0, 0, 0) end
function ScrH() return 1080 end
function ENTM:BoundingRadius() return self.radius or 100 end
function ENTM:WorldSpaceCenter() return self.pos or Vector(100, 0, 0) end
function ENTM:GetModel() return self.model end
function ENTM:GetMaterial() return "" end
function ENTM:GetMaterials() return self.mats or {} end
function ENTM:GetSubMaterial() return "" end
function ENTM:SetMaterial() end
function ENTM:SetSubMaterial() end
function ENTM:SetModel(m) self.model = m end
function FindMetaTable(n) return META[n] or ENTM end
function Material(name)
    local m = MATS[name]
    if not m then m = setmetatable({ name = name, tex = {} }, IMAT); MATS[name] = m end
    return m
end
function CreateMaterial(name) return Material(name) end
function ClientsideModel() end
ents = { CreateClientProp = function() end, GetAll = function() return ENTS or {} end }
CONVARS = {}
function CreateClientConVar(name, def)
    return { GetFloat = function() return 50 end, GetBool = function() return true end,
             GetInt = function() return CONVARS[name] or tonumber(def) or 0 end }
end
function IsValid(e) return e ~= nil and e ~= false and e.valid == true end
function isstring(s) return type(s) == "string" end
function LocalPlayer() return nil end
SortedPairs = pairs
function EVAL(src) return loadstring("return " .. src)() end    -- a Lua value for the tests
hook = { Add = function(ev, name, f) HOOKS[ev .. "/" .. name] = f end }
util.TableToJSON = function() return "{}" end
concommand = { Add = function(n, f) CMDS[n] = f end }
GAMEFILES = {}
game = { MountGMA = function(p)
            MOUNTED[#MOUNTED + 1] = p
            local names = GMA_NAMES and GMA_NAMES(WRITTEN[string.sub(p, 6)]) or {}
            for _, n in ipairs(names) do GAMEFILES[n] = true end
            return true, names
         end,
         AddDecal = function() end,
         GetMap = function() return "gm_test" end,
         GetWorld = function() return nil end }
file = {
    Exists = function(p, where) if where == "DATA" then return WRITTEN[p] ~= nil end return GAMEFILES[p] ~= nil end,
    Read = function(p, where) if where == "DATA" then return WRITTEN[p] end return FILES[p] end,
    Find = function() return {}, {} end,
    Delete = function() end, CreateDir = function() end,
    Open = function(p, mode)
        if mode == "rb" then            -- a game file (the map)
            local data, pos = FILES[p], 0
            if not data then return nil end
            local r = {}
            function r:Read(n) local s = data:sub(pos + 1, pos + n); pos = pos + n; return s end
            function r:ReadLong()
                local a, b, c, d = data:byte(pos + 1, pos + 4); pos = pos + 4
                if not d then return 0 end
                local v = a + b * 256 + c * 65536 + d * 16777216
                return v >= 2147483648 and v - 4294967296 or v
            end
            function r:ReadUShort() local a, b = data:byte(pos + 1, pos + 2); pos = pos + 2; return (a or 0) + (b or 0) * 256 end
            function r:Seek(n) pos = n end
            function r:Tell() return pos end
            function r:Close() end
            return r
        end
        local buf = {}
        return { Write = function(_, s) buf[#buf + 1] = s end, Close = function() WRITTEN[p] = table.concat(buf) end }
    end,
    Rename = function(a, b) WRITTEN[b] = WRITTEN[a]; WRITTEN[a] = nil end,
    Write = function(p, s) WRITTEN[p] = s end,
}
"""


# reading a global nobody defined is a bug in the runtime (typically a local function used above its
# definition, which Lua silently turns into a global lookup)
STRICT_GLOBALS = rb"""
local OPTIONAL = { GMOT = true, GMA_NAMES = true }   -- GMOT: read before the first addon creates it; GMA_NAMES: test hook
setmetatable(_G, { __index = function(_, k)
    if OPTIONAL[k] then return nil end
    error("undefined global " .. tostring(k), 2)
end })
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
    slug = _convert(root, monkeypatch)["slug"]
    rt = _lua()
    g = rt.globals()
    rt.execute(GMOD_STUBS)
    files = rt.table()
    for p in (root / "data_static").rglob("*.dat"):
        files[p.relative_to(root).as_posix().encode()] = p.read_bytes()
    g.FILES = files
    g.util.JSONToTable = lambda s: _to_lua(rt, json.loads(bytes(s).decode()))
    Material = g.Material
    rt.execute(STRICT_GLOBALS)
    Material(b"models/car/body").tex[b"$basetexture"] = b"models/car/body"     # as the VMTs load them
    Material(b"models/car/truck").tex[b"$basetexture"] = b"models/car/truck"
    rt.execute(texture_stream.runtime_path(root, slug).read_bytes())
    assert g.GMOT.CORE == texture_stream.CORE_VERSION
    ids = {bytes(e.ref).decode(): e.i for e in g.GMOT.Entries.values()}
    body = f"gmot/{slug}/{ids['models/car/body']}".encode()
    car = rt.eval(b"setmetatable({ model = 'Models/Car.mdl', valid = true }, ENTM)")
    # a truck far away (~30 px on screen): its 1024 texture is decoded only up to the 512 preview
    truck = rt.eval(b"setmetatable({ model = 'models/truck.mdl', valid = true, pos = Vector(5000, 0, 0) }, ENTM)")
    g.ENTS = rt.table(car, truck)
    think = g.HOOKS[b"Think/gmot_stream"]
    for _ in range(200):
        think()
        if g.MATS[b"models/car/body"].tex[b"$basetexture"] == body:
            break
    assert g.MATS[b"models/car/body"].tex[b"$basetexture"] == body
    truck_tex = f"gmot/{slug}/{ids['models/car/truck']}".encode()
    for _ in range(200):
        think()
    entries = {bytes(e.ref).decode(): e for e in g.GMOT.Entries.values()}
    assert entries["models/car/truck"].state == 2
    assert g.MATS[b"models/car/truck"].tex[b"$basetexture"] == truck_tex + b"_p"
    gma = {}
    for path in g.MOUNTED.values():
        gma.update(_gma_files(bytes(g.WRITTEN[path[5:]])))
    assert set(gma) == {f"materials/{body.decode()}.vtf", f"materials/{truck_tex.decode()}_p.vtf"}
    blob = (root / "data_static" / "gmot" / slug / f"{ids['models/car/body']}.dat").read_bytes()
    assert_same_vtf(gma[f"materials/{body.decode()}.vtf"], codec.lua_vtf(blob))
    # the truck comes close: the full texture follows
    truck.pos = rt.eval(b"Vector(300, 0, 0)")
    for _ in range(400):
        think()
        if entries["models/car/truck"].state == 3:
            break
    assert g.MATS[b"models/car/truck"].tex[b"$basetexture"] == truck_tex
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


def test_one_level_texture_is_built_with_the_flags_the_game_wants():
    """A texture stored as one level without NOMIP / NOLOD (particle textures of the 2026-11-10 server test):
    the game sets both when it loads the file and prints a console line each time."""
    raw = bytearray(make_dxt3_vtf(smooth_image(128, 128, alpha=True)))
    struct.pack_into("<I", raw, 20, 0x2000)
    v = read_vtf(bytes(raw))
    assert v.mip_count == 1 and not v.flags & 0x300
    rgba = decode_level(v, 0).astype(np.float64)
    out = read_vtf(codec.lua_vtf(codec.encode(v, rgba, 6, alpha_step=4, keep_alpha=True)))
    assert out.mip_count == 1 and out.flags & 0x300 == 0x300


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
    assert_same_vtf(bytes(out[0] if isinstance(out, tuple) else out), codec.lua_vtf(blob))


def test_lua_full_decode_from_cached_preview_planes_is_identical():
    rt = _lua()
    rt.execute((ROOT / "texture_stream" / "decoder.lua").read_bytes())
    v, rgba = vtf_and_rgba(smooth_image(1024, 1024, alpha=True), fmt=15)
    blob = codec.encode(v, rgba, 6, alpha_step=4, keep_alpha=True, fill_levels=2)
    cache = rt.table()
    decode = rt.globals().GMOT.Decode
    prev = decode(blob, True, cache)
    assert_same_vtf(bytes(prev[0] if isinstance(prev, tuple) else prev), codec.lua_vtf(blob, preview=True))
    assert cache.planes is not None
    full = decode(blob, False, cache)
    assert_same_vtf(bytes(full[0] if isinstance(full, tuple) else full), codec.lua_vtf(blob))


def _from_lua(obj):
    from lupa import luajit21
    if luajit21.lua_type(obj) == "table":
        keys = list(obj.keys())
        if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [_from_lua(obj[k]) for k in sorted(keys)]
        return {(k.decode() if isinstance(k, bytes) else k): _from_lua(v) for k, v in obj.items()}
    return obj.decode() if isinstance(obj, bytes) else obj


def _session(root, slug, written=None, before=None, game_files=None, vmts=None):
    """one game session of the runtime in LuaJIT with the stubs; `written`: data/ files of an earlier session;
    `before`: Lua run before the runtime loads; `game_files`: more files of the game (the map); `vmts`:
    material -> texture, as the game loads their VMTs."""
    rt = _lua()
    g = rt.globals()
    rt.execute(GMOD_STUBS)
    files = rt.table()
    for p in (root / "data_static").rglob("*.dat"):
        files[p.relative_to(root).as_posix().encode()] = p.read_bytes()
    for k, v in (game_files or {}).items():
        files[k.encode()] = v
    g.FILES = files
    for k, v in (vmts or {}).items():
        g.Material(k.encode()).tex[b"$basetexture"] = v.encode()
    if before:
        rt.execute(before)
    g.util.JSONToTable = lambda s: _to_lua(rt, json.loads(bytes(s).decode()))
    g.util.TableToJSON = lambda t: json.dumps(_from_lua(t)).encode()
    for k, v in (written or {}).items():
        g.WRITTEN[k] = v

    g.GMA_NAMES = lambda data: rt.table_from([n.encode() for n in _gma_files(bytes(data))])
    g.Material(b"models/car/body").tex[b"$basetexture"] = b"models/car/body"
    rt.execute(STRICT_GLOBALS)
    rt.execute(texture_stream.runtime_path(root, slug).read_bytes())
    car = rt.eval(b"setmetatable({ model = 'models/car.mdl', valid = true }, ENTM)")
    g.ENTS = rt.table(car)
    think = g.HOOKS[b"Think/gmot_stream"]
    ids = {bytes(e.ref).decode(): e.i for e in g.GMOT.Entries.values()}
    body = f"gmot/{slug}/{ids['models/car/body']}".encode()
    for _ in range(200):
        think()
        if g.MATS[b"models/car/body"].tex[b"$basetexture"] == body:
            break
    return g, body


def test_lua_runtime_keeps_decoded_textures_for_the_next_session(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    slug = _convert(root, monkeypatch)["slug"]
    g1, body = _session(root, slug)
    assert g1.MATS[b"models/car/body"].tex[b"$basetexture"] == body and g1.GMOT.Stats.decoded >= 1
    written = {bytes(k): bytes(v) for k, v in g1.WRITTEN.items()}
    index = json.loads(written[b"gmot/cache.txt"])
    assert len(index) == 1 and list(index.values())[0][2] == [slug]
    # next session: the GMA of the cache is mounted at start and the texture applied without decoding
    g2, _ = _session(root, slug, written)
    assert g2.MATS[b"models/car/body"].tex[b"$basetexture"] == body
    assert g2.GMOT.Stats.decoded == 0 and g2.GMOT.Stats.cache_mounted == 1 and g2.GMOT.Stats.reused >= 1


def test_lua_half_size_decode_and_the_preview_planes_chain():
    """2048 texture: preview (512), half (1024) and full (2048), each from the preview's planes, all equal
    to the Python mirror."""
    rt = _lua()
    rt.execute((ROOT / "texture_stream" / "decoder.lua").read_bytes())
    v, rgba = vtf_and_rgba(smooth_image(2048, 1024, alpha=True), fmt=15)
    blob = codec.encode(v, rgba, 8, alpha_step=4, keep_alpha=True, fill_levels=2)
    decode = rt.globals().GMOT.Decode
    cache = rt.table()

    def lua(*args):
        out = decode(blob, *args)
        return bytes(out[0] if isinstance(out, tuple) else out)
    assert_same_vtf(lua(True, cache), codec.lua_vtf(blob, preview=True))
    half = lua(1, cache)
    assert_same_vtf(half, codec.lua_vtf(blob, preview=1))
    assert (read_vtf(half).width, read_vtf(half).height) == (1024, 512)
    full_cached = lua(False, cache)
    assert_same_vtf(full_cached, codec.lua_vtf(blob))
    assert lua(1) == half and lua(False) == full_cached     # without the cache: the very same bytes


def _one_level_vtf(img, fmt=13) -> bytes:
    h, w = img.shape[:2]
    template = read_vtf(make_dxt_vtf(img[:4, :4]))
    top = codec.lua_dxt1(codec.blocks(img[..., :3], w, h)).tobytes()
    return write_vtf(template, fmt, w, h, [top], flags=0x300)


@pytest.mark.parametrize("case", ["npot", "normal", "bgra", "bgr", "nomip", "wide", "nofill", "tall"])
def test_lua_row_by_row_levels_match_the_mirror_on_every_path(case):
    """The levels after the preview are synthesized row by row and the last one goes straight to DXT/raw
    blocks and mips (never held whole): preview, half and full sizes equal the mirror for every output kind,
    band kind and size (a half size that is not a multiple of 4 falls back to whole planes)."""
    rt = _lua()
    rt.execute((ROOT / "texture_stream" / "decoder.lua").read_bytes())
    kw = dict(alpha_step=4, fill_levels=2)
    step = 6
    if case == "npot":
        v = read_vtf(_one_level_vtf(smooth_image(1028, 1032, seed=5)))
        rgba = decode_level(v, 0).astype(np.float64)
    elif case == "normal":
        img = smooth_image(1024, 1024, alpha=True, seed=6)
        img[..., :3] = codec.unit_normals(np.stack([img[..., 0] * 0.2 + 110, img[..., 1] * 0.2 + 110,
                                                    img[..., 2] * 0 + 255], -1))
        v, rgba = vtf_and_rgba(img, fmt=15)
        kw.update(normal=True, keep_alpha=True)
    elif case in ("bgra", "bgr"):
        fmt = 12 if case == "bgra" else 3
        v = read_vtf(make_raw_vtf(smooth_image(1024, 512, alpha=True, seed=7), fmt))
        rgba = decode_level(v, 0).astype(np.float64)
        kw.update(keep_alpha=(fmt == 12))
        step = 3
    elif case == "nomip":
        v = read_vtf(_one_level_vtf(smooth_image(1024, 512, seed=8)))
        rgba = decode_level(v, 0).astype(np.float64)
    elif case == "wide":
        img = smooth_image(1024, 1024, alpha=True, seed=9)   # hard checker: 16-bit bands on the fine levels
        y, x = np.mgrid[0:1024, 0:1024]
        img = np.clip(img + (((x + y // 3) % 2) * 160 - 80)[..., None], 0, 255)
        v, rgba = vtf_and_rgba(img, fmt=15)
        kw.update(keep_alpha=True, alpha_step=0.5)
        step = 0.5
    elif case == "nofill":
        v, rgba = vtf_and_rgba(smooth_image(2048, 1024, seed=10))
        kw.update(fill_levels=0)
    else:
        v, rgba = vtf_and_rgba(smooth_image(16, 2048, seed=11))
    blob = codec.encode(v, rgba, step, **kw)
    decode = rt.globals().GMOT.Decode
    for mode in (True, 1, False):
        out = decode(blob, mode)
        assert_same_vtf(bytes(out[0] if isinstance(out, tuple) else out), codec.lua_vtf(blob, preview=mode))


def _shown(g, material):
    return bytes(g.MATS[material.encode()].tex[b"$basetexture"]).decode()


def _stream_name(g, slug, ref, size=""):
    ids = {bytes(e.ref).decode(): e.i for e in g.GMOT.Entries.values()}
    return f"gmot/{slug}/{ids[ref]}{size}"


def _sprite(root, name, seed):
    fx = root / "materials" / "effects"
    (fx / f"{name}.vtf").write_bytes(make_dxt_vtf(smooth_image(128, 128, seed=seed)))
    (fx / f"{name}.vmt").write_text('"Sprite" { "$basetexture" "effects/%s" }' % name)


# a clock the test drives: every engine call that loads something takes 4 ms, every frame 20 ms
FAKE_CLOCK = rb"""
CLOCK, FRAME, ENGINE, SETS = 0, 1, {}, {}
SysTime = function() return CLOCK end
local mat, set = Material, IMAT.SetTexture
Material = function(name)
    CLOCK = CLOCK + 0.004
    ENGINE[FRAME] = (ENGINE[FRAME] or 0) + 1
    return mat(name)
end
IMAT.SetTexture = function(self, p, t)
    CLOCK = CLOCK + 0.004
    ENGINE[FRAME] = (ENGINE[FRAME] or 0) + 1
    SETS[#SETS + 1] = { FRAME, self.name }
    return set(self, p, t)
end
local add = hook.Add
hook.Add = function(ev, name, f)
    if ev == "Think" then
        local think = f
        f = function()
            CLOCK = CLOCK + 0.02
            FRAME = FRAME + 1
            return think()
        end
    end
    return add(ev, name, f)
end
"""


def test_lua_runtime_shows_cached_textures_a_few_per_frame_and_the_background_ones_spread_out(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    for k, name in enumerate(("smoke", "spark")):
        _sprite(root, name, 20 + k)
    slug = _convert(root, monkeypatch)["slug"]
    vmts = {m: m for m in ("models/car/truck", "effects/flash", "effects/smoke", "effects/spark")}
    # first session: everything decoded (models on screen, then the background prefetch)
    g1, body = _session(root, slug, vmts=vmts)
    g1.ENTS[2] = g1.EVAL(b"setmetatable({ model = 'models/truck.mdl', valid = true }, ENTM)")
    g1.HOOKS[b"InitPostEntity/gmot_stream"]()
    for timer in list(g1.TIMERS.values()):
        timer()
    think = g1.HOOKS[b"Think/gmot_stream"]
    for _ in range(4000):
        think()
        if all(e.state == 3 for e in g1.GMOT.Entries.values()):
            break
    assert all(e.state == 3 for e in g1.GMOT.Entries.values())
    written = {bytes(k): bytes(v) for k, v in g1.WRITTEN.items()}
    # next session: nothing is decoded, and no frame gets more than the engine calls its share allows
    g2, _ = _session(root, slug, written, before=FAKE_CLOCK, vmts=vmts)
    g2.ENTS[2] = g2.EVAL(b"setmetatable({ model = 'models/truck.mdl', valid = true }, ENTM)")
    g2.HOOKS[b"InitPostEntity/gmot_stream"]()
    for timer in list(g2.TIMERS.values()):
        timer()
    think = g2.HOOKS[b"Think/gmot_stream"]
    for _ in range(300):
        think()
    assert g2.GMOT.Stats.decoded == 0
    assert all(e.state == 3 for e in g2.GMOT.Entries.values())
    for m in vmts:
        assert _shown(g2, m) == _stream_name(g2, slug, m)
    assert max(g2.ENGINE.values()) <= 2                 # 6 ms of a 20 ms frame, 4 ms per call
    frames = {bytes(name).decode(): frame for frame, name in (tuple(s.values()) for s in g2.SETS.values())}
    fx = sorted(frames[m] for m in ("effects/flash", "effects/smoke", "effects/spark"))
    assert fx[1] - fx[0] >= 3 and fx[2] - fx[1] >= 3    # 8 ms of engine calls, then 72 ms of rest


def test_lua_runtime_follows_lamps_texture_objects_late_materials_and_materials_of_the_entity(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    slug = _convert(root, monkeypatch)["slug"]
    g, body = _session(root, slug, vmts={"models/car/truck": "models/car/truck"})
    think = g.HOOKS[b"Think/gmot_stream"]
    flash = _stream_name(g, slug, "effects/flash").encode()
    # a lamp (ProjectedTexture) lit with a stream texture: followed until the texture is decoded
    lamp = g.EVAL(b"setmetatable({ valid = true }, PTM)")
    g.PTM.SetTexture(lamp, b"effects/flash")
    assert lamp.tex == b"effects/flash"
    for _ in range(200):
        think()
    assert lamp.tex == flash and lamp.updates >= 1
    lamp2 = g.EVAL(b"setmetatable({ valid = true }, PTM)")
    g.PTM.SetTexture(lamp2, b"materials/effects/flash.vtf")
    assert lamp2.tex == flash                           # already decoded: at once
    g.PTM.SetTexture(lamp, b"effects/flashlight001")
    assert lamp.tex == b"effects/flashlight001"
    # the entity shows a material no manifest links to its model (a model of another addon): asked for too.
    # Far away: the 1024 texture as its preview
    car = g.ENTS[1]
    car.mats = g.EVAL(b"{ 'models/car/truck' }")
    car.pos = g.Vector(5000, 0, 0)
    car.model = b"models/car_b.mdl"
    for _ in range(200):
        think()
    truck = _stream_name(g, slug, "models/car/truck")
    assert _shown(g, "models/car/truck") == truck + "_p"
    # Lua copies the texture object of that material to its own material: followed like a name
    copy = g.Material(b"my_copy")
    copy.SetTexture(copy, b"$basetexture", g.MATS[b"models/car/truck"].GetTexture(g.MATS[b"models/car/truck"], b"$basetexture"))
    assert _shown(g, "my_copy") == truck + "_p"
    car.pos = g.Vector(100, 0, 0)
    for _ in range(400):
        think()
    assert _shown(g, "models/car/truck") == truck and _shown(g, "my_copy") == truck
    # a texture decoded in the background before any Lua named it as a material (no VMT: the engine makes one)
    g.HOOKS[b"InitPostEntity/gmot_stream"]()
    for timer in list(g.TIMERS.values()):
        timer()
    entries = {bytes(e.ref).decode(): e for e in g.GMOT.Entries.values()}
    for _ in range(2000):
        think()
        if entries["models/car/hud"].state == 3:
            break
    assert entries["models/car/hud"].state == 3
    g.GMOT.Orig.Material(b"models/car/hud").tex[b"$basetexture"] = b"models/car/hud"
    g.Material(b"models/car/hud")
    assert _shown(g, "models/car/hud") == _stream_name(g, slug, "models/car/hud")


def _bsp(entities: bytes, texture_names: bytes) -> bytes:
    lumps = {0: entities, 43: texture_names}
    head, body, pos = b"VBSP" + struct.pack("<i", 20), b"", 8 + 64 * 16 + 4
    for k in range(64):
        data = lumps.get(k, b"")
        head += struct.pack("<iiii", pos if data else 0, len(data), 0, 0)
        body += data
        pos += len(data)
    return head + struct.pack("<i", 1) + body


def test_lua_runtime_asks_for_the_sky_and_what_the_map_names_and_prefetches_up_to_its_budget(tmp_path, monkeypatch):
    root = _addon(tmp_path)
    maps = root / "materials" / "maps"
    sky = root / "materials" / "skybox"
    maps.mkdir()
    sky.mkdir()
    for k, (folder, name) in enumerate(((sky, "sky_testbk"), (maps, "rope"), (maps, "wall"))):
        (folder / f"{name}.vtf").write_bytes(make_dxt_vtf(smooth_image(128, 128, seed=30 + k)))
        (folder / f"{name}.vmt").write_text('"UnlitGeneric" { "$basetexture" "%s/%s" }' % (folder.name, name))
    slug = _convert(root, monkeypatch)["slug"]
    bsp = _bsp(b'{\n"classname" "move_rope"\n"RopeMaterial" "maps/rope.vmt"\n"origin" "0 0 0"\n}\n\0', b"MAPS/WALL\0TOOLS/NODRAW\0")
    names = ("skybox/sky_testbk", "maps/rope", "maps/wall")
    # background budget of 0 MB: only what the map start asks for is decoded
    g, _ = _session(root, slug, game_files={"maps/gm_test.bsp": bsp}, vmts={m: m for m in names},
                    before=b'SKYNAME = "sky_test"; CONVARS.gmot_prefetch_mb = 0')
    g.HOOKS[b"InitPostEntity/gmot_stream"]()
    for timer in list(g.TIMERS.values()):
        timer()
    think = g.HOOKS[b"Think/gmot_stream"]
    for _ in range(300):
        think()
    for m in names:
        assert _shown(g, m) == _stream_name(g, slug, m)
    entries = {bytes(e.ref).decode(): e for e in g.GMOT.Entries.values()}
    assert entries["effects/flash"].state is None and entries["models/car/unused"].state is None
    assert g.GMOT.Stats.prefetch_left >= 3
    # next session: what the map needs is in the cache and goes up when the map loads, before its first frame
    written = {bytes(k): bytes(v) for k, v in g.WRITTEN.items()}
    g2, _ = _session(root, slug, written, game_files={"maps/gm_test.bsp": bsp}, vmts={m: m for m in names},
                     before=b'SKYNAME = "sky_test"')
    g2.HOOKS[b"InitPostEntity/gmot_stream"]()
    for m in names:
        assert _shown(g2, m) == _stream_name(g2, slug, m)
    assert g2.GMOT.Stats.decoded == 0
