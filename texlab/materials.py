"""VMT (KeyValues) parsing and material/texture resolution across addon roots."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# VMT parameters that reference textures, with the role texlab assigns to them.
TEXTURE_PARAMS = {
    "$basetexture": "base",
    "$basetexture2": "base",
    "$bumpmap": "normal",
    "$normalmap": "normal",
    "$bumpmap2": "normal",
    "$envmapmask": "mask",
    "$phongexponenttexture": "mask",
    "$selfillummask": "mask",
    "$detail": "detail",
    "$lightwarptexture": "lut",
    "$phongwarptexture": "lut",
    "$blendmodulatetexture": "mask",
    "$ambientoccltexture": "mask",
}

_TOKEN = re.compile(r'"([^"]*)"|([{}])|([^\s{}"]+)')


def parse_keyvalues(text: str) -> tuple[str, dict]:
    """Parse a VMT into (shader, params). Keys are lower-cased; nested blocks become dicts."""
    lines = []
    for line in text.splitlines():
        cut = line.find("//")
        lines.append(line if cut < 0 else line[:cut])
    tokens = []
    for m in _TOKEN.finditer("\n".join(lines)):
        if m.group(1) is not None:
            tokens.append(("s", m.group(1)))
        elif m.group(2):
            tokens.append((m.group(2), m.group(2)))
        else:
            tokens.append(("s", m.group(3)))
    pos = 0

    def block() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            kind, value = tokens[pos]
            if kind == "}":
                pos += 1
                return out
            if kind != "s":
                pos += 1
                continue
            key = value.lower()
            pos += 1
            if pos < len(tokens) and tokens[pos][0] == "{":
                pos += 1
                out[key] = block()
            elif pos < len(tokens) and tokens[pos][0] == "s":
                out[key] = tokens[pos][1]
                pos += 1
        return out

    shader = ""
    while pos < len(tokens) and tokens[pos][0] != "s":
        pos += 1
    if pos < len(tokens):
        shader = tokens[pos][1].lower()
        pos += 1
    while pos < len(tokens) and tokens[pos][0] != "{":
        pos += 1
    pos += 1
    return shader, block()


_SLASHES = re.compile(r"/{2,}")


def clean_path(value: str) -> str:
    """A material path as the game's file system reads it: either slash, repeated slashes as one, any case."""
    return _SLASHES.sub("/", value.strip().replace("\\", "/").lower()).strip("/")


def normalize_texture_ref(value: str) -> str:
    """A texture name of a VMT as the engine reads it (clean_path, no materials/ prefix, no .vtf).
    M9K's Winchester 1887 writes its texture with two backslashes after "models": the game loads that file
    (and calls the texture models//weapons/v_models/...); read literally, the texture had no material and
    stayed a 16 px placeholder in game (2026-11-11)."""
    value = clean_path(value)
    if value.startswith("materials/"):
        value = value[len("materials/"):]
    if value.endswith(".vtf"):
        value = value[:-4]
    return value.strip("/")


@dataclass
class Material:
    path: str                      # relative, e.g. models/diggercars/supra/body (no extension)
    shader: str
    params: dict
    textures: dict[str, str] = field(default_factory=dict)   # param -> texture ref
    # (key, texture ref) of every other value of the VMT that names a texture file of the roots: parameters
    # outside TEXTURE_PARAMS ($texture2, $refracttinttexture, $emissiveblendbasetexture...), keys inside
    # blocks (">=dx90" { ... }, proxies) and conditional keys ("srgb?$basetexture"). The analysis does not
    # model how these are drawn; whoever needs to know that a material holds a texture reads both.
    other: list = field(default_factory=list)

    def all_textures(self) -> list:
        """[(key, texture ref)]: the modelled parameters, then the others."""
        return list(self.textures.items()) + list(self.other)

    def flag(self, name: str) -> bool:
        value = str(self.params.get(name, "0")).strip().strip('"')
        try:
            return float(value) != 0.0
        except ValueError:
            return value.lower() in ("true", "yes")

    _TRANSFORM_PARAM = {
        "$basetexture": "$basetexturetransform",
        "$basetexture2": "$basetexturetransform2",
        "$bumpmap": "$bumptransform",
        "$normalmap": "$bumptransform",
        "$detail": "$detailtexturetransform",
        "$envmapmask": "$envmapmasktransform",
    }

    def texture_scale(self, param: str) -> float:
        """Largest UV scale the material applies to this texture (1.0 when absent)."""
        scale = 1.0
        transform = self.params.get(self._TRANSFORM_PARAM.get(param, ""))
        if isinstance(transform, str):
            m = re.search(r"scale\s+\[?\s*([-\d.]+)(?:\s+([-\d.]+))?", transform)
            if m:
                sx = abs(float(m.group(1)))
                sy = abs(float(m.group(2))) if m.group(2) else sx
                scale = max(sx, sy, 1e-6)
        if param == "$detail":
            try:
                scale *= abs(float(str(self.params.get("$detailscale", "4")).split()[0].strip('"[')))
            except ValueError:
                scale *= 4.0
        return scale


class MaterialResolver:
    """Find VMT/VTF files across several addon roots (first match wins)."""

    def __init__(self, roots: list[Path]):
        self.roots = [Path(r) for r in roots]
        self._index: dict[str, Path] = {}
        for root in self.roots:
            mat = root / "materials"
            if not mat.is_dir():
                continue
            for p in mat.rglob("*"):
                if p.suffix.lower() in (".vmt", ".vtf") and p.is_file():
                    key = p.relative_to(mat).as_posix().lower()
                    self._index.setdefault(key, p)
        self._cache: dict[str, Material | None] = {}

    def find(self, relative: str, ext: str) -> Path | None:
        return self._index.get(f"{clean_path(relative)}.{ext}")

    def texture(self, value: str) -> str:
        """The texture a VMT value names: normalize_texture_ref; when no such VTF exists and the name
        without its extension does, that one (the engine drops whatever extension the name carries:
        "$basetexture" "models/x/barrel.vmt" loads barrel.vtf, seen in M9K and ARC9)."""
        ref = normalize_texture_ref(value)
        if f"{ref}.vtf" not in self._index and "." in ref.rsplit("/", 1)[-1]:
            short = ref.rsplit(".", 1)[0]
            if f"{short}.vtf" in self._index:
                return short
        return ref

    def _note_other(self, result: Material, key: str, value) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                self._note_other(result, k, v)
            return
        if not isinstance(value, str) or not value.strip():
            return
        key = key.split("?")[-1]
        if key == "include":            # a patch material's base: a VMT, never a texture
            return
        ref = self.texture(value)
        if f"{ref}.vtf" in self._index and result.textures.get(key) != ref and (key, ref) not in result.other:
            result.other.append((key, ref))

    def material(self, relative: str) -> Material | None:
        key = clean_path(relative)
        if key in self._cache:
            return self._cache[key]
        path = self.find(key, "vmt")
        result = None
        if path is not None:
            shader, params = parse_keyvalues(path.read_text(encoding="utf-8", errors="replace"))
            if shader == "patch" and "include" in params:
                base = self.material(normalize_texture_ref(params["include"]).removesuffix(".vmt"))
                merged = dict(base.params) if base else {}
                for section in ("insert", "replace"):
                    if isinstance(params.get(section), dict):
                        merged.update(params[section])
                shader = base.shader if base else shader
                params = merged
            result = Material(key, shader, params)
            for param, value in params.items():
                if param in TEXTURE_PARAMS and isinstance(value, str) and value.strip():
                    result.textures[param] = self.texture(value)
            for param, value in params.items():
                if param not in result.textures:
                    self._note_other(result, param, value)
        self._cache[key] = result
        return result

    def model_material(self, cdmaterials: list[str], texture_name: str) -> Material | None:
        name = clean_path(texture_name)
        for cd in cdmaterials:
            cd = clean_path(cd)
            rel = f"{cd}/{name}" if cd else name
            mat = self.material(rel)
            if mat is not None:
                return mat
        return self.material(name)


# Base-texture alpha is read by the shader when any of these is set.
_BASE_ALPHA_FLAGS = ("$translucent", "$alphatest", "$basealphaenvmapmask", "$basemapalphaphongmask",
                     "$blendtintbybasealpha", "$selfillum", "$allowalphatocoverage", "$distancealpha",
                     "$basemapluminancephongmask", "$vertexalpha", "$additive")


def alpha_required(material: Material, param: str) -> bool:
    """Conservative: does `material` read the alpha channel of the texture bound to `param`?"""
    if param in ("$basetexture", "$basetexture2"):
        if any(material.flag(f) for f in _BASE_ALPHA_FLAGS):
            return True
        if "$detail" in material.params and material.params.get("$detailblendmode", "0") not in ("0", "1", "4"):
            return True
        if material.shader not in ("vertexlitgeneric", "lightmappedgeneric", "unlitgeneric"):
            return True   # unknown shaders: assume alpha matters
        return False
    if param in ("$bumpmap", "$normalmap"):
        if material.flag("$normalmapalphaenvmapmask"):
            return True
        if material.flag("$phong") and not material.flag("$basemapalphaphongmask"):
            return True
        return False
    return True   # masks, detail, warps: keep their alpha
