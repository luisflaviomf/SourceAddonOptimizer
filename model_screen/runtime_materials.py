"""Models whose look the game makes while it runs stay as their author made them.

Two properties of a model's materials, read from the files (no addon or weapon-base names):

- a texture parameter names an engine render target (`_rt_...`): the surface shows a picture the game
  renders every frame (scope picture, camera screen, mirror);
- a see-through surface (`$translucent` / `$additive`) runs a material proxy that the addon, or one of the
  addons beside it, defines in Lua (`matproxy.Add`): Lua changes it while the game runs (scope glass a
  weapon base swaps for its scope picture, red-dot lenses, digital counters).

Such models are neither streamed nor simplified (only the packaging steps that keep the image touch
them): what the game draws on those surfaces does not show on the stream's IMeshes (a screen-space
Refract), and on scopes any simplification shows at once (an eyepiece a few units from the eye under a
zoomed FOV: faceted rims). On the 2026-11-08 test bench this keeps 32 weapon models (scopes, sights,
scoped rifles, a stat screen) and nothing else: ordinary Refract windows (LVS), car paint driven by Lua
colour proxies (simfphys, opaque) and weapon camos (opaque) are not concerned.
"""
from __future__ import annotations

import re
from pathlib import Path

_SEE_THROUGH = ("$translucent", "$additive")
_LUA_PROXY = re.compile(r"""matproxy\s*\.\s*Add\s*\(\s*\{[^}]*?\bname\s*=\s*["']([^"']+)["']""", re.I | re.S)


def lua_proxy_names(roots, wanted) -> set[str]:
    """Which of the proxy names in `wanted` some Lua file under roots/*/lua defines with matproxy.Add."""
    wanted = {w.lower() for w in wanted}
    found: set[str] = set()
    if not wanted:
        return found
    for root in roots:
        lua = Path(root) / "lua"
        if not lua.is_dir():
            continue
        for f in lua.rglob("*.lua"):
            try:
                text = f.read_text(encoding="latin-1")
            except OSError:
                continue
            if "matproxy" not in text:
                continue
            found.update(n.lower() for n in _LUA_PROXY.findall(text) if n.lower() in wanted)
            if found >= wanted:
                return found
    return found


def _proxies(material) -> list[str]:
    block = material.params.get("proxies")
    return [k.lower() for k in block] if isinstance(block, dict) else []


def runtime_reason(material, lua_proxies) -> str | None:
    """Why the game makes this material's look at run time, or None."""
    for param, ref in sorted(material.textures.items()):
        if ref.startswith("_rt_"):
            return f"{material.path}: {param} is the render target {ref}"
    if any(material.flag(f) for f in _SEE_THROUGH):
        lua = sorted(p for p in _proxies(material) if p in lua_proxies)
        if lua:
            return f"{material.path}: see-through surface driven by the Lua proxy {lua[0]}"
    return None


def model_materials(mdl: Path, resolver) -> list:
    """Material of each texture slot (every skin family), first cdmaterials match like the engine."""
    from texlab.source_model import _read_mdl
    _v, _n, _c, textures, cdmaterials, _s, _b = _read_mdl(Path(mdl))
    out = []
    for tex in textures:
        mat = resolver.model_material(cdmaterials, tex)
        if mat is not None:
            out.append(mat)
    return out


def kept_reasons(models, resolver, lua_roots) -> list[str | None]:
    """Per model: why it stays as authored (a material made at run time), or None."""
    mats = []
    for m in models:
        try:
            mats.append(model_materials(m, resolver))
        except Exception:      # unreadable models fail later, in their own step
            mats.append([])
    wanted = {p for ms in mats for mat in ms if any(mat.flag(f) for f in _SEE_THROUGH) for p in _proxies(mat)}
    lua_proxies = lua_proxy_names(lua_roots, wanted)
    out = []
    for ms in mats:
        out.append(next((r for r in (runtime_reason(mat, lua_proxies) for mat in ms) if r), None))
    return out
