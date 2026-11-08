"""Screen-space texel density: which mip level can a player ever see?

For every triangle that shows a texture we compute the texture-space Jacobian
(texels per world unit along the two principal directions of the surface).
Seen face-on from the closest realistic distance d with a focal length of f
pixels, the GPU samples level

    lambda = log2( max(s_min, s_max / A) * d / f )

where s_max >= s_min are the singular values and A is the anisotropic filtering
ratio (16x; A = 1 means isotropic trilinear). Facing the camera at distance d
is the finest level any view at distance >= d can request, so every mip level
below floor(lambda) is never sampled for that triangle.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .materials import MaterialResolver, TEXTURE_PARAMS
from .source_model import SourceModel, load_model
from .vtf import read_vtf, read_vtf_size  # noqa: F401  (read_vtf kept for callers importing it here)

UV_ROLES = {"base", "normal", "mask", "detail"}   # textures sampled through the mesh UVs


@dataclass
class ViewModel:
    """Closest realistic camera. Defaults: 2560x1440, GMod fov_desired 75 (4:3 horizontal)."""
    screen_height: int = 1440
    fov_43_horizontal_deg: float = 75.0
    closest_distance: float = 16.0      # Source units (1 unit = 1 inch); 16 = player hull radius
    anisotropy: float = 16.0

    @property
    def focal_px(self) -> float:
        vfov = 2.0 * math.atan(math.tan(math.radians(self.fov_43_horizontal_deg) / 2.0) * 3.0 / 4.0)
        return (self.screen_height / 2.0) / math.tan(vfov / 2.0)


@dataclass
class TextureUse:
    texture: str                      # materials-relative ref (no extension)
    width: int
    height: int
    mip_count: int
    roles: set = field(default_factory=set)
    materials: set = field(default_factory=set)
    models: set = field(default_factory=set)
    s_max: list = field(default_factory=list)      # per-triangle texels/unit, most stretched direction
    s_min: list = field(default_factory=list)      # per-triangle texels/unit, least stretched direction
    area: list = field(default_factory=list)       # per-triangle world area (units^2)
    uv: list = field(default_factory=list)         # per-triangle UV centroid (with material scale)

    def arrays(self, anisotropy: float = 16.0):
        if not self.s_max:
            return np.zeros(0), np.zeros(0)
        smax, smin = np.concatenate(self.s_max), np.concatenate(self.s_min)
        return np.maximum(smin, smax / max(anisotropy, 1.0)), np.concatenate(self.area)


def triangle_density(mesh_positions, mesh_uvs, triangles, tex_w, tex_h, uv_scale=1.0):
    """(s_max, s_min, area, uv_centroid) for every non-degenerate triangle."""
    p = mesh_positions[triangles].astype(np.float64)
    uvc = mesh_uvs[triangles].astype(np.float64).mean(axis=1) * uv_scale
    t = mesh_uvs[triangles].astype(np.float64) * np.array([tex_w * uv_scale, tex_h * uv_scale])
    e1, e2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    t1, t2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    n = np.cross(e1, e2)
    nlen = np.linalg.norm(n, axis=1)
    l1 = np.linalg.norm(e1, axis=1)
    ok = (nlen > 1e-9) & (l1 > 1e-9)
    e1, e2, t1, t2, n, nlen, l1, uvc = e1[ok], e2[ok], t1[ok], t2[ok], n[ok], nlen[ok], l1[ok], uvc[ok]
    a = e1 / l1[:, None]
    b = np.cross(n / nlen[:, None], a)
    # world edges in the triangle plane basis: E = [[|e1|, e2.a], [0, e2.b]]
    e00, e01, e11 = l1, np.einsum("ij,ij->i", e2, a), np.einsum("ij,ij->i", e2, b)
    ok2 = np.abs(e11) > 1e-12
    e00, e01, e11, t1, t2, nlen, uvc = e00[ok2], e01[ok2], e11[ok2], t1[ok2], t2[ok2], nlen[ok2], uvc[ok2]
    inv00, inv01, inv11 = 1.0 / e00, -e01 / (e00 * e11), 1.0 / e11
    # J = T @ inv(E); T columns are t1, t2
    j00 = t1[:, 0] * inv00
    j01 = t1[:, 0] * inv01 + t2[:, 0] * inv11
    j10 = t1[:, 1] * inv00
    j11 = t1[:, 1] * inv01 + t2[:, 1] * inv11
    # singular values of 2x2
    s_sum = j00 ** 2 + j01 ** 2 + j10 ** 2 + j11 ** 2
    det = np.abs(j00 * j11 - j01 * j10)
    disc = np.sqrt(np.maximum(s_sum ** 2 - 4.0 * det ** 2, 0.0))
    s_max = np.sqrt((s_sum + disc) / 2.0)
    s_min = np.sqrt(np.maximum((s_sum - disc) / 2.0, 0.0))
    keep = np.isfinite(s_max) & (s_max > 0)
    return s_max[keep], s_min[keep], (nlen[keep] / 2.0), uvc[keep]


_USAGE_STATE: dict = {}


def _init_usage_worker(roots: list[str]) -> None:
    """Per-process resolver and texture-header cache (also used by the sequential path)."""
    _USAGE_STATE["roots"] = list(roots)
    _USAGE_STATE["resolver"] = MaterialResolver([Path(r) for r in roots])
    _USAGE_STATE["tex"] = {}


def _tex_info(ref: str):
    cache = _USAGE_STATE["tex"]
    if ref not in cache:
        path = _USAGE_STATE["resolver"].find(ref, "vtf")
        if path is None:
            cache[ref] = None
        else:
            w, h, mips = read_vtf_size(path)
            cache[ref] = (w, h, mips, path)
    return cache[ref]


def _model_usage(args) -> list[tuple]:
    """Density rows of one model, in the exact order the sequential scan appends them."""
    mdl, addon_root, roots = Path(args[0]), Path(args[1]), list(args[2])
    stream = args[3] if len(args) > 3 else None
    if _USAGE_STATE.get("roots") != roots:
        _init_usage_worker(roots)
    resolver = _USAGE_STATE["resolver"]
    model = load_model(mdl)
    rel_model = mdl.relative_to(addon_root).as_posix()
    meshes = [(m.material_slot, m.positions, m.uvs, m.triangles) for m in model.meshes]
    if stream:
        # streamed model (Models Tela "stream"): the .mdl only has the coarse stand-in mesh; what is
        # seen up close is the original geometry of the GMOS stream
        from model_stream.geometry import decode_geometry
        try:
            meshes = [(g["slot"], g["pos"], g["uv"], g["tris"]) for g in decode_geometry(Path(stream).read_bytes())]
        except Exception:   # unreadable stream: measure the stand-in mesh like any model
            pass
    rows = []
    for slot, positions, uvs, triangles in meshes:
        if not len(triangles):
            continue
        for tex_name in model.texture_names_for_slot(slot):
            material = resolver.model_material(model.cdmaterials, tex_name)
            if material is None:
                continue
            for param, ref in material.textures.items():
                role = TEXTURE_PARAMS[param]
                if role not in UV_ROLES:
                    continue
                info = _tex_info(ref)
                if info is None:
                    continue
                w, h, mips, _ = info
                smax, smin, area, uvc = triangle_density(positions, uvs, triangles, w, h,
                                                         material.texture_scale(param))
                rows.append((ref, w, h, mips, role, material.path, rel_model, smax, smin, area, uvc))
    return rows


def usage_tasks(addon_root: Path, extra_roots: list[Path] | None = None) -> list[tuple]:
    """One task per model (sorted), for _model_usage on any executor."""
    addon_root = Path(addon_root)
    roots = tuple([str(addon_root)] + [str(r) for r in (extra_roots or [])])
    models = sorted((addon_root / "models").rglob("*.mdl"))
    streams = {}
    if (addon_root / "lua" / "autorun" / "client").is_dir():
        from model_stream.geometry import read_manifests
        streams = read_manifests(addon_root)
    tasks = []
    for m in models:
        task = (str(m), str(addon_root), roots)
        stream = streams.get(m.relative_to(addon_root).as_posix().lower())
        tasks.append(task + (str(stream),) if stream else task)
    return tasks


def collect_usage(addon_root: Path, extra_roots: list[Path] | None = None, jobs: int = 1,
                  executor=None) -> dict[str, TextureUse]:
    """Scan every model in the addon and accumulate texel density per texture.

    With jobs > 1 (or an executor) the models are read in worker processes; rows are merged in
    model order, so the result is identical to the sequential scan."""
    work = usage_tasks(addon_root, extra_roots)
    if executor is not None:
        per_model = list(executor.map(_model_usage, work, chunksize=1))
    elif jobs > 1 and len(work) > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            per_model = list(ex.map(_model_usage, work, chunksize=1))
    else:
        per_model = [_model_usage(w) for w in work]
    return merge_usage(per_model)


def merge_usage(per_model: list[list[tuple]]) -> dict[str, TextureUse]:
    """Accumulate _model_usage rows (models in sorted order) into TextureUse records."""
    uses: dict[str, TextureUse] = {}
    for rows in per_model:
        for ref, w, h, mips, role, material_path, rel_model, smax, smin, area, uvc in rows:
            use = uses.get(ref)
            if use is None:
                use = uses[ref] = TextureUse(ref, w, h, mips)
            use.roles.add(role)
            use.materials.add(material_path)
            use.models.add(rel_model)
            use.s_max.append(smax)
            use.s_min.append(smin)
            use.area.append(area)
            use.uv.append(uvc)
    return uses


def needed_level(density: np.ndarray, area: np.ndarray, view: ViewModel, coverage: float = 1.0) -> float:
    """Finest mip level (lambda) required, ignoring the finest (1-coverage) fraction of area."""
    if not len(density):
        return math.inf
    lam = np.log2(density * view.closest_distance / view.focal_px)
    if coverage >= 1.0:
        return float(lam.min())
    order = np.argsort(lam)
    cum = np.cumsum(area[order]) / area.sum()
    idx = int(np.searchsorted(cum, 1.0 - coverage, side="right"))
    return float(lam[order][min(idx, len(order) - 1)])
