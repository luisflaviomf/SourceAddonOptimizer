"""Per-mesh UV tolerance from the textures each material really samples.

1 texel of the most demanding texture (width x UV scale); tiled textures (scale > 4: noise/flake
patterns where a sub-tile shift is invisible) are ignored. Unknown materials: 1/1024.
"""
from __future__ import annotations

import struct
from pathlib import Path

from texlab.materials import MaterialResolver
from texlab.source_model import _read_mdl

SAMPLED = ("$basetexture", "$detail", "$bumpmap", "$normalmap", "$envmapmask", "$basetexture2")
DEFAULT = 1.0 / 1024.0


def _vtf_size(path: Path) -> int:
    try:
        with open(path, "rb") as fh:   # header only: width/height sit at bytes 16..19
            head = fh.read(20)
        w, h = struct.unpack_from("<HH", head, 16)
        return max(w, h, 1)
    except Exception:
        return 1024


def mesh_uv_tolerance(mdl: Path, resolver: MaterialResolver) -> list[float]:
    """Tolerance (UV units, for 1 texel) per mesh, in mesh_arrays order."""
    _v, _n, _c, textures, cdmaterials, skins, bodyparts = _read_mdl(mdl)
    tol_by_slot: dict[int, float] = {}
    for fam in skins or [list(range(len(textures)))]:
        for slot, tex_index in enumerate(fam):
            mat = resolver.model_material(cdmaterials, textures[tex_index]) if 0 <= tex_index < len(textures) else None
            need = 0.0
            if mat is not None:
                for param in SAMPLED:
                    ref = mat.textures.get(param)
                    if not ref:
                        continue
                    scale = mat.texture_scale(param)
                    if scale > 4.0:
                        continue
                    found = resolver.find(ref, "vtf")
                    need = max(need, (_vtf_size(found) if found else 1024) * scale)
            tol = 1.0 / need if need > 0 else DEFAULT
            tol_by_slot[slot] = min(tol_by_slot.get(slot, 1.0), tol)
    out = []
    for _bname, models in bodyparts:
        for _mname, _nv, _vi, meshes in models:
            for material, _mnv, _vo in meshes:
                out.append(tol_by_slot.get(material, DEFAULT))
    return out
