"""Per texture coding choices of the texture stream, read from the files (no per-addon rules).

- alpha_uses(): how much each texture's alpha shows on screen, from the VMTs of the addon and its
  neighbours. weight None = no material reads the alpha (dropped: DXT1 output); w = an alpha error e
  changes the final pixel by about w * e (tint or envmap strength); 1 = full (see-through, self-illum,
  unknown shader).
- choose(): normal-map or colour mode, chroma resolution and the largest quantization step (STEPS) whose
  reconstruction keeps the target PSNR (+ MARGIN for the DXT step after it); alpha against its own target
  max(30, target + 20 log10(weight)).
"""
from __future__ import annotations

import lzma
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from texlab import native
from texlab.materials import TEXTURE_PARAMS, normalize_texture_ref

from .codec import dwt, idwt, levels_for, normal_z, psnr, quant, rgb_to_ycocg, unit_normals, ycocg_to_rgb

STEPS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 19, 22, 26, 30, 36, 44, 54, 66]
MARGIN = 1.2
MIN_SIDE = 64
# Uncompressed sources (HUD art, sights, icons: hard edges the DXT step never blurred) keep at least this
# much; they come back in their own format. DXT3 is decoded to DXT5.
RAW_TARGET = 40.0
STREAM_FORMATS = (13, 15, 14, 0, 1, 2, 3, 12, 16)
ALPHA_FORMATS = (15, 14, 0, 1, 12)
SEE = ("$translucent", "$alphatest", "$allowalphatocoverage", "$distancealpha", "$vertexalpha")
SHADERS = ("vertexlitgeneric", "lightmappedgeneric", "unlitgeneric")
COLOR_PROXIES = {"playercolor", "itemtintcolor", "playerweaponcolor", "modelglowcolor"}
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_MATPROXY = re.compile(r"""matproxy\s*\.\s*Add\s*\(\s*\{[^}]*?\bname\s*=\s*["']([^"']+)["']""", re.I | re.S)


def _flag(p, k):
    v = str(p.get(k, "0")).strip().strip('"')
    try:
        return float(v) != 0
    except ValueError:
        return v.lower() in ("true", "yes")


def _vec(p, k, default):
    v = p.get(k)
    if not isinstance(v, str):
        return default
    nums = [float(x) for x in _NUM.findall(v)]
    if not nums:
        return default
    if "{" in v:
        nums = [x / 255 for x in nums]
    if len(nums) == 1:
        nums = nums * 3
    return nums[:3]


def _lua_color_proxies(roots):
    """matproxy names whose Lua code writes $color2 (tint driven at run time)."""
    names = set()
    for root in roots:
        lua_dir = Path(root) / "lua"
        if not lua_dir.is_dir():
            continue
        for lua in lua_dir.rglob("*.lua"):
            try:
                t = lua.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "matproxy" not in t:
                continue
            for m in _MATPROXY.finditer(t):
                if "color2" in t[m.start(): m.start() + 4000].lower():
                    names.add(m.group(1).lower())
    return names


def _tint_driven(p, lua_color):
    prox = p.get("proxies")
    if not isinstance(prox, dict):
        return False
    for name, block in prox.items():
        n = name.lower()
        if n in COLOR_PROXIES or n in lua_color:
            return True
        if isinstance(block, dict) and any(isinstance(v, str) and "$color2" in v.lower() for v in block.values()):
            return True
    return False


def read_vmts(resolver):
    """-> [(vmt ref, shader, params)] for every VMT the resolver sees (patch materials merged)."""
    out = []
    for key in sorted(k for k in resolver._index if k.endswith(".vmt")):
        try:
            m = resolver.material(key[:-4])
        except Exception:
            continue
        if m is not None:
            out.append((key[:-4], (m.shader or "").lower(), m.params))
    return out


def alpha_uses(vmts, roots) -> dict:
    """{texture ref: {"w": weight | None, "roles": set}} over the given VMTs."""
    lua_color = _lua_color_proxies(roots)
    w = defaultdict(lambda: None)
    roles = defaultdict(set)

    def put(ref, val):
        cur = w[ref]
        w[ref] = val if cur is None else max(cur, val)

    for _vref, sh, p in vmts:
        for k, val in p.items():
            if not (isinstance(val, str) and k in TEXTURE_PARAMS):
                continue
            ref = normalize_texture_ref(val)
            role = TEXTURE_PARAMS[k]
            roles[ref].add("ssbump" if role == "normal" and _flag(p, "$ssbump") else role)
            if role == "base":
                if sh not in SHADERS or any(_flag(p, f) for f in SEE) or _flag(p, "$selfillum"):
                    put(ref, 1.0)
                if "$detail" in p and str(p.get("$detailblendmode", "0")).strip() not in ("0", "1", "4"):
                    put(ref, 1.0)
                if _flag(p, "$basemapalphaphongmask"):
                    put(ref, 0.7)
                if _flag(p, "$basealphaenvmapmask") and "$envmap" in p:
                    put(ref, min(1.0, max(0.2, max(_vec(p, "$envmaptint", [1, 1, 1])))))
                if _flag(p, "$blendtintbybasealpha"):
                    if _tint_driven(p, lua_color):
                        put(ref, 1.0)
                    else:
                        put(ref, min(1.0, max(0.15, max(abs(1 - c) for c in _vec(p, "$color2", [1, 1, 1])))))
            elif role == "normal":
                if _flag(p, "$normalmapalphaenvmapmask") and "$envmap" in p:
                    put(ref, min(1.0, max(0.2, max(_vec(p, "$envmaptint", [1, 1, 1])))))
                if _flag(p, "$phong") and not _flag(p, "$basemapalphaphongmask"):
                    put(ref, 0.7)
            else:
                put(ref, 1.0)
    return {ref: {"w": w[ref], "roles": roles[ref]} for ref in roles}


def _recon(ll, bands, st, drop, L):
    rb = []
    for li, trio in enumerate(bands):
        fine = li >= L - drop
        rb.append(tuple(np.zeros_like(b) if fine else quant(b, st) * st for b in trio))
    return idwt(np.round(ll / st) * st, rb)


def _pack(body, ll, bands, st, drop, L):
    from .codec import put_band
    put_band(body, np.round(ll / st).astype(np.int64))
    for li, trio in enumerate(bands):
        fine = li >= L - drop
        for b in trio:
            put_band(body, np.zeros(1, np.int64) if fine else quant(b, st))


def _search(fn, target):
    """largest step in STEPS with fn(step) >= target (fn decreasing in step), else the smallest step; binary
    search with virtual ends (5 evaluations for 19 steps)."""
    lo, hi = -1, len(STEPS)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fn(STEPS[mid]) >= target:
            lo = mid
        else:
            hi = mid
    return STEPS[max(lo, 0)]


def _search_together(evaluate, count, target):
    """`count` searches like _search run side by side: evaluate(step, wanted) -> {i: value} for the searches
    (indices) asking for that step in this round. Each ends where _search(lambda st: value of i, target) ends;
    the steps asked for by more than one are evaluated once (the luma plane is the same for both chroma
    resolutions)."""
    lo, hi = [-1] * count, [len(STEPS)] * count
    while True:
        asks = {}
        for i in range(count):
            if hi[i] - lo[i] > 1:
                asks.setdefault((lo[i] + hi[i]) // 2, []).append(i)
        if not asks:
            return [STEPS[max(x, 0)] for x in lo]
        for mid, wanted in asks.items():
            values = evaluate(STEPS[mid], wanted)
            for i in wanted:
                if values[i] >= target:
                    lo[i] = mid
                else:
                    hi[i] = mid


class _Plane:
    """The wavelet coefficients of a plane and its reconstruction at a step: _recon, by the native library
    when it is there (same bytes, texlab.native.ts_recon)."""

    def __init__(self, coefficients, levels):
        self.ll, self.bands = coefficients
        self.levels = levels
        self.flat = None

    def recon(self, st, drop):
        if native.available():
            if self.flat is None:
                self.flat = np.concatenate([self.ll.ravel()] + [b.ravel() for trio in self.bands for b in trio])
            out = native.ts_recon(self.flat, self.ll.shape[0] << self.levels, self.ll.shape[1] << self.levels,
                                  self.levels, st, drop)
            if out is not None:
                return out
        return _recon(self.ll, self.bands, st, drop, self.levels)


def _psnr_of(squared_error):
    """codec.psnr from the squared error array it averages."""
    mse = np.mean(squared_error)
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def _size(body) -> int:
    return len(lzma.compress(bytes(body), format=lzma.FORMAT_ALONE, preset=6))


def eligible(v) -> str | None:
    """None when the codec takes this VTF, else the reason it stays a VTF."""
    if v.fmt not in STREAM_FORMATS:
        return "format"
    if v.frames > 1 or v.faces > 1 or v.depth > 1:
        return "animated"
    if min(v.width, v.height) < MIN_SIDE or levels_for(v.width, v.height) < 2:
        return "size"
    return None


def choose(v, rgba, use, target, transforms=None) -> dict:
    """v: texlab.vtf.VTF, rgba: level 0 decoded (float 0..255), use: alpha_uses() entry or None.
    -> {"normal", "alpha", "step", "drop", "alpha_step", "rgb_db", "alpha_db"}
    transforms: a list that receives the wavelet transform of every plane the encoder will code, in its
    order (codec.encode takes it and does not transform the same planes again)."""
    L = levels_for(v.width, v.height)
    rgb = rgba[..., :3]
    aw = use["w"] if use else 1.0          # textures no VMT names: keep their alpha
    roles = set(use["roles"]) if use else set()
    has_alpha = v.fmt in ALPHA_FORMATS and aw is not None and rgba[..., 3].min() < 255
    if v.fmt not in (13, 15, 14):
        target = max(target, RAW_TARGET)
    zr = normal_z(rgb[..., 0], rgb[..., 1])
    is_normal = roles == {"normal"} or (not roles and psnr(zr, rgb[..., 2]) >= 45.0 and rgb[..., 2].mean() > 170)
    n0 = unit_normals(rgb) if is_normal else None
    if is_normal and np.mean(n0[..., 2] < 127.5) > 0.01:
        is_normal, n0 = False, None       # vectors pointing into the surface: not a tangent-space normal map
    t = target + MARGIN
    cands = []
    # Every quality below is the same number the plain numpy expressions give (kept as the fallback): the
    # native library returns the array numpy would have averaged, and a value already measured is not
    # measured again.
    if is_normal:
        dw = [dwt(n0[..., c] - 128.0, L) for c in (0, 1)]
        planes = [_Plane(d, L) for d in dw]
        n0_c = np.ascontiguousarray(n0)
        measured = {}

        def q_rgb(st):
            if st not in measured:
                rx, ry = [p.recon(st, 0) for p in planes]
                sq = native.ts_sqerr_normal(rx, ry, n0_c) if native.available() else None
                if sq is not None:
                    measured[st] = _psnr_of(sq)
                else:
                    x, y = [np.clip(r + 128, 0, 255) for r in (rx, ry)]
                    measured[st] = psnr(unit_normals(np.stack([x, y, normal_z(x, y)], -1)), n0)
            return measured[st]
        st = _search(q_rgb, t)
        cands.append((None, st, 0, q_rgb(st)))          # the only candidate: its size decides nothing
    else:
        dw = [dwt(q, L) for q in rgb_to_ycocg(rgb)]
        planes = [_Plane(d, L) for d in dw]
        rgb_c = np.ascontiguousarray(rgb)
        drops = (1, 0)
        measured = {}

        def q_rgb(st, wanted):
            yy = None
            for i in wanted:
                drop = drops[i]
                if (st, drop) not in measured:
                    if yy is None:
                        yy = planes[0].recon(st, 0)
                    co = planes[1].recon(st * 2, drop)
                    cg = planes[2].recon(st * 2, drop)
                    sq = native.ts_sqerr_ycocg(yy, co, cg, rgb_c) if native.available() else None
                    measured[(st, drop)] = (_psnr_of(sq) if sq is not None else
                                            psnr(np.clip(np.round(ycocg_to_rgb(yy, co, cg)), 0, 255), rgb))
            return {i: measured[(st, drops[i])] for i in wanted}
        steps = _search_together(q_rgb, len(drops), t)

        def packed_size(st, drop):
            body = bytearray()
            _pack(body, *dw[0], st, 0, L)
            _pack(body, *dw[1], st * 2, drop, L)
            _pack(body, *dw[2], st * 2, drop, L)
            return _size(body)
        for i, drop in enumerate(drops):
            cands.append((lambda st=steps[i], drop=drop: packed_size(st, drop), steps[i], drop,
                          q_rgb(steps[i], [i])[i]))
    ok = [c for c in cands if c[3] >= t]
    if len(ok) > 1:
        # the smaller of the candidates that keep the target; their sizes are only worked out here, where
        # they decide (a compression pass over all the coefficients each)
        _size_, st, drop, q = min(ok, key=lambda c: c[0]())
    else:
        _size_, st, drop, q = ok[0] if ok else max(cands, key=lambda c: c[3])
    ast = aq = None
    if has_alpha:
        a = rgba[..., 3]
        da = dwt(a - 128.0, L)
        alpha_plane = _Plane(da, L)
        a_c = np.ascontiguousarray(a)
        measured_a = {}

        def q_a(s):
            if s not in measured_a:
                r = alpha_plane.recon(s, 0)
                sq = native.ts_sqerr_plane(r, a_c) if native.available() else None
                measured_a[s] = (_psnr_of(sq) if sq is not None else
                                 psnr(np.clip(np.round(r + 128), 0, 255), a))
            return measured_a[s]
        ast = _search(q_a, max(30.0, t + 20 * np.log10(aw)))
        aq = q_a(ast)
    if transforms is not None:
        transforms[:] = list(dw) + ([da] if has_alpha else [])
    return {"normal": bool(is_normal), "alpha": bool(has_alpha), "step": st, "drop": drop, "alpha_step": ast,
            "rgb_db": round(float(q), 2), "alpha_db": round(float(aq), 2) if aq is not None else None}
