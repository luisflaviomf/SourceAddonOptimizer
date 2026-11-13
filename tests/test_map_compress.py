"""Map compressor (Mapas tab): lossless BSP repack in the engine's format, scan/build over any pakfile position,
.gma/.bin input with addon.json, and the texture rules for maps."""
import io
import json
import lzma
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import batch_build_map_bsp  # noqa: E402
import batch_scan_map_bsp  # noqa: E402
import batch_unpack_addons  # noqa: E402
import bsp_repack  # noqa: E402


def _pak(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_bsp(pak_files: dict | None = None, pak_last: bool = True, compressed_lump: bool = False,
             names: list[str] | None = None, entities: bytes | None = None) -> bytes:
    """A small VBSP v20: entities, planes (compressible), a game lump with one static prop entry, the
    texture name lumps and a pakfile (at the end of the file or before the planes).
    names: the world's material names (lumps 43/44); entities: the entity lump."""
    rng = np.random.default_rng(1)
    lumps = {
        0: b'{\n"classname" "worldspawn"\n"skyname" "sky_day01_01"\n}\n\x00',
        1: (np.arange(6000, dtype=np.float32) % 37).tobytes() + rng.integers(0, 4, 4000, dtype=np.uint8).tobytes(),
        43: b"TOOLS/TOOLSNODRAW\x00BRICK/WALL01\x00",
        44: struct.pack("<ii", 0, 18),
        8: bytes(rng.integers(0, 255, 3000, dtype=np.uint8)),        # lighting: noise, does not shrink
    }
    if names is not None:
        offsets, strings = [], b""
        for name in names:
            offsets.append(len(strings))
            strings += name.encode() + b"\x00"
        lumps[43], lumps[44] = strings, struct.pack(f"<{len(offsets)}i", *offsets)
    if entities is not None:
        lumps[0] = entities
    if pak_files is not None:
        lumps[40] = _pak(pak_files)
    order = [0, 1, 8, 43, 44, 35] + ([40] if pak_last else [])
    if not pak_last and 40 in lumps:
        order = [0, 40, 1, 8, 43, 44, 35]
    head = bytearray(b"VBSP" + struct.pack("<i", 20) + bytes(64 * 16) + struct.pack("<i", 7))
    body = bytearray()
    base = len(head)
    directory = {}
    for i in order:
        while (base + len(body)) % 4:
            body.append(0)
        start = base + len(body)
        if i == 35:
            # one game lump ("sprp") right after the directory; its offset is absolute in the file
            sprp = struct.pack("<i", 0) + struct.pack("<i", 0) + struct.pack("<i", 0)
            g = struct.pack("<i", 1) + struct.pack("<iHHii", int.from_bytes(b"sprp", "big"), 0, 10, start + 20, len(sprp))
            data = g + sprp
        else:
            data = lumps[i]
        fourcc = 0
        if compressed_lump and i == 1:
            fourcc = len(data)
            data = bsp_repack.valve_lzma(data)
        body += data
        directory[i] = (start, len(data), 0, fourcc)
    for i, (ofs, length, ver, fourcc) in directory.items():
        struct.pack_into("<iiii", head, 8 + i * 16, ofs, length, ver, fourcc)
    return bytes(head + body)


PAK = {
    "materials/brick/wall01.vtf": b"VTF\x00" + bytes(range(256)) * 40,
    "materials/brick/wall01.vmt": b'"LightmappedGeneric" { "$basetexture" "brick/wall01" }',
    "models/props/crate.mdl": b"IDST" + bytes(500),
    "models/props/crate.dx90.vtx": b"\x07" + bytes(300),
    "models/props/crate.dx80.vtx": b"\x07" + bytes(300),
    "sound/ambient/wind.mp3": bytes(np.random.default_rng(2).integers(0, 255, 2000, dtype=np.uint8)),
}


def test_repack_compresses_every_lump_and_pak_file_and_verify_proves_the_content():
    src = make_bsp(PAK)
    out = bsp_repack.repack(src)
    assert bsp_repack.verify(src, out) == []
    assert len(out) < len(src)
    L = bsp_repack.lumps(out)
    # planes compressed (fourCC = size uncompressed), the lumps Lua reads and the incompressible one untouched
    assert L[1][3] > 0 and out[L[1][0]:L[1][0] + 4] == b"LZMA"
    for i in (0, 43, 44, 8):
        assert L[i][3] == 0
    assert bsp_repack.lump_data(out, L, 0) == bsp_repack.lump_data(src, bsp_repack.lumps(src), 0)
    # game lump moved with its absolute offset
    (gofs,) = struct.unpack_from("<i", out, L[35][0] + 4 + 8)
    assert L[35][0] <= gofs < L[35][0] + L[35][1]


def test_pak_entries_look_like_valves():
    """GMod refused Python zipfile's own LZMA entries ("LZMA compression header is invalid"): the entries
    carry the version bytes 9.38 and general purpose flags 0 like Valve's tools write them."""
    out = bsp_repack.repack(make_bsp(PAK))
    L = bsp_repack.lumps(out)
    blob = out[L[40][0]:L[40][0] + L[40][1]]
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert z.testzip() is None and z.comment.startswith(b"XZP1")
    seen_lzma = 0
    for info in z.infolist():
        assert info.flag_bits == 0 and info.date_time[:3] == (1980, 0, 0)
        n, e = struct.unpack_from("<HH", blob, info.header_offset + 26)
        data = blob[info.header_offset + 30 + n + e:info.header_offset + 30 + n + e + 9]
        if info.compress_type == 14:
            seen_lzma += 1
            assert data[:4] == b"\x09\x26\x05\x00" and info.extract_version == 63
    assert seen_lzma >= 3
    # the noise file did not shrink: stored
    assert z.getinfo("sound/ambient/wind.mp3").compress_type == 0


def test_repack_keeps_already_compressed_lumps_and_reads_valves_lzma():
    src = make_bsp(PAK, compressed_lump=True)
    out = bsp_repack.repack(src)
    assert bsp_repack.verify(src, out) == []
    assert bsp_repack.lumps(out)[1][3] == bsp_repack.lumps(src)[1][3]


def test_game_lumps_compressed_by_valves_tools_and_data_outside_the_lump():
    """A game lump Valve's tools compressed has its uncompressed size in the directory, larger than the data
    (ttt_assault_csspk): the whole lump is compared with its offsets moved back. A game lump whose data lies
    outside lump 35 would not move with it: repack refuses the map."""
    src = bytearray(make_bsp(PAK))
    L = bsp_repack.lumps(bytes(src))
    entry = L[35][0] + 4                                     # the first (only) game lump's directory entry
    struct.pack_into("<H", src, entry + 4, 1)                # flags: compressed
    struct.pack_into("<i", src, entry + 12, 50000)           # length: "uncompressed size", past the lump
    out = bsp_repack.repack(bytes(src))
    assert bsp_repack.verify(bytes(src), out) == []
    struct.pack_into("<i", src, entry + 8, L[1][0])          # data offset inside the planes lump
    with pytest.raises(bsp_repack.BspError):
        bsp_repack.repack(bytes(src))


def test_a_wrong_crc_in_the_maps_zip_is_read_like_the_game_reads_it(tmp_path):
    """ttt_plaza_b7 ships a VMT whose CRC in the zip is wrong and the game loads it: the scan stages it, the
    rebuilt pakfile holds the same bytes with their real CRC."""
    src = bytearray(make_bsp(PAK))
    L = bsp_repack.lumps(bytes(src))
    pak = bytes(src[L[40][0]:L[40][0] + L[40][1]])
    with zipfile.ZipFile(io.BytesIO(pak)) as z:
        info = z.getinfo("materials/brick/wall01.vmt")
    struct.pack_into("<I", src, L[40][0] + info.header_offset + 14, info.CRC ^ 1)      # local header
    entry = pak.index(b"PK\x01\x02")                                                   # central directory
    for _ in range(len(PAK)):
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", pak, entry + 28)
        if pak[entry + 46:entry + 46 + name_len] == b"materials/brick/wall01.vmt":
            struct.pack_into("<I", src, L[40][0] + entry + 16, info.CRC ^ 1)
        entry += 46 + name_len + extra_len + comment_len
    src = bytes(src)
    with zipfile.ZipFile(io.BytesIO(src[L[40][0]:L[40][0] + L[40][1]])) as z:
        assert z.testzip() == "materials/brick/wall01.vmt"                 # Python's own check refuses it
        assert batch_scan_map_bsp._unreadable_member(z) is None
    out = bsp_repack.repack(src)
    assert bsp_repack.verify(src, out) == []
    files = dict(bsp_repack.pak_files(out)[0])
    assert files["materials/brick/wall01.vmt"] == PAK["materials/brick/wall01.vmt"]
    Lo = bsp_repack.lumps(out)
    with zipfile.ZipFile(io.BytesIO(out[Lo[40][0]:Lo[40][0] + Lo[40][1]])) as z:
        assert z.testzip() is None


def test_verify_catches_a_changed_lump_and_a_missing_file():
    src = make_bsp(PAK)
    out = bytearray(bsp_repack.repack(src, compress=False))
    L = bsp_repack.lumps(bytes(out))
    out[L[8][0] + 10] ^= 0xFF
    assert "lump 8" in bsp_repack.verify(src, bytes(out))
    fewer = [(n, d) for n, d in bsp_repack.pak_files(src)[0] if not n.endswith(".mp3")]
    assert "pakfile file list" in bsp_repack.verify(src, bsp_repack.repack(src, fewer))


def _scan_build(tmp_path, bsp: bytes, gmad=None):
    root = tmp_path / "in"
    (root / "mymap" / "maps").mkdir(parents=True)
    (root / "mymap" / "maps" / "mymap.bsp").write_bytes(bsp)
    (root / "mymap" / "models").mkdir()
    (root / "mymap" / "models" / "loose.dx90.vtx").write_bytes(b"x")
    (root / "mymap" / "models" / "loose.sw.vtx").write_bytes(b"x")
    work, out = tmp_path / "work", tmp_path / "out"
    assert batch_scan_map_bsp.main([str(root), "--work", str(work)]) == 0
    args = [str(root), "--work", str(work), "--out", str(out), "--jobs", "1"]
    assert batch_build_map_bsp.main(args) == 0
    return root, work, out


@pytest.mark.parametrize("pak_last", [True, False])
def test_scan_and_build_rebuild_the_map_wherever_its_pakfile_is(tmp_path, pak_last):
    src = make_bsp(PAK, pak_last=pak_last)
    root, work, out = _scan_build(tmp_path, src)
    built = (out / "mymap" / "maps" / "mymap.bsp").read_bytes()
    want = [(n, d) for n, d in bsp_repack.pak_files(src)[0] if not n.endswith(".dx80.vtx")]
    assert bsp_repack.verify(src, built, want) == []
    assert len(built) < len(src)
    # the loose mesh file the PC game never reads is gone, the one it reads stays
    assert not (out / "mymap" / "models" / "loose.sw.vtx").exists()
    assert (out / "mymap" / "models" / "loose.dx90.vtx").exists()
    summary = json.loads((work / "map_bsp_build_summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["reinjected_total"] == 1 and summary["run"]["mode"] == "LZMA"


def test_build_takes_the_optimized_staging_in_the_pakfiles_order(tmp_path):
    src = make_bsp(PAK)
    root = tmp_path / "in"
    (root / "m" / "maps").mkdir(parents=True)
    (root / "m" / "maps" / "m.bsp").write_bytes(src)
    work = tmp_path / "work"
    assert batch_scan_map_bsp.main([str(root), "--work", str(work)]) == 0
    stage = next(work.glob("staging/*/pak_contents"))
    (stage / "materials" / "brick" / "wall01.vtf").write_bytes(b"VTF\x00smaller")
    (stage / "materials" / "brick" / "wall01.vtf.texscreen.tmp").write_bytes(b"leftover")
    assert batch_build_map_bsp.main([str(root), "--work", str(work), "--out", str(tmp_path / "out"), "--jobs", "1"]) == 0
    files, _ = bsp_repack.pak_files((tmp_path / "out" / "m" / "maps" / "m.bsp").read_bytes())
    names = [n for n, _d in files]
    assert names == [n for n in PAK if not n.endswith(".dx80.vtx")]
    assert dict(files)["materials/brick/wall01.vtf"] == b"VTF\x00smaller"


def _gma(path: Path, title: str, desc: dict, files: dict):
    """a GMAD v3 file as gmad writes it"""
    out = bytearray(b"GMAD\x03" + struct.pack("<QQ", 0, 0) + b"\x00")
    out += title.encode() + b"\x00" + json.dumps(desc).encode() + b"\x00" + b"author\x00" + struct.pack("<i", 1)
    for k, (name, data) in enumerate(files.items(), 1):
        out += struct.pack("<I", k) + name.encode() + b"\x00" + struct.pack("<qI", len(data), 0)
    out += struct.pack("<I", 0)
    for data in files.values():
        out += data
    out += struct.pack("<I", 0)
    path.write_bytes(bytes(out))


def test_gma_header_gives_an_addon_json_gmad_accepts(tmp_path):
    gma = tmp_path / "x.gma"
    _gma(gma, "My Map", {"description": "nice", "type": "map", "tags": ["fun", "scenic", "build", "nope"]},
         {"maps/x.bsp": b"VBSP"})
    info = batch_unpack_addons.read_gma_header(gma)
    assert info["title"] == "My Map" and info["type"] == "map" and info["description"] == "nice"
    out = tmp_path / "out"
    (out / "maps").mkdir(parents=True)
    assert batch_unpack_addons.write_addon_json_from_gma(gma, out)
    data = json.loads((out / "addon.json").read_text(encoding="utf-8"))
    assert data == {"title": "My Map", "type": "map", "tags": ["fun", "scenic"], "ignore": [], "description": "nice"}
    assert not batch_unpack_addons.write_addon_json_from_gma(gma, out)     # an existing one is kept


def test_unpack_takes_a_single_archive_file(tmp_path):
    gma = tmp_path / "x.gma"
    _gma(gma, "My Map", {"description": "", "type": "map", "tags": []}, {"maps/x.bsp": b"VBSP"})
    candidates, errors = batch_unpack_addons.scan_archive_candidates(gma)
    assert errors == [] and len(candidates) == 1 and candidates[0].supported
    assert candidates[0].planned_output_dir.name == "x_extraido"
    # Mapas asks for plain names: the result folder and its .gma keep the addon's name
    out = tmp_path / "prepared"
    candidates, _ = batch_unpack_addons.scan_archive_candidates(gma, output_root=out, suffix="")
    assert candidates[0].planned_output_dir == out / "x"


def test_gma_pack_keeps_the_map_icon_and_names_what_gmad_left_out(tmp_path):
    """maps/<map>.png (old .bin addons) is not allowed in a .gma: a copy goes to maps/thumb/, where the game
    reads the icon; gmad -warninvalid's report of skipped files is read back."""
    addon = tmp_path / "addon"
    (addon / "maps").mkdir(parents=True)
    (addon / "maps" / "x.bsp").write_bytes(b"VBSP")
    (addon / "maps" / "x.png").write_bytes(b"icon")
    (addon / "maps" / "loose.png").write_bytes(b"not a map icon")
    batch_build_map_bsp._thumbs_for_gma(addon)
    assert (addon / "maps" / "thumb" / "x.png").read_bytes() == b"icon"
    assert not (addon / "maps" / "thumb" / "loose.png").exists()
    (addon / "maps" / "thumb" / "x.png").write_bytes(b"own thumb")
    batch_build_map_bsp._thumbs_for_gma(addon)                                # an existing thumb is kept
    assert (addon / "maps" / "thumb" / "x.png").read_bytes() == b"own thumb"

    stdout = ("Verifying file list...\n\tmaps/x.bsp\n\tmaps/x.png\n\t\t[Not allowed by whitelist]\n"
              "\tmaps/thumb/x.png\nWriting file list...\n")
    assert batch_build_map_bsp._gmad_rejected(stdout) == ["maps/x.png"]


def test_textures_a_map_draws_at_their_pixel_size_keep_it(tmp_path):
    """The engine divides a brush face's texture coordinates by its $basetexture's pixel size when the map
    loads (a reduced sign repeated 2x2 on ttt_stadium, 2026-11-13). World materials (also behind VBSP's
    cubemap patches, in the map or only by name), infodecal and env_sprite keep their texture's size; the
    world's normal maps and model textures still shrink. The map is read uncompressed, rebuilt with LZMA
    and, for the Mapas tab's staged pakfile, through stage_manifest.json."""
    sys.path.insert(0, str(ROOT / "tests"))
    from texlab import screen_compress as sc
    from texlab.size_bound import size_bound_refs
    from test_texture_stream import make_dxt_vtf, smooth_image
    vmts = {
        "brick/wall01": '"LightmappedGeneric" { "$basetexture" "brick/wall01" "$bumpmap" "brick/wall01_n" }',
        "signs/welcome": '"LightmappedGeneric" { "$basetexture" "signs/welcome" }',
        "signs/exit": '"LightmappedGeneric" { "$basetexture" "signs/exit" }',
        "decals/arrow": '"LightmappedGeneric" { "$basetexture" "decals/arrow" "$decal" 1 }',
        "sprites/lamp": '"Sprite" { "$basetexture" "sprites/lamp" }',
        "models/props/crate": '"VertexLitGeneric" { "$basetexture" "models/props/crate" }',
    }
    textures = ("brick/wall01", "brick/wall01_n", "signs/welcome", "signs/exit", "decals/arrow", "sprites/lamp",
                "models/props/crate")
    names = ["BRICK/WALL01", "maps/mymap/signs/welcome_10_-20_30", "maps/mymap/signs/exit_0_0_64"]
    entities = (b'{\n"classname" "worldspawn"\n}\n{\n"classname" "infodecal"\n"texture" "decals/arrow"\n}\n'
                b'{\n"classname" "env_sprite"\n"model" "sprites/lamp.vmt"\n}\n\x00')
    # the patch of the welcome sign is in the map; the exit sign's patch VMT is missing (read off its name)
    pak = {"materials/maps/mymap/signs/welcome_10_-20_30.vmt":
           b'"patch" { "include" "materials/signs/welcome.vmt" "insert" { "$envmap" "maps/mymap/c10_-20_30" } }'}
    bsp = make_bsp(pak, names=names, entities=entities)
    expected = {"brick/wall01", "signs/welcome", "signs/exit", "decals/arrow", "sprites/lamp"}

    root = tmp_path / "addon"
    for rel in textures:
        p = root / "materials" / (rel + ".vtf")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_dxt_vtf(smooth_image(512, 512)))
    for rel, text in vmts.items():
        (root / "materials" / (rel + ".vmt")).write_text(text)
    (root / "maps").mkdir()
    (root / "maps" / "mymap.bsp").write_bytes(bsp)
    assert size_bound_refs(root) == expected
    items = {i.ref: i for i in sc.plan_addon(root, "max")}
    for ref in expected:
        assert items[ref].drop == 0, ref
    assert items["brick/wall01_n"].drop >= 1 and items["models/props/crate"].drop >= 1

    (root / "maps" / "mymap.bsp").write_bytes(bsp_repack.repack(bsp))         # LZMA lumps and pakfile
    assert size_bound_refs(root) == expected

    # the staged pakfile holds the materials, the map is elsewhere
    stage = tmp_path / "staging" / "001_maps_mymap.bsp" / "pak_contents"
    for rel, text in vmts.items():
        (stage / "materials" / (rel + ".vmt")).parent.mkdir(parents=True, exist_ok=True)
        (stage / "materials" / (rel + ".vmt")).write_text(text)
    source = tmp_path / "source.bsp"
    source.write_bytes(bsp)
    assert size_bound_refs(stage) == set()                                 # no map known yet
    (stage.parent / "stage_manifest.json").write_text(json.dumps({"source_bsp": str(source)}))
    assert size_bound_refs(stage) == expected


def test_textures_of_the_sky_cubemaps_and_cutouts_keep_their_size(tmp_path):
    """NEVER_REDUCE: materials/skybox and materials/maps keep their size in every profile; cutouts
    ($alphatest) lose at most one level even on Máximo."""
    sys.path.insert(0, str(ROOT / "tests"))
    from texlab import screen_compress as sc
    from test_texture_stream import make_dxt_vtf, smooth_image  # same fixtures as the texture stream tests
    root = tmp_path / "addon"
    for rel in ("skybox/sky_rt", "maps/mymap/patched", "brick/wall", "brick/fence"):
        p = root / "materials" / (rel + ".vtf")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_dxt_vtf(smooth_image(512, 512)))
    (root / "materials" / "brick" / "fence.vmt").write_text('"LightmappedGeneric" { "$basetexture" "brick/fence" "$alphatest" 1 }')
    items = {i.ref: i for i in sc.plan_addon(root, "max")}
    assert items["skybox/sky_rt"].drop == 0 and items["maps/mymap/patched"].drop == 0
    assert items["brick/fence"].drop == 1
    assert items["brick/wall"].drop >= 2
