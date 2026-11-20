"""Lab helpers (review/claude): read GMOT v2 blobs into their integer bands, exactly as stored."""
import lzma
import struct

import numpy as np

TILE = 8


def unlzma(c):
    d = bytearray(c)
    d[5:13] = b"\xff" * 8
    return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)


def lzma_chunk(body, preset=9, pb=0, lc=3, lp=0):
    size = 1 << 12
    while size < len(body):
        size <<= 1
    return len(lzma.compress(bytes(body), format=lzma.FORMAT_ALONE,
                             filters=[{"id": lzma.FILTER_LZMA1, "preset": preset, "pb": pb, "lc": lc, "lp": lp,
                                       "dict_size": min(size, 1 << 24)}]))


def _band(buf, pos, h, w):
    """-> (q int32 (h,w) or None for an all-zero band, noise codes (th,tw) or None, new pos, kind)"""
    t = buf[pos]
    pos += 1
    codes = None
    if t & 16:
        th, tw = max(1, h // TILE), max(1, w // TILE)
        codes = np.frombuffer(buf, np.uint8, th * tw, pos).reshape(th, tw).copy()
        pos += th * tw
        t &= 15
    n = h * w
    if t == 0:
        return np.zeros((h, w), np.int32), codes, pos, 0
    if t == 1:
        return np.frombuffer(buf, np.int8, n, pos).reshape(h, w).astype(np.int32), codes, pos + n, 1
    return np.frombuffer(buf, "<i2", n, pos).reshape(h, w).astype(np.int32), codes, pos + 2 * n, 2


def parse(data):
    """GMOT bytes -> dict(w, h, L, n, mode, cdrop, P, steps, header (VTF bytes), out_fmt, mips, sizes (chunk
    compressed sizes), bands: list of dict(plane, lvl, k (0 LL, 1 LH, 2 HL, 3 HH), q, codes, chunk, kind))."""
    assert data[:4] == b"GMOT" and data[4] == 2
    nch = data[5]
    sizes = struct.unpack_from("<%dI" % nch, data, 6)
    offs = np.cumsum([6 + 4 * nch] + list(sizes))
    body = unlzma(data[offs[0]:offs[0] + sizes[0]])
    w, h, L, n, mode, cdrop, P = struct.unpack_from("<HHBBBBB", body, 0)
    pos = 9
    steps = struct.unpack_from("<%df" % n, body, pos)
    pos += 4 * n
    hl = struct.unpack_from("<I", body, pos)[0]
    pos += 4
    header = body[pos:pos + hl]
    pos += hl
    out_fmt, mips = struct.unpack_from("<BB", body, pos)
    pos += 2
    bands = []
    for pi in range(n):
        q, codes, pos, kind = _band(body, pos, h >> L, w >> L)
        bands.append(dict(plane=pi, lvl=L, k=0, q=q, codes=codes, chunk=0, kind=kind))
        for lvl in range(L, P, -1):
            for k in range(3):
                q, codes, pos, kind = _band(body, pos, h >> lvl, w >> lvl)
                bands.append(dict(plane=pi, lvl=lvl, k=k + 1, q=q, codes=codes, chunk=0, kind=kind))
    assert pos == len(body)
    c = 1
    for lvl in range(P, 0, -1):
        for pi in range(n):
            for k in range(3):
                raw = unlzma(data[offs[c]:offs[c] + sizes[c]])
                q, codes, p2, kind = _band(raw, 0, h >> lvl, w >> lvl)
                assert p2 == len(raw) or (kind == 0 and p2 == 1)
                bands.append(dict(plane=pi, lvl=lvl, k=k + 1, q=q, codes=codes, chunk=c, kind=kind))
                c += 1
    return dict(w=w, h=h, L=L, n=n, mode=mode, cdrop=cdrop, P=P, steps=steps, header=header, out_fmt=out_fmt,
                mips=mips, sizes=sizes, bands=bands, head_raw=len(body))


def band_bytes(b):
    """the band's bytes as codec.put_band / put_band_nf wrote them"""
    q, codes, kind = b["q"], b["codes"], b["kind"]
    if codes is None:
        if kind == 0:
            return bytes([0])
        return bytes([kind]) + (q.astype(np.int8).tobytes() if kind == 1 else q.astype("<i2").tobytes())
    head = bytes([kind | 16]) + codes.astype(np.uint8).tobytes()
    return head + (q.astype(np.int8).tobytes() if kind == 1 else q.astype("<i2").tobytes())
