"""Map frame-rate edits (map_fps.py): spawnflags of the entities measured as expensive, per level; every other
byte of the map stays as it was, and the rebuilt map passes the byte-for-byte check."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import batch_build_map_bsp  # noqa: E402
import batch_scan_map_bsp  # noqa: E402
import bsp_repack  # noqa: E402
import map_fps  # noqa: E402
from test_map_compress import make_bsp  # noqa: E402

ENTS = (b'{\n"classname" "worldspawn"\n}\n'
        b'{\n"classname" "point_spotlight"\n"spawnflags" "1"\n}\n'
        b'{\n"classname" "point_spotlight"\n"spawnflags" "3"\n}\n'
        b'{\n"classname" "point_spotlight"\n}\n'
        b'{\n"classname" "prop_physics"\n"model" "models/props/crate.mdl"\n}\n\x00')


def test_spotlights_lose_their_dynamic_light_and_nothing_else_changes():
    src = make_bsp({}, entities=ENTS)
    out, report = map_fps.apply(src, "max")
    assert report["entities"]["spotlights_dynamic_light_off"] == 2
    L = bsp_repack.lumps(out)
    e = bsp_repack.lump_data(out, L, 0)
    assert e.count(b'"spawnflags" "3"') == 2 and e.count(b'"spawnflags" "2"') == 1
    assert e.replace(b'"spawnflags" "3"', b"").replace(b'\n"spawnflags" "2"\n', b"\n").count(b"point_spotlight") == 3
    A = bsp_repack.lumps(src)
    for i in range(64):
        if i not in (0, 35):                        # 35: same data, its absolute offsets moved with it
            assert bsp_repack.lump_data(src, A, i) == bsp_repack.lump_data(out, L, i), i
    assert bsp_repack.verify(out, bsp_repack.repack(out)) == []


def test_no_level_or_nothing_to_change_leaves_the_map_byte_for_byte():
    src = make_bsp({}, entities=ENTS)
    assert map_fps.apply(src, "none")[0] == src
    once = map_fps.apply(src, "balanced")[0]
    again, report = map_fps.apply(once, "balanced")
    assert again == once and report["entities"]["spotlights_dynamic_light_off"] == 0


class _Contents:
    """Stand-in for PointContents: solid below z = 0."""
    def solid(self, p):
        return p[2] < 0


def test_world_glow_sprites_become_additive_unless_embedded_or_parented():
    """Render mode 9 -> 5 for a sprite in open air; kept for one whose centre is inside solid world brushes
    (ttt_minecraft_haven's glowstone: an additive sprite there disappears), one with a parent, and the
    screen-sized "Glow" mode 3."""
    ents = (b'{\n"classname" "env_sprite"\n"origin" "0 0 64"\n"rendermode" "9"\n}\n'
            b'{\n"classname" "env_sprite"\n"origin" "0 0 -8"\n"rendermode" "9"\n}\n'
            b'{\n"classname" "env_sprite"\n"origin" "0 0 64"\n"rendermode" "9"\n"parentname" "door"\n}\n'
            b'{\n"classname" "env_sprite"\n"origin" "0 0 64"\n"rendermode" "3"\n}\n\x00')
    out, report = map_fps.plan_entities(ents, "balanced", _Contents())
    assert report["sprites_glow_to_additive"] == 1 and report["sprites_kept_inside_solid"] == 1
    assert report["sprites_kept_parented"] == 1
    blocks = out.split(b"}\n")
    assert b'"rendermode" "5"' in blocks[0]
    assert b'"rendermode" "9"' in blocks[1] and b'"rendermode" "9"' in blocks[2] and b'"rendermode" "3"' in blocks[3]
    assert map_fps.plan_entities(ents, "none", _Contents())[0] is None


def test_mapbuild_applies_the_fps_level_and_reports_it(tmp_path):
    root = tmp_path / "in"
    (root / "mymap" / "maps").mkdir(parents=True)
    (root / "mymap" / "maps" / "mymap.bsp").write_bytes(make_bsp({"materials/x.vmt": b"x"}, entities=ENTS))
    work, out = tmp_path / "work", tmp_path / "out"
    assert batch_scan_map_bsp.main([str(root), "--work", str(work)]) == 0
    assert batch_build_map_bsp.main([str(root), "--work", str(work), "--out", str(out), "--jobs", "1",
                                     "--fps", "max"]) == 0
    built = (out / "mymap" / "maps" / "mymap.bsp").read_bytes()
    ents = bsp_repack.lump_data(built, bsp_repack.lumps(built), 0)
    assert b'"spawnflags" "1"' not in ents
    summary = json.loads((work / "map_bsp_build_summary.json").read_text(encoding="utf-8"))
    item = summary["items"][0]
    assert item["status"] == "reinjected" and item["fps"]["entities"]["spotlights_dynamic_light_off"] == 2
