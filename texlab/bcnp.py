"""Vectorised numpy BC1/BC2/BC3 decoding and exact BC3 -> BC1 colour transcoding."""
from __future__ import annotations

import numpy as np


def _rgb565(c: np.ndarray):
    r = (c >> 11) & 31
    g = (c >> 5) & 63
    b = c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def decode_bc(data: bytes, w: int, h: int, fmt: int) -> np.ndarray:
    """fmt 13 DXT1, 20 DXT1 one-bit alpha, 14 DXT3, 15 DXT5 -> (h, w, 4) uint8."""
    bsize = 8 if fmt in (13, 20) else 16
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    nb = bw * bh
    blocks = np.frombuffer(data, dtype=np.uint8, count=nb * bsize).reshape(nb, bsize).astype(np.int64)
    cb = blocks[:, bsize - 8:]
    c0 = cb[:, 0] | (cb[:, 1] << 8)
    c1 = cb[:, 2] | (cb[:, 3] << 8)
    idx = cb[:, 4] | (cb[:, 5] << 8) | (cb[:, 6] << 16) | (cb[:, 7] << 24)
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    four = (c0 > c1) | (bsize == 16)
    pal = np.zeros((nb, 4, 4), np.int64)
    pal[:, 0] = np.stack([r0, g0, b0, np.full_like(r0, 255)], 1)
    pal[:, 1] = np.stack([r1, g1, b1, np.full_like(r0, 255)], 1)
    p2_four = np.stack([(2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, np.full_like(r0, 255)], 1)
    p3_four = np.stack([(r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, np.full_like(r0, 255)], 1)
    p2_three = np.stack([(r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, np.full_like(r0, 255)], 1)
    pal[:, 2] = np.where(four[:, None], p2_four, p2_three)
    pal[:, 3] = np.where(four[:, None], p3_four, 0)
    sel = (idx[:, None] >> (2 * np.arange(16))[None, :]) & 3
    px = pal[np.arange(nb)[:, None], sel]                       # (nb, 16, 4)
    if bsize == 8:
        if fmt == 13:
            px[..., 3] = np.where(px[..., 3] == 0, 0, 255)
    else:
        if fmt == 15:
            a0, a1 = blocks[:, 0], blocks[:, 1]
            bits = np.zeros(nb, np.int64)
            for k in range(6):
                bits |= blocks[:, 2 + k] << (8 * k)
            apal = np.zeros((nb, 8), np.int64)
            apal[:, 0], apal[:, 1] = a0, a1
            big = a0 > a1
            for k in range(1, 7):
                apal[:, k + 1] = np.where(big, ((7 - k) * a0 + k * a1) // 7, apal[:, k + 1])
            for k in range(1, 5):
                apal[:, k + 1] = np.where(big, apal[:, k + 1], ((5 - k) * a0 + k * a1) // 5)
            apal[:, 6] = np.where(big, apal[:, 6], 0)
            apal[:, 7] = np.where(big, apal[:, 7], 255)
            asel = (bits[:, None] >> (3 * np.arange(16))[None, :]) & 7
            px[..., 3] = apal[np.arange(nb)[:, None], asel]
        else:  # DXT3 explicit 4-bit alpha
            nib = np.zeros((nb, 16), np.int64)
            for i in range(16):
                nib[:, i] = (blocks[:, i // 2] >> (4 * (i % 2))) & 15
            px[..., 3] = nib * 17
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w].astype(np.uint8)


def bc3_to_bc1(data: bytes) -> bytes:
    """Exact: DXT5 colour blocks are always 4-colour; DXT1 needs c0 > c1 for that mode."""
    src = np.frombuffer(data, dtype=np.uint8).reshape(-1, 16)[:, 8:].astype(np.uint32)
    c0 = src[:, 0] | (src[:, 1] << 8)
    c1 = src[:, 2] | (src[:, 3] << 8)
    idx = src[:, 4] | (src[:, 5] << 8) | (src[:, 6] << 16) | (src[:, 7] << 24)
    swap = c0 < c1
    nc0 = np.where(swap, c1, c0)
    nc1 = np.where(swap, c0, c1)
    idx = np.where(swap, idx ^ np.uint32(0x55555555), idx)
    equal = c0 == c1
    hi = (idx >> np.uint32(1)) & np.uint32(0x55555555)
    lo = idx & np.uint32(0x55555555)
    three = hi & lo                                  # pairs equal to 3 (bit at even position)
    cleared = idx & ~(three | (three << np.uint32(1)))
    idx = np.where(equal, cleared, idx)
    out = np.zeros((len(src), 8), np.uint8)
    out[:, 0], out[:, 1] = nc0 & 0xFF, (nc0 >> 8) & 0xFF
    out[:, 2], out[:, 3] = nc1 & 0xFF, (nc1 >> 8) & 0xFF
    for k in range(4):
        out[:, 4 + k] = (idx >> np.uint32(8 * k)) & 0xFF
    return out.tobytes()
