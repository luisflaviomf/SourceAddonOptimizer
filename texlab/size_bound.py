"""Textures whose pixel size is part of how big they look in a map: they keep their size.

When a map loads, Source computes the texture coordinates of every brush face and displacement in texels
and divides them by the size of the material's $basetexture (the material's "mapping size"); the result is
baked into the world's vertex buffers. A $basetexture with half the pixels repeats twice on every wall: on
ttt_stadium the 1024x256 "MARCEM STADIUM" sign, reduced to 512x128, became a 2x2 grid (2026-11-13). A
texture swapped in later (IMaterial:SetTexture, the texture stream) does not move those coordinates either.
The size of a decal placed by the map (infodecal) and of a sprite (env_sprite, env_glow) also comes from
the texture's pixel size.

Model textures (coordinates from 0 to 1), overlays (info_overlay has its own coordinates) and the other
textures of a world material ($bumpmap, $detail, $basetexture2..., sampled at the base texture's
coordinates) can change size. A world material without $basetexture (water) keeps its normal map too.

The maps read are the addon's own (maps/*.bsp, LZMA lumps and LZMA pakfile entries included) and, for a
map's pakfile staged by the Mapas tab, the map it came from (stage_manifest.json next to the staged
folder). Rule from the files only: texdata names (lumps 43/44), entity keyvalues, VMTs (VBSP's cubemap
"patch" materials resolved to the material they include).
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from .materials import clean_path, normalize_texture_ref, parse_keyvalues

# VBSP names its cubemap/water patches maps/<map>/<material>_<x>_<y>_<z>, <material>_depth_<n> and
# <material>_wvt_patch; when the patch VMT is missing the base material is read off the name
_PATCH_NAME = re.compile(r"^maps/[^/]+/(.+?)(?:_-?\d+_-?\d+_-?\d+|_depth_-?\d+|_wvt_patch)$")
_ENTITY = re.compile(rb"\{([^{}]*)\}")
_KEYVALUE = re.compile(rb'"([^"]*)"\s*"([^"]*)"')
# entity class -> keyvalue naming a material drawn at the texture's own pixel size
SIZED_ENTITIES = {b"infodecal": b"texture", b"env_sprite": b"model", b"env_sprite_oriented": b"model",
                  b"env_glow": b"model"}


def map_bsps(root: Path) -> list[Path]:
    """The maps whose materials live in root: root/maps/*.bsp and, for a staged pakfile, its source map."""
    root = Path(root)
    found = sorted((root / "maps").glob("*.bsp")) if (root / "maps").is_dir() else []
    manifest = root.parent / "stage_manifest.json"
    if manifest.is_file():
        try:
            source = Path(json.loads(manifest.read_text(encoding="utf-8")).get("source_bsp") or "")
        except (OSError, ValueError):
            source = None
        if source and source.is_file() and source not in found:
            found.append(source)
    return found


def _material_name(value: str) -> str:
    name = clean_path(value)
    if name.startswith("materials/"):
        name = name[len("materials/"):]
    for ext in (".vmt", ".spr"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    return name


def read_map(path: Path) -> tuple[set[str], set[str], dict[str, str]]:
    """(world materials, materials drawn at their texture's size by entities, {material: VMT text} of the
    map's pakfile). An unreadable map gives empty sets."""
    import bsp_repack
    try:
        data = Path(path).read_bytes()
        L = bsp_repack.lumps(data)
        strings = bsp_repack.lump_data(data, L, 43)
        table = bsp_repack.lump_data(data, L, 44)
        entities = bsp_repack.lump_data(data, L, 0)
    except Exception:  # noqa: BLE001 - not a map this reader knows: nothing to protect
        return set(), set(), {}
    world = set()
    for (offset,) in struct.iter_unpack("<i", table[:len(table) // 4 * 4]):
        if 0 <= offset < len(strings):
            end = strings.find(b"\0", offset)
            world.add(_material_name(strings[offset:end if end >= 0 else None].decode("latin-1")))
    sized = set()
    for block in _ENTITY.finditer(entities):
        keys = {k.lower(): v for k, v in _KEYVALUE.findall(block.group(1))}
        key = SIZED_ENTITIES.get(keys.get(b"classname", b"").lower())
        if key and keys.get(key, b"").strip():
            sized.add(_material_name(keys[key].decode("latin-1")))
    vmts = {}
    try:
        files, _comment = bsp_repack.pak_files(data, L)
    except Exception:  # noqa: BLE001 - a broken pakfile: the materials on disk are still read
        files = []
    for name, body in files:
        name = clean_path(name)
        if name.startswith("materials/") and name.endswith(".vmt"):
            vmts[name[len("materials/"):-len(".vmt")]] = body.decode("utf-8", errors="replace")
    return world, sized, vmts


class _Materials:
    """VMT text by material name: the maps' pakfiles first, then root/materials (any case)."""

    def __init__(self, root: Path, vmts: dict[str, str]):
        self.vmts = vmts
        self.disk = {}
        mat = Path(root) / "materials"
        if mat.is_dir():
            for p in mat.rglob("*.vmt"):
                self.disk.setdefault(p.relative_to(mat).as_posix().lower()[:-4], p)

    def text(self, name: str) -> str | None:
        if name in self.vmts:
            return self.vmts[name]
        p = self.disk.get(name)
        if p is None:
            return None
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def textures(self, name: str, world: bool, depth: int = 0) -> set[str]:
        """The textures of material `name` that set its size: $basetexture; for a world material without one
        (water), its normal map too."""
        text = self.text(name)
        if text is None:
            m = _PATCH_NAME.match(name)
            return self.textures(m.group(1), world, depth + 1) if m and depth < 4 else set()
        shader, params = parse_keyvalues(text)
        if shader == "patch":
            for section in ("replace", "insert"):
                block = params.get(section)
                if isinstance(block, dict) and isinstance(block.get("$basetexture"), str):
                    return {normalize_texture_ref(block["$basetexture"])}
            include = params.get("include")
            if isinstance(include, str) and depth < 4:
                return self.textures(_material_name(include), world, depth + 1)
            return set()
        base = params.get("$basetexture")
        if isinstance(base, str) and base.strip():
            return {normalize_texture_ref(base)}
        if world:
            return {normalize_texture_ref(params[k]) for k in ("$normalmap", "$bumpmap")
                    if isinstance(params.get(k), str) and params[k].strip()}
        return set()


def size_bound_refs(root: Path) -> set[str]:
    """Texture refs (relative to materials/, no extension, lower case) that must keep their pixel size."""
    world, sized, vmts = set(), set(), {}
    for bsp in map_bsps(root):
        w, s, v = read_map(bsp)
        world |= w
        sized |= s
        for name, text in v.items():
            vmts.setdefault(name, text)
    if not world and not sized:
        return set()
    mats = _Materials(root, vmts)
    refs = set()
    for name in world:
        refs |= mats.textures(name, world=True)
    for name in sized:
        refs |= mats.textures(name, world=False)
    return {r for r in refs if r}
