"""CPU reference renderer for texlab quality checks.

Renders a compiled Source model the way the player sees it closely enough to
compare texture variants: perspective-correct UVs, per-pixel mip selection
from screen-space UV derivatives (GPU style, 16x anisotropic LOD rule),
trilinear filtering, alpha test, alpha blending, and diffuse lighting through
the normal map. The same scene rendered with different texture files shows
exactly what each compression choice changes on screen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numba
import numpy as np

from .materials import MaterialResolver
from .source_model import SourceModel, load_model
from .vtf import decode_level, read_vtf, TEXTUREFLAGS_ENVMAP

CLAMP_S, CLAMP_T = 0x4, 0x8
MODE_OPAQUE, MODE_ALPHATEST, MODE_BLEND = 0, 1, 2


# ----------------------------------------------------------------------------- texture pack

class TexturePack:
    """All mip levels of a set of textures in one flat RGBA float32 buffer (linear 0..1)."""

    MAX_LEVELS = 16

    def __init__(self):
        self.chunks: list[np.ndarray] = []
        self.size = 0
        self.off, self.w, self.h, self.levels, self.flags = [], [], [], [], []
        self.slot_of: dict[str, int] = {}

    def add(self, key: str, vtf_path: Path | None) -> int:
        if key in self.slot_of:
            return self.slot_of[key]
        off = np.zeros(self.MAX_LEVELS, np.int64)
        ws = np.ones(self.MAX_LEVELS, np.int64)
        hs = np.ones(self.MAX_LEVELS, np.int64)
        levels, flags = 0, 0
        if vtf_path is not None:
            v = read_vtf(vtf_path)
            flags = v.flags
            if not v.flags & TEXTUREFLAGS_ENVMAP:
                levels = min(v.mip_count, self.MAX_LEVELS)
                for lvl in range(levels):
                    img = decode_level(v, lvl).astype(np.float32) / 255.0
                    h, w = img.shape[:2]
                    off[lvl], ws[lvl], hs[lvl] = self.size, w, h
                    self.chunks.append(img.reshape(-1))
                    self.size += img.size
        slot = len(self.off)
        self.off.append(off); self.w.append(ws); self.h.append(hs)
        self.levels.append(levels); self.flags.append(flags)
        self.slot_of[key] = slot
        return slot

    def arrays(self):
        data = np.concatenate(self.chunks) if self.chunks else np.zeros(4, np.float32)
        return (data, np.array(self.off, np.int64).reshape(-1, self.MAX_LEVELS),
                np.array(self.w, np.int64).reshape(-1, self.MAX_LEVELS),
                np.array(self.h, np.int64).reshape(-1, self.MAX_LEVELS),
                np.array(self.levels, np.int64), np.array(self.flags, np.int64))


# ----------------------------------------------------------------------------- scene

@dataclass
class Scene:
    positions: np.ndarray   # (N,3)
    normals: np.ndarray     # (N,3)
    tangents: np.ndarray    # (N,4)
    uvs: np.ndarray         # (N,2)
    tris: np.ndarray        # (T,3)
    base_slot: np.ndarray   # (T,) texture slot or -1
    bump_slot: np.ndarray   # (T,) texture slot or -1
    mode: np.ndarray        # (T,) MODE_*
    alpha_ref: np.ndarray   # (T,) alpha test reference
    textures_used: set


def build_scene(model: SourceModel, resolver: MaterialResolver, pack: TexturePack,
                texture_file, skin: int = 0) -> Scene:
    """texture_file(ref) -> Path of the VTF variant to use for that texture (or None)."""
    pos, nrm, tan, uv, tris, bs, ns, md, ar = [], [], [], [], [], [], [], [], []
    used = set()
    base_index = 0
    family = model.skin_families[min(skin, len(model.skin_families) - 1)] if model.skin_families else None
    for mesh in model.meshes:
        if not len(mesh.triangles):
            continue
        slot = mesh.material_slot
        tex_index = family[slot] if family and slot < len(family) else slot
        name = model.textures[tex_index] if 0 <= tex_index < len(model.textures) else ""
        material = resolver.model_material(model.cdmaterials, name) if name else None
        base_slot = bump_slot = -1
        mode, ref = MODE_OPAQUE, 0.5
        if material is not None:
            base = material.textures.get("$basetexture")
            bump = material.textures.get("$bumpmap") or material.textures.get("$normalmap")
            if base:
                base_slot = pack.add(base, texture_file(base)); used.add(base)
            if bump:
                bump_slot = pack.add(bump, texture_file(bump)); used.add(bump)
            alpha_is_mask = material.flag("$basealphaenvmapmask") or material.flag("$basemapalphaphongmask") \
                or material.flag("$blendtintbybasealpha")
            if material.flag("$translucent") and not alpha_is_mask:
                mode = MODE_BLEND
            elif material.flag("$alphatest") and not alpha_is_mask:
                mode = MODE_ALPHATEST
                try:
                    ref = float(str(material.params.get("$alphatestreference", "0.5")).strip('"'))
                except ValueError:
                    ref = 0.5
        n = len(mesh.positions)
        pos.append(mesh.positions); nrm.append(mesh.normals); uv.append(mesh.uvs)
        tan.append(mesh.tangents if mesh.tangents is not None else np.zeros((n, 4), np.float32))
        tris.append(mesh.triangles + base_index)
        t = len(mesh.triangles)
        bs.append(np.full(t, base_slot)); ns.append(np.full(t, bump_slot))
        md.append(np.full(t, mode)); ar.append(np.full(t, ref, np.float32))
        base_index += n
    cat = lambda xs, dt: np.ascontiguousarray(np.concatenate(xs).astype(dt))
    return Scene(cat(pos, np.float32), cat(nrm, np.float32), cat(tan, np.float32), cat(uv, np.float32),
                 cat(tris, np.int64), cat(bs, np.int64), cat(ns, np.int64), cat(md, np.int64),
                 cat(ar, np.float32), used)


# ----------------------------------------------------------------------------- rasterizer

@numba.njit(cache=True, inline="always")
def _texel(data, off, w, h, flags, x, y):
    if flags & 0x4:
        x = min(max(x, 0), w - 1)
    else:
        x = x % w
    if flags & 0x8:
        y = min(max(y, 0), h - 1)
    else:
        y = y % h
    i = off + (y * w + x) * 4
    return data[i], data[i + 1], data[i + 2], data[i + 3]


@numba.njit(cache=True)
def _bilinear(data, off, w, h, flags, u, v):
    x = u * w - 0.5
    y = v * h - 0.5
    x0 = math.floor(x)
    y0 = math.floor(y)
    fx = x - x0
    fy = y - y0
    ix0 = np.int64(x0)
    iy0 = np.int64(y0)
    a = _texel(data, off, w, h, flags, ix0, iy0)
    b = _texel(data, off, w, h, flags, ix0 + 1, iy0)
    c = _texel(data, off, w, h, flags, ix0, iy0 + 1)
    d = _texel(data, off, w, h, flags, ix0 + 1, iy0 + 1)
    r = (a[0] * (1 - fx) + b[0] * fx) * (1 - fy) + (c[0] * (1 - fx) + d[0] * fx) * fy
    g = (a[1] * (1 - fx) + b[1] * fx) * (1 - fy) + (c[1] * (1 - fx) + d[1] * fx) * fy
    bb = (a[2] * (1 - fx) + b[2] * fx) * (1 - fy) + (c[2] * (1 - fx) + d[2] * fx) * fy
    al = (a[3] * (1 - fx) + b[3] * fx) * (1 - fy) + (c[3] * (1 - fx) + d[3] * fx) * fy
    return r, g, bb, al


@numba.njit(cache=True)
def _sample(data, toff, tw, th, tlev, tflags, slot, u, v, dudx, dvdx, dudy, dvdy, aniso):
    levels = tlev[slot]
    if slot < 0 or levels == 0:
        return 0.5, 0.5, 0.5, 1.0, -1.0
    w0 = tw[slot, 0]
    h0 = th[slot, 0]
    ax = math.hypot(dudx * w0, dvdx * h0)
    ay = math.hypot(dudy * w0, dvdy * h0)
    major = max(ax, ay)
    minor = min(ax, ay)
    rho = max(minor, major / aniso)
    lam = math.log2(rho) if rho > 1e-12 else -30.0
    lam = min(max(lam, 0.0), levels - 1.0)
    l0 = int(math.floor(lam))
    l1 = min(l0 + 1, levels - 1)
    t = lam - l0
    flags = tflags[slot]
    r0, g0, b0, a0 = _bilinear(data, toff[slot, l0], tw[slot, l0], th[slot, l0], flags, u, v)
    if t > 1e-6 and l1 != l0:
        r1, g1, b1, a1 = _bilinear(data, toff[slot, l1], tw[slot, l1], th[slot, l1], flags, u, v)
        r0 = r0 * (1 - t) + r1 * t
        g0 = g0 * (1 - t) + g1 * t
        b0 = b0 * (1 - t) + b1 * t
        a0 = a0 * (1 - t) + a1 * t
    return r0, g0, b0, a0, lam


@numba.njit(cache=True)
def _raster(cam, right, up, fwd, focal, width, height, pos, nrm, tan, uvs, tris, base_slot, bump_slot,
            mode, alpha_ref, order, data, toff, tw, th, tlev, tflags, aniso, light, color, depth, blend_pass):
    near = 1.0
    nt = tris.shape[0]
    for oi in range(nt):
        ti = order[oi]
        m = mode[ti]
        if (m == 2) != blend_pass:
            continue
        i0 = tris[ti, 0]
        i1 = tris[ti, 1]
        i2 = tris[ti, 2]
        cx = np.empty(3)
        cy = np.empty(3)
        cz = np.empty(3)
        ok = True
        for k in range(3):
            idx = tris[ti, k]
            px = pos[idx, 0] - cam[0]
            py = pos[idx, 1] - cam[1]
            pz = pos[idx, 2] - cam[2]
            cx[k] = px * right[0] + py * right[1] + pz * right[2]
            cy[k] = px * up[0] + py * up[1] + pz * up[2]
            cz[k] = px * fwd[0] + py * fwd[1] + pz * fwd[2]
            if cz[k] < near:
                ok = False
        if not ok:
            continue
        sx = np.empty(3)
        sy = np.empty(3)
        for k in range(3):
            sx[k] = width * 0.5 + focal * cx[k] / cz[k]
            sy[k] = height * 0.5 - focal * cy[k] / cz[k]
        area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
        if abs(area) < 1e-9:
            continue
        minx = max(int(math.floor(min(sx[0], sx[1], sx[2]))), 0)
        maxx = min(int(math.ceil(max(sx[0], sx[1], sx[2]))), width - 1)
        miny = max(int(math.floor(min(sy[0], sy[1], sy[2]))), 0)
        maxy = min(int(math.ceil(max(sy[0], sy[1], sy[2]))), height - 1)
        if minx > maxx or miny > maxy:
            continue
        iz0 = 1.0 / cz[0]
        iz1 = 1.0 / cz[1]
        iz2 = 1.0 / cz[2]
        u0 = uvs[i0, 0]; v0 = uvs[i0, 1]
        u1 = uvs[i1, 0]; v1 = uvs[i1, 1]
        u2 = uvs[i2, 0]; v2 = uvs[i2, 1]
        inv_area = 1.0 / area
        for py in range(miny, maxy + 1):
            fy = py + 0.5
            for px in range(minx, maxx + 1):
                fx = px + 0.5
                w0 = ((sx[1] - fx) * (sy[2] - fy) - (sx[2] - fx) * (sy[1] - fy)) * inv_area
                w1 = ((sx[2] - fx) * (sy[0] - fy) - (sx[0] - fx) * (sy[2] - fy)) * inv_area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                iz = w0 * iz0 + w1 * iz1 + w2 * iz2
                if iz <= depth[py, px]:
                    continue
                # perspective-correct attributes at the pixel and at +x / +y neighbours
                uu = np.empty(3)
                vv = np.empty(3)
                for s in range(3):
                    qx = fx + (1.0 if s == 1 else 0.0)
                    qy = fy + (1.0 if s == 2 else 0.0)
                    a0 = ((sx[1] - qx) * (sy[2] - qy) - (sx[2] - qx) * (sy[1] - qy)) * inv_area
                    a1 = ((sx[2] - qx) * (sy[0] - qy) - (sx[0] - qx) * (sy[2] - qy)) * inv_area
                    a2 = 1.0 - a0 - a1
                    q = a0 * iz0 + a1 * iz1 + a2 * iz2
                    uu[s] = (a0 * u0 * iz0 + a1 * u1 * iz1 + a2 * u2 * iz2) / q
                    vv[s] = (a0 * v0 * iz0 + a1 * v1 * iz1 + a2 * v2 * iz2) / q
                b0 = w0 * iz0 / iz
                b1 = w1 * iz1 / iz
                b2 = w2 * iz2 / iz
                r, g, b, a, lam = _sample(data, toff, tw, th, tlev, tflags, base_slot[ti], uu[0], vv[0],
                                          uu[1] - uu[0], vv[1] - vv[0], uu[2] - uu[0], vv[2] - vv[0], aniso)
                if m == 1 and a < alpha_ref[ti]:
                    continue
                # shading normal (normal map through the tangent frame when present)
                nx = b0 * nrm[i0, 0] + b1 * nrm[i1, 0] + b2 * nrm[i2, 0]
                ny = b0 * nrm[i0, 1] + b1 * nrm[i1, 1] + b2 * nrm[i2, 1]
                nz = b0 * nrm[i0, 2] + b1 * nrm[i1, 2] + b2 * nrm[i2, 2]
                nl = math.sqrt(nx * nx + ny * ny + nz * nz) + 1e-12
                nx /= nl; ny /= nl; nz /= nl
                bs = bump_slot[ti]
                if bs >= 0:
                    tr, tg, tb, ta, _ = _sample(data, toff, tw, th, tlev, tflags, bs, uu[0], vv[0],
                                                uu[1] - uu[0], vv[1] - vv[0], uu[2] - uu[0], vv[2] - vv[0], aniso)
                    tx = b0 * tan[i0, 0] + b1 * tan[i1, 0] + b2 * tan[i2, 0]
                    ty = b0 * tan[i0, 1] + b1 * tan[i1, 1] + b2 * tan[i2, 1]
                    tz = b0 * tan[i0, 2] + b1 * tan[i1, 2] + b2 * tan[i2, 2]
                    hand = tan[i0, 3]
                    tl = math.sqrt(tx * tx + ty * ty + tz * tz)
                    if tl > 1e-6:
                        tx /= tl; ty /= tl; tz /= tl
                        bx = (ny * tz - nz * ty) * hand
                        by = (nz * tx - nx * tz) * hand
                        bz = (nx * ty - ny * tx) * hand
                        mx = tr * 2 - 1
                        my = tg * 2 - 1
                        mz = tb * 2 - 1
                        ox = mx * tx + my * bx + mz * nx
                        oy = mx * ty + my * by + mz * ny
                        oz = mx * tz + my * bz + mz * nz
                        ol = math.sqrt(ox * ox + oy * oy + oz * oz) + 1e-12
                        nx = ox / ol; ny = oy / ol; nz = oz / ol
                diff = nx * light[0] + ny * light[1] + nz * light[2]
                if diff < 0:
                    diff = -diff * 0.25   # soft back light so dark sides still show texture
                shade = 0.35 + 0.65 * diff
                if m == 2:
                    color[py, px, 0] = color[py, px, 0] * (1 - a) + r * shade * a
                    color[py, px, 1] = color[py, px, 1] * (1 - a) + g * shade * a
                    color[py, px, 2] = color[py, px, 2] * (1 - a) + b * shade * a
                else:
                    depth[py, px] = iz
                    color[py, px, 0] = r * shade
                    color[py, px, 1] = g * shade
                    color[py, px, 2] = b * shade


def look_at(eye, target, world_up=(0.0, 0.0, 1.0)):
    eye = np.asarray(eye, np.float64)
    fwd = np.asarray(target, np.float64) - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, world_up)
    if np.linalg.norm(right) < 1e-6:
        right = np.cross(fwd, (1.0, 0.0, 0.0))
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return eye, right, up, fwd


def render(scene: Scene, pack_arrays, eye, right, up, fwd, width=1920, height=1080, focal=938.0,
           aniso=16.0, background=(0.18, 0.2, 0.22)) -> np.ndarray:
    data, toff, tw, th, tlev, tflags = pack_arrays
    color = np.empty((height, width, 3), np.float32)
    color[:] = background
    depth = np.zeros((height, width), np.float64)
    light = np.array([0.35, -0.45, 0.82]); light /= np.linalg.norm(light)
    n = len(scene.tris)
    order = np.arange(n, dtype=np.int64)
    args = (np.asarray(eye, np.float64), right, up, fwd, float(focal), width, height,
            scene.positions, scene.normals, scene.tangents, scene.uvs, scene.tris, scene.base_slot,
            scene.bump_slot, scene.mode, scene.alpha_ref)
    _raster(*args, order, data, toff, tw, th, tlev, tflags, float(aniso), light, color, depth, False)
    if (scene.mode == MODE_BLEND).any():
        # back-to-front for translucent surfaces
        cent = scene.positions[scene.tris].mean(axis=1)
        dist = (cent - np.asarray(eye)) @ fwd
        order = np.argsort(-dist).astype(np.int64)
        _raster(*args, order, data, toff, tw, th, tlev, tflags, float(aniso), light, color, depth, True)
    return np.clip(color, 0.0, 1.0)


def standard_views(scene: Scene, distances=(24.0, 96.0, 256.0), directions=8, interior=True):
    """Orbit views around the model at several distances from its bounding box surface."""
    lo, hi = scene.positions.min(axis=0), scene.positions.max(axis=0)
    center = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    views = []
    for d in distances:
        for i in range(directions):
            ang = 2.0 * math.pi * i / directions
            dir_xy = np.array([math.cos(ang), math.sin(ang), 0.0])
            reach = abs(dir_xy[0]) * half[0] + abs(dir_xy[1]) * half[1]
            eye = center + dir_xy * (reach + d) + np.array([0.0, 0.0, half[2] * 0.6 + min(d, 64.0) * 0.4])
            target = center + np.array([0.0, 0.0, half[2] * 0.1])
            views.append((f"d{int(d)}_a{i}", look_at(eye, target)))
    if interior:
        eye = center + np.array([0.0, 0.0, half[2] * 0.35])
        for i in range(4):
            ang = 2.0 * math.pi * i / 4
            target = eye + np.array([math.cos(ang), math.sin(ang), -0.25])
            views.append((f"int_a{i}", look_at(eye, target)))
    return views
