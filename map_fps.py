"""Frame-rate edits of a map (Mapas tab): entity settings the map file holds that cost a large share of
each frame on the player's machine.

Measured in the lab (dedicated server + lab client on one CPU core, 64 views spread over the map; see
docs/mapas-fps-2026-11-14.md):
- point_spotlight with its dynamic light on: the light it casts relights every surface it touches, each frame
  (27% of the frame on ttt_libertycity, 18 such spotlights). Spawnflag 2 ("No Dynamic Light") keeps the
  beam and the halo.
- env_fire: the flames and smoke of 112 fires were 80% of the frame on ttt_lost_temple_v2 (spawnflags 2
  "Smokeless", 32 "No Glow").
- Fade distances are NOT used: adding them to the props that had none made frames slower (ttt_lego_
  treasureisland +41%, ttt_college +5%): in GMod a fading prop costs more than the culling saves.

World geometry and the 3D skybox (a third of the frame each on ttt_rooftops_2016_v1) cannot be thinned
without compiling the map again.
"""
from __future__ import annotations

import re
import struct

import bsp_repack

ENTITY = re.compile(rb"\{[^{}]*\}")
KEYVALUE = re.compile(rb'"([^"]*)"\s*"([^"]*)"')
CONTENTS_SOLID = 0x1


class PointContents:
    """Whether a point is inside solid world brushes (BSP tree walk: planes, nodes, leaves)."""

    def __init__(self, data: bytes, L):
        lump = lambda i: bsp_repack.lump_data(data, L, i)  # noqa: E731
        pl, nd, lf = lump(1), lump(5), lump(10)
        self.planes = [struct.unpack_from("<4f", pl, o) for o in range(0, len(pl) - 19, 20)]
        self.nodes = [struct.unpack_from("<iii", nd, o) for o in range(0, len(nd) - 31, 32)]
        size = 32 if L[10][2] == 1 else 56
        self.contents = [struct.unpack_from("<i", lf, o)[0] for o in range(0, len(lf) - size + 1, size)]

    def solid(self, p) -> bool:
        if not self.nodes:
            return False
        node = 0
        for _ in range(len(self.nodes) + 1):
            if node < 0:
                leaf = -1 - node
                return 0 <= leaf < len(self.contents) and bool(self.contents[leaf] & CONTENTS_SOLID)
            plane, front, back = self.nodes[node]
            if not 0 <= plane < len(self.planes):
                return False
            nx, ny, nz, d = self.planes[plane]
            node = front if nx * p[0] + ny * p[1] + nz * p[2] - d >= 0 else back
        return False

# (class, spawnflags to set, report key) per level
SPOTLIGHT_NO_DLIGHT = 2
FIRE_SMOKELESS = 2
FIRE_NO_GLOW = 32
EDITS = {
    "balanced": ((b"point_spotlight", SPOTLIGHT_NO_DLIGHT, "spotlights_dynamic_light_off"),),
    "max": ((b"point_spotlight", SPOTLIGHT_NO_DLIGHT, "spotlights_dynamic_light_off"),),
}
SPRITE_LEVELS = ("balanced", "max")      # glow sprites drawn as additive sprites (_sprite_edit)


def _set_kv(block: bytes, key: bytes, value: bytes) -> bytes:
    pat = re.compile(rb'("' + re.escape(key) + rb'"\s*)"[^"]*"', re.IGNORECASE)
    if pat.search(block):
        return pat.sub(lambda m: m.group(1) + b'"' + value + b'"', block, count=1)
    return block[:-1].rstrip(b"\n") + b'\n"' + key + b'" "' + value + b'"\n}'


def _flag_edit(block: bytes, kv: dict, add: int) -> bytes | None:
    """block with `add` set in its spawnflags, None when they are all set already (or unreadable)."""
    try:
        flags = int(kv.get(b"spawnflags", b"0") or 0)
    except ValueError:
        return None
    if flags & add == add:
        return None
    return _set_kv(block, b"spawnflags", str(flags | add).encode())


def _sprite_edit(block: bytes, kv: dict, contents: PointContents | None, report: dict) -> bytes | None:
    """A "World Space Glow" env_sprite drawn as a plain additive sprite (render mode 9 -> 5), None to keep it.

    A glow sprite asks the GPU every frame how much of it is visible (occlusion query); 556 of them were most
    of the frame on ttt_nuclear_power_v4_fix (heaviest views 23.6 -> 13.9 ms as additive sprites, same look),
    11 were 22% of minecraft_haven's heaviest views. An additive sprite has the same world size but is hidden
    by whatever is in front of it pixel by pixel: one whose centre is inside solid world brushes (the glowstone
    blocks of ttt_minecraft_haven) would disappear, so it stays a glow, as does one attached to a moving parent
    (its position here is not where it is drawn). Mode 3 ("Glow") keeps a constant size on screen: kept."""
    if kv.get(b"rendermode", b"").strip() != b"9":
        return None
    if kv.get(b"parentname", b"").strip():
        report["sprites_kept_parented"] += 1
        return None
    try:
        origin = [float(x) for x in kv.get(b"origin", b"").split()]
    except ValueError:
        origin = []
    if len(origin) != 3:
        return None
    if contents is not None and contents.solid(origin):
        report["sprites_kept_inside_solid"] += 1
        return None
    report["sprites_glow_to_additive"] += 1
    return _set_kv(block, b"rendermode", b"5")


def plan_entities(entities: bytes, level: str, contents: PointContents | None = None) -> tuple[bytes | None, dict]:
    """The entity lump with the edits of `level` (None: nothing to change), and a report."""
    edits = EDITS.get(level, ())
    sprites = level in SPRITE_LEVELS
    report = {key: 0 for _cls, _flag, key in edits}
    if sprites:
        report.update(sprites_glow_to_additive=0, sprites_kept_inside_solid=0, sprites_kept_parented=0)
    if not edits and not sprites:
        return None, report
    by_class = {cls: (flag, key) for cls, flag, key in edits}
    out, last, changed = bytearray(), 0, False
    for m in ENTITY.finditer(entities):
        block = m.group(0)
        kv = {k.lower(): v for k, v in KEYVALUE.findall(block)}
        cls = kv.get(b"classname", b"").lower()
        new = None
        if cls in by_class:
            new = _flag_edit(block, kv, by_class[cls][0])
            if new is not None:
                report[by_class[cls][1]] += 1
        elif cls == b"env_sprite" and sprites:
            new = _sprite_edit(block, kv, contents, report)
        if new is None:
            continue
        out += entities[last:m.start()] + new
        last = m.end()
        changed = True
    if not changed:
        return None, report
    out += entities[last:]
    return bytes(out), report


def apply(data: bytes, level: str) -> tuple[bytes, dict]:
    """The map with the frame-rate edits of `level`; the same bytes when nothing changes."""
    if level not in EDITS:
        return data, {}
    L = bsp_repack.lumps(data)
    if L[0][3]:                          # an entity lump Valve's tools compressed: left as it is
        return data, {"level": level, "entities": {}}
    try:
        contents = PointContents(data, L) if level in SPRITE_LEVELS else None
    except (struct.error, bsp_repack.BspError):
        contents = None
    ents, report = plan_entities(bsp_repack.lump_data(data, L, 0), level, contents)
    if level in SPRITE_LEVELS and contents is None:
        ents, report = None, {}          # no tree to tell embedded sprites apart: nothing changes
    if ents is None:
        return data, {"level": level, "entities": report}
    return bsp_repack.replace_lumps(data, {0: ents}), {"level": level, "entities": report}
