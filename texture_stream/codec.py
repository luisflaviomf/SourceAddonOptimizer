"""GMOT v2: wavelet texture codec decoded by the Lua runtime (decoder.lua) into DXT1/DXT5 VTFs.

File = b"GMOT" + u8 2 + u8 nchunks + nchunks x u32 chunk size + chunks. Every chunk is LZMA-alone with the
uncompressed size in its header (the format util.Decompress reads).
Chunk 0: <HHBBBBB w, h, levels L, planes n, mode (0 colour YCoCg[+A], 1 normal map X,Y[+A] with Z rebuilt),
         chroma_drop, P> + n x <f step> + <I header_len> + output VTF header bytes + <BB out_fmt, mips>,
         then per plane: LL band and the LH, HL, HH bands of levels L..P+1 (coarse to fine).
Then one chunk per band of the finer levels P..1: per level (coarse to fine), per plane, LH, HL, HH.
P = levels to drop until the largest side is <= the preview size: chunk 0 alone gives a preview.

Bands: u8 type (0 all zero, 1 int8, 2 int16; +16 when a noise map of TILE x TILE tiles precedes the
coefficients) + data. Noise filling: per tile, the RMS of the coefficients quantized to zero (3 bits,
NLEV levels); the decoder replaces those zeros with uniform noise of that amplitude from a 32-bit LCG
seeded per band, so fine grain keeps its texture instead of turning flat.

The 5/3 lifting wavelet is scaled by sqrt(2) per step so one step size fits every band. Colour planes are
YCoCg (chroma at a coarser step, optionally without its finest level); normal maps store the normalised
X and Y. lua_vtf() mirrors the Lua decoder (up to float rounding) so the encoder can measure what players
get. See docs/texturas-stream-2026-11-09.md.
"""
from __future__ import annotations

import lzma
import struct

import numpy as np

from texlab.vtf import image_bytes, write_vtf

VERSION = 2
S2 = float(np.sqrt(2.0))
MAX_LEVELS = 5
# Uncompressed formats decoded back to the same format (same VRAM, no DXT step): byte order of each pixel as
# indices into (R, G, B, A, 255). DXT1/DXT5 (and DXT3 sources) come out as DXT1/DXT5.
RAW_ORDER = {0: (0, 1, 2, 3), 1: (3, 2, 1, 0), 2: (0, 1, 2), 3: (2, 1, 0), 12: (2, 1, 0, 3), 16: (2, 1, 0, 4)}


# ------------------------------------------------------------------ 5/3 lifting with orthonormal-ish scaling
def fwd53(x):
    s, d = x[..., 0::2].copy(), x[..., 1::2].copy()
    d -= 0.5 * (s + np.concatenate([s[..., 1:], s[..., -1:]], -1))
    s += 0.25 * (d + np.concatenate([d[..., :1], d[..., :-1]], -1))
    return s * S2, d / S2


_A, _B = 1.0 / S2, 0.25 * S2


def inv53(s, d):
    """inverse of fwd53, with the decoder.lua arithmetic (same operations in the same order, so the Python
    mirror and the Lua output agree to the last bit)"""
    dp = np.concatenate([d[..., :1], d[..., :-1]], -1)
    s = s * _A - _B * (dp + d)
    d = d * S2 + 0.5 * (s + np.concatenate([s[..., 1:], s[..., -1:]], -1))
    x = np.empty(s.shape[:-1] + (s.shape[-1] * 2,), np.float64)
    x[..., 0::2], x[..., 1::2] = s, d
    return x


def dwt(p, levels):
    """plane -> (LL, [(LH, HL, HH) per level, coarse to fine])."""
    bands = []
    ll = p
    for _ in range(levels):
        lo, hi = fwd53(ll)                                   # horizontal
        lolo, lohi = fwd53(lo.swapaxes(0, 1))                # vertical
        hilo, hihi = fwd53(hi.swapaxes(0, 1))
        bands.append((lohi.swapaxes(0, 1), hilo.swapaxes(0, 1), hihi.swapaxes(0, 1)))
        ll = lolo.swapaxes(0, 1)
    return ll, bands[::-1]


def idwt(ll, bands):
    for lh, hl, hh in bands:
        lo = inv53(ll.swapaxes(0, 1), lh.swapaxes(0, 1)).swapaxes(0, 1)
        hi = inv53(hl.swapaxes(0, 1), hh.swapaxes(0, 1)).swapaxes(0, 1)
        ll = inv53(lo, hi)
    return ll


def levels_for(w, h, max_levels=MAX_LEVELS):
    n = 0
    while n < max_levels and w % (2 << n) == 0 and h % (2 << n) == 0 and (w >> (n + 1)) >= 4 and (h >> (n + 1)) >= 4:
        n += 1
    return n


# ------------------------------------------------------------------ quantization / serialization
def quant(c, st):
    """dead-zone quantizer (rounding offset 0.4)."""
    return (np.sign(c) * np.floor(np.abs(c) / st + 0.4)).astype(np.int64)


def put_band(out: bytearray, q):
    if not q.any():
        out.append(0)
        return
    if q.min() >= -128 and q.max() <= 127:
        out.append(1)
        out += q.astype(np.int8).tobytes()
    else:
        if q.min() < -32768 or q.max() > 32767:
            raise ValueError("coefficient out of int16 range")
        out.append(2)
        out += q.astype("<i2").tobytes()


# ------------------------------------------------------------------ noise filling
TILE = 8
NLEV = 8
AMPK = 0.6 / NLEV * np.sqrt(3.0)
LCG_A, LCG_C, M32 = 1664525, 1013904223, 1 << 32


def lcg_uniform(seed, n):
    """u_k = x_k / 2^32 for x_k = (A x_{k-1} + C) mod 2^32, k = 1..n (vectorized jump-ahead)."""
    a = np.full(n, LCG_A, np.uint64)
    ak = np.cumprod(a)                                   # A^k mod 2^64 (wraps), k = 1..n
    apow_prev = np.concatenate([[np.uint64(1)], ak[:-1]])
    sk = np.cumsum(apow_prev)                            # sum_{j<k} A^j mod 2^64
    x = (ak * np.uint64(seed) + np.uint64(LCG_C) * sk) & np.uint64(M32 - 1)
    return x.astype(np.float64) / M32


def band_seed(index):
    return (index * 2654435761 + 12345) % M32


def seed_index(pi, lvl, k):
    """k = 0 for LL, 1..3 for LH, HL, HH."""
    return pi * 64 + lvl * 4 + k


def _tiles(h, w):
    th, tw = max(1, h // TILE), max(1, w // TILE)
    ty = np.minimum(np.arange(h) // TILE, th - 1)
    tx = np.minimum(np.arange(w) // TILE, tw - 1)
    return th, tw, ty, tx


def noise_codes(b, q, st):
    """per tile: RMS of the coefficients that quantized to zero, in (0.6 / NLEV) * st units."""
    h, w = b.shape
    th, tw, ty, tx = _tiles(h, w)
    idx = (ty[:, None] * tw + tx[None, :]).ravel()
    zc = (q == 0).ravel()
    e = np.bincount(idx, weights=np.where(zc, b.ravel() ** 2, 0.0), minlength=th * tw)
    cnt = np.bincount(idx, weights=zc.astype(np.float64), minlength=th * tw)
    rms = np.sqrt(e / np.maximum(cnt, 1)).reshape(th, tw)
    return np.clip(np.round(rms / (0.6 / NLEV * st)), 0, NLEV - 1).astype(np.int64)


def noise_fill(qf, codes, st, index):
    """qf: dequantized band; zeros replaced by uniform noise of the tile amplitude."""
    h, w = qf.shape
    _th, _tw, ty, tx = _tiles(h, w)
    amp = codes[ty[:, None], tx[None, :]] * AMPK * st
    u = lcg_uniform(band_seed(index), h * w).reshape(h, w)
    return np.where(qf == 0, (2 * u - 1) * amp, qf)


def put_band_nf(out: bytearray, q, codes):
    """band with a noise map: type | 16, map bytes, then the coefficients (zeros written explicitly)."""
    tmp = bytearray()
    if q.any():
        put_band(tmp, q)
    else:
        tmp = bytearray([1]) + q.astype(np.int8).tobytes()   # explicit zeros so the map has positions
    out.append(tmp[0] | 16)
    out += codes.astype(np.uint8).tobytes()
    out += tmp[1:]


# ------------------------------------------------------------------ colour transforms
def rgb_to_ycocg(rgb):
    r, g, b = [rgb[..., i].astype(np.float64) for i in range(3)]
    return [0.25 * r + 0.5 * g + 0.25 * b - 128.0, 0.5 * r - 0.5 * b, -0.25 * r + 0.5 * g - 0.25 * b]


def ycocg_to_rgb(y, co, cg):
    y = y + 128.0
    t = y - cg
    return np.stack([t + co, y + cg, t - co], -1)


def normal_z(x, y):
    nx, ny = x / 127.5 - 1.0, y / 127.5 - 1.0
    nz = np.sqrt(np.clip(1.0 - nx * nx - ny * ny, 0.0, 1.0))
    return (nz + 1.0) * 127.5


def unit_normals(rgb):
    """normal-map texels -> normalized vectors expressed in 0..255 (what the shader lights with)."""
    n = rgb / 127.5 - 1.0
    ln = np.sqrt((n * n).sum(-1, keepdims=True))
    return (n / np.maximum(ln, 1e-6) + 1.0) * 127.5


# ------------------------------------------------------------------ encoder
def lzma_chunk(body) -> bytes:
    comp = bytearray(lzma.compress(bytes(body), format=lzma.FORMAT_ALONE,
                                   filters=[{"id": lzma.FILTER_LZMA1, "preset": 9 | lzma.PRESET_EXTREME,
                                             "dict_size": 1 << 24}]))
    comp[5:13] = struct.pack("<Q", len(body))       # known size: what util.Decompress expects
    return bytes(comp)


def unlzma(c) -> bytes:
    d = bytearray(c)
    d[5:13] = bytes([255]) * 8
    return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)


def encode(v, rgba, step, alpha_step=None, chroma_drop=1, normal=False, keep_alpha=False, fill_levels=2,
           preview_side=512, chroma_mul=2.0, transforms=None) -> bytes:
    """v: texlab.vtf.VTF of the source (header, flags, mips); rgba: its level 0 decoded (float, 0..255).
    Output format: the source's when it is uncompressed (RAW_ORDER), else DXT5 with alpha / DXT1.
    transforms: dwt() of each plane coded here, in order, when the caller already has them (choose())."""
    w, h = v.width, v.height
    L = levels_for(w, h)
    if L < 2:
        raise ValueError("size not suited")
    rgba = np.asarray(rgba, np.float64)
    if normal:
        n = unit_normals(rgba[..., :3])
        planes = [n[..., 0] - 128.0, n[..., 1] - 128.0]
        steps = [step, step]
        drops = [0, 0]
        fills = [0, 0]
        chroma_drop = 0
    else:
        planes = rgb_to_ycocg(rgba)
        steps = [step, step * chroma_mul, step * chroma_mul]
        drops = [0, chroma_drop, chroma_drop]
        fills = [fill_levels, 0, 0]
    if keep_alpha:
        planes.append(rgba[..., 3] - 128.0)
        steps.append(alpha_step or step)
        drops.append(0)
        fills.append(fill_levels)
    if transforms is not None and len(transforms) != len(planes):
        raise ValueError("transforms do not match the planes")
    out_fmt = v.fmt if v.fmt in RAW_ORDER else (15 if keep_alpha else 13)
    mips = v.mip_count
    levels = [b"\0" * image_bytes(out_fmt, max(1, w >> i), max(1, h >> i)) for i in range(mips)]
    flags = v.flags
    if out_fmt == 13 or (out_fmt in RAW_ORDER and len(RAW_ORDER[out_fmt]) == 3):
        flags &= ~0x2000   # no eight-bit alpha
    if mips == 1:
        # the game sets NOMIP and NOLOD on a single-level texture when it loads one without them, with a
        # console line each time; the texture built in game carries them from the start
        flags |= 0x300
    full = write_vtf(v, out_fmt, w, h, levels, flags=flags)
    header = full[:len(full) - sum(len(x) for x in levels)]
    P = 0
    while P < L and max(w, h) >> P > preview_side:
        P += 1
    head = bytearray(struct.pack("<HHBBBBB", w, h, L, len(planes), 1 if normal else 0, chroma_drop, P))
    head += struct.pack("<%df" % len(planes), *steps)
    head += struct.pack("<I", len(header)) + header
    head += struct.pack("<BB", out_fmt, mips)
    fine = {}
    for pi, (p, st, drop, fill) in enumerate(zip(planes, steps, drops, fills)):
        st = float(np.float32(st))               # the decoder reads the step as float32
        ll, bands = transforms[pi] if transforms is not None else dwt(p, L)
        put_band(head, np.round(ll / st).astype(np.int64))
        for li, trio in enumerate(bands):
            lvl = L - li
            for k, b in enumerate(trio):
                out = head if lvl > P else fine.setdefault((lvl, pi, k), bytearray())
                if lvl <= drop:
                    put_band(out, np.zeros(1, np.int64))
                elif lvl <= fill:
                    q = quant(b, st)
                    put_band_nf(out, q, noise_codes(b, q, st))
                else:
                    put_band(out, quant(b, st))
    chunks = [head] + [fine[key] for key in sorted(fine, key=lambda t: (-t[0], t[1], t[2]))]
    if len(chunks) > 255:
        raise ValueError("too many chunks")
    comp = [lzma_chunk(c) for c in chunks]
    return b"GMOT" + bytes([VERSION, len(comp)]) + b"".join(struct.pack("<I", len(c)) for c in comp) + b"".join(comp)


# ------------------------------------------------------------------ decoder (mirrors decoder.lua)
def read_band(buf, pos, shape, st, index):
    """dequantized band; zeros noise-filled when the band carries a noise map."""
    t = buf[pos]
    pos += 1
    n = shape[0] * shape[1]
    codes = None
    if t & 16:
        th, tw = max(1, shape[0] // TILE), max(1, shape[1] // TILE)
        codes = np.frombuffer(buf, np.uint8, th * tw, pos).reshape(th, tw).astype(np.float64)
        pos += th * tw
        t &= 15
    if t == 0:
        return np.zeros(shape), pos
    if t == 1:
        q, pos = np.frombuffer(buf, np.int8, n, pos).reshape(shape).astype(np.float64), pos + n
    else:
        q, pos = np.frombuffer(buf, "<i2", n, pos).reshape(shape).astype(np.float64), pos + 2 * n
    q = q * st
    if codes is not None:
        q = noise_fill(q, codes, st, index)
    return q, pos


def decode_planes(data: bytes, preview=False):
    """-> planes, mode, VTF header, out_fmt, mips, (w, h) exactly as the Lua decoder builds them.
    preview: True for the preview (P finest levels left out), or the number of finest levels to leave out."""
    if data[:4] != b"GMOT" or data[4] != VERSION:
        raise ValueError("not GMOT v2")
    nch = data[5]
    sizes = struct.unpack_from("<%dI" % nch, data, 6)
    offs = np.cumsum([6 + 4 * nch] + list(sizes))

    def chunk(c):
        return unlzma(data[offs[c]:offs[c] + sizes[c]])
    body = chunk(0)
    w, h, L, n, mode, _cdrop, P = struct.unpack_from("<HHBBBBB", body, 0)
    pos = 9
    steps = struct.unpack_from("<%df" % n, body, pos)
    pos += 4 * n
    hl = struct.unpack_from("<I", body, pos)[0]
    pos += 4
    header = bytearray(body[pos:pos + hl])
    pos += hl
    out_fmt, mips = struct.unpack_from("<BB", body, pos)
    pos += 2
    planes = []
    for pi in range(n):
        st = steps[pi]
        ll, pos = read_band(body, pos, (h >> L, w >> L), st, seed_index(pi, L, 0))
        bands = []
        for lvl in range(L, P, -1):
            trio = []
            for k in range(3):
                b, pos = read_band(body, pos, (h >> lvl, w >> lvl), st, seed_index(pi, lvl, k + 1))
                trio.append(b)
            bands.append(tuple(trio))
        planes.append(idwt(ll, bands))
    drop = P if preview is True else min(int(preview or 0), P)
    c = 1
    for lvl in range(P, drop, -1):
        for pi in range(n):
            trio = []
            for k in range(3):
                b, _ = read_band(chunk(c), 0, (h >> lvl, w >> lvl), steps[pi], seed_index(pi, lvl, k + 1))
                c += 1
                trio.append(b)
            planes[pi] = idwt(planes[pi], [tuple(trio)])
    if drop > 0:
        planes = [p / 2 ** drop for p in planes]
        w, h = w >> drop, h >> drop
        if mips > 1:
            mips = max(1, mips - drop)
        struct.pack_into("<HH", header, 16, w, h)
        header[56] = mips
    return planes, mode, bytes(header), out_fmt, mips, (w, h)


def decode(data: bytes, preview=False):
    """-> rgba (h, w, 4) float rounded to 0..255 before DXT, VTF header, out_fmt, mips."""
    planes, mode, header, out_fmt, mips, (w, h) = decode_planes(data, preview)
    n = len(planes)
    if mode == 1:
        x, y = planes[0] + 128.0, planes[1] + 128.0
        rgb = np.stack([x, y, normal_z(np.clip(x, 0, 255), np.clip(y, 0, 255))], -1)
    else:
        rgb = ycocg_to_rgb(*planes[:3])
    has_alpha = n == (2 if mode == 1 else 3) + 1
    a = planes[-1] + 128.0 if has_alpha else np.full((h, w), 255.0)
    rgba = np.concatenate([rgb, a[..., None]], -1)
    return np.clip(np.round(rgba), 0, 255), header, out_fmt, mips


def psnr(a, b):
    mse = np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


# ------------------------------------------------------------------ DXT encoder of the Lua decoder
def _q5(v):
    return np.clip(np.floor(v * 31 / 255 + 0.5), 0, 31)


def _q6(v):
    return np.clip(np.floor(v * 63 / 255 + 0.5), 0, 63)


def _seq_sum(a):
    """sum over axis 1 in index order, as the Lua loops add (numpy's own sum pairs the terms, which moves
    the last bit and flips exact ties)."""
    s = a[:, 0].copy()
    for k in range(1, a.shape[1]):
        s = s + a[:, k]
    return s


def lua_dxt1(px):
    """px (nb,16,3) float 0..255 -> (nb,8) uint8, same arithmetic as decoder.lua: bounding-box diagonal
    from the covariance signs, inset 1/16, selectors by projection, one least-squares refinement."""
    mn, mx = px.min(1), px.max(1)
    m = _seq_sum(px) / 16
    dg = px[..., 1] - m[:, None, 1]
    crg = _seq_sum((px[..., 0] - m[:, None, 0]) * dg)
    cbg = _seq_sum((px[..., 2] - m[:, None, 2]) * dg)
    hi, lo = mx.copy(), mn.copy()
    s = crg < 0
    hi[s, 0], lo[s, 0] = mn[s, 0], mx[s, 0]
    s = cbg < 0
    hi[s, 2], lo[s, 2] = mn[s, 2], mx[s, 2]
    ins = (hi - lo) / 16
    hi, lo = hi - ins, lo + ins
    c0 = _q5(hi[:, 0]) * 2048 + _q6(hi[:, 1]) * 32 + _q5(hi[:, 2])
    c1 = _q5(lo[:, 0]) * 2048 + _q6(lo[:, 1]) * 32 + _q5(lo[:, 2])
    nb = px.shape[0]
    sel = np.zeros((nb, 16), np.int64)
    active = np.ones(nb, bool)
    for it in range(2):
        sw = c0 < c1
        c0, c1 = np.where(sw, c1, c0), np.where(sw, c0, c1)

        def ex(c):
            r, g, b = np.floor(c / 2048), np.floor(c / 32) % 64, c % 32
            return np.stack([r * 8 + np.floor(r / 4), g * 4 + np.floor(g / 16), b * 8 + np.floor(b / 4)], -1)
        p0, p1 = ex(c0), ex(c1)
        d = p1 - p0
        dd = (d * d).sum(1)
        flat = (c0 == c1) | (dd == 0)
        sc = 3 / np.where(dd == 0, 1, dd)
        t = np.floor(((px - p0[:, None, :]) * d[:, None, :]).sum(-1) * sc[:, None] + 0.5)
        t = np.clip(t, 0, 3)
        ix = np.where(t == 0, 0, np.where(t == 3, 1, t + 1)).astype(np.int64)
        upd = active & ~flat
        sel[upd] = ix[upd]
        sel[active & flat] = 0
        active = active & ~flat
        if it == 1:
            break
        wt = 1 - t / 3
        wu = 1 - wt
        a11, a22, a12 = _seq_sum(wt * wt), _seq_sum(wu * wu), _seq_sum(wt * wu)
        x1 = _seq_sum(wt[..., None] * px)
        x2 = _seq_sum(wu[..., None] * px)
        det = a11 * a22 - a12 * a12
        good = active & (np.abs(det) > 1e-6)
        dsafe = np.where(good, det, 1.0)
        e0 = (a22[:, None] * x1 - a12[:, None] * x2) / dsafe[:, None]
        e1 = (a11[:, None] * x2 - a12[:, None] * x1) / dsafe[:, None]
        n0 = _q5(e0[:, 0]) * 2048 + _q6(e0[:, 1]) * 32 + _q5(e0[:, 2])
        n1 = _q5(e1[:, 0]) * 2048 + _q6(e1[:, 1]) * 32 + _q5(e1[:, 2])
        c0, c1 = np.where(good, n0, c0), np.where(good, n1, c1)
        active = good
    c0, c1 = c0.astype(np.int64), c1.astype(np.int64)
    out = np.zeros((nb, 8), np.uint8)
    out[:, 0], out[:, 1], out[:, 2], out[:, 3] = c0 & 255, c0 >> 8, c1 & 255, c1 >> 8
    pw = np.array([1, 4, 16, 64])
    for row in range(4):
        out[:, 4 + row] = (sel[:, 4 * row:4 * row + 4] * pw).sum(1)
    return out


def lua_alpha(pa):
    """pa (nb,16) float 0..255 -> (nb,8) uint8 DXT5 alpha blocks (8-level mode), as decoder.lua."""
    a0 = np.floor(pa.max(1) + 0.5).astype(np.int64)
    a1 = np.floor(pa.min(1) + 0.5).astype(np.int64)
    diff = np.where(a0 > a1, a0 - a1, 1)
    p = np.floor((a0[:, None] - pa) * (7 / diff)[:, None] + 0.5)
    ix = np.where(p <= 0, 0, np.where(p >= 7, 1, p + 1)).astype(np.int64)
    ix[a0 <= a1] = 0
    bits = (ix << (3 * np.arange(16))).sum(1)
    out = np.zeros((pa.shape[0], 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for k in range(6):
        out[:, 2 + k] = (bits >> (8 * k)) & 255
    return out


def blocks(img, w, h):
    """(h, w[, c]) -> (nblocks, 16, c) 4x4 blocks with edge clamp."""
    bh, bw = max(1, (h + 3) // 4), max(1, (w + 3) // 4)
    yy = np.minimum(np.arange(bh * 4), h - 1)
    xx = np.minimum(np.arange(bw * 4), w - 1)
    pad = img[yy][:, xx]
    c = pad.shape[2] if pad.ndim == 3 else 1
    return pad.reshape(bh, 4, bw, 4, c).swapaxes(1, 2).reshape(bh * bw, 16, c)


def down2(p, w, h):
    if w > 1 and h > 1:
        return 0.25 * (p[0::2, 0::2] + p[0::2, 1::2] + p[1::2, 0::2] + p[1::2, 1::2]), max(1, w // 2), max(1, h // 2)
    if h > 1:
        return 0.5 * (p[0::2] + p[1::2]), w, max(1, h // 2)
    return 0.5 * (p[:, 0::2] + p[:, 1::2]), max(1, w // 2), h


def lua_vtf(data: bytes, preview=False) -> bytes:
    """VTF bytes as decoder.lua builds them (up to float rounding)."""
    planes, mode, header, out_fmt, mips, (w, h) = decode_planes(data, preview)
    n = len(planes)
    has_alpha = n == (2 if mode == 1 else 3) + 1
    levels = []
    cw, ch = w, h
    for m in range(mips):
        if mode == 1:
            r = np.clip(planes[0] + 128, 0, 255)
            g = np.clip(planes[1] + 128, 0, 255)
            nx, ny = r / 127.5 - 1, g / 127.5 - 1
            nz = 1 - nx * nx - ny * ny
            b = np.where(nz > 0, np.sqrt(np.maximum(nz, 0)), 0) * 127.5 + 127.5
        else:
            yy = planes[0] + 128
            co, cg = planes[1], planes[2]
            t = yy - cg
            r, g, b = [np.clip(v, 0, 255) for v in (t + co, yy + cg, t - co)]
        if out_fmt in RAW_ORDER:
            a = np.clip(planes[-1] + 128, 0, 255) if has_alpha else np.full((ch, cw), 255.0)
            px = np.floor(np.stack([r, g, b, a, np.full_like(a, 255.0)], -1) + 0.5).astype(np.uint8)
            levels.append(np.ascontiguousarray(px[..., list(RAW_ORDER[out_fmt])]).tobytes())
        else:
            cb = lua_dxt1(blocks(np.stack([r, g, b], -1), cw, ch))
            if has_alpha:
                ab = lua_alpha(blocks(np.clip(planes[-1] + 128, 0, 255)[..., None], cw, ch)[..., 0])
                levels.append(np.concatenate([ab, cb], 1).tobytes())
            else:
                levels.append(cb.tobytes())
        if m < mips - 1:
            planes, cw, ch = _down_planes(planes, cw, ch)
    return bytes(header) + b"".join(reversed(levels))


def _down_planes(planes, cw, ch):
    nxt = []
    nw, nh = cw, ch
    for p in planes:
        q, nw, nh = down2(p, cw, ch)
        nxt.append(q)
    return nxt, nw, nh
