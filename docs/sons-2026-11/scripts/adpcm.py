"""MS-ADPCM de laboratório: o mesmo formato que o produto já grava (WAVE_FORMAT_ADPCM, 7 preditores padrão, bloco de
1024 bytes mono / 2048 estéreo), com um codificador melhor que o do ffmpeg:
- escolhe o melhor dos 7 preditores em cada bloco (o ffmpeg usa sempre o preditor 0),
- escolhe o passo inicial do bloco entre o herdado e estimativas do resíduo,
- busca em feixe (K caminhos) em vez da escolha amostra a amostra.
O arquivo é lido por qualquer decodificador MS-ADPCM (ffmpeg, motor Source): nada muda no formato.
"""
import struct

import numpy as np
from numba import njit

COEF1 = np.array([256, 512, 0, 192, 240, 460, 392], dtype=np.int64)
COEF2 = np.array([0, -256, 0, 64, 0, -208, -232], dtype=np.int64)
ADAPT = np.array([230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230], dtype=np.int64)


@njit(cache=True)
def _cdiv256(v):
    # C division, truncating toward zero (what ffmpeg's decoder does)
    return -((-v) >> 8) if v < 0 else v >> 8


@njit(cache=True)
def _greedy(x, c1, c2, delta):
    """x: int64 samples of one channel of one block (first two stored verbatim). Returns (error, nibbles)."""
    n = len(x)
    nib = np.zeros(n, dtype=np.int8)
    s2 = x[0]
    s1 = x[1]
    err = 0.0
    for i in range(2, n):
        pred = _cdiv256(s1 * c1 + s2 * c2)
        d = x[i] - pred
        q = int(np.floor(d / delta + 0.5))
        if q > 7:
            q = 7
        elif q < -8:
            q = -8
        v = pred + q * delta
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        e = x[i] - v
        err += e * e
        nib[i] = q
        s2 = s1
        s1 = v
        delta = (ADAPT[q & 15] * delta) >> 8
        if delta < 16:
            delta = 16
    return err, nib, delta


@njit(cache=True)
def _beam(x, c1, c2, delta0, K):
    """Beam search over the nibbles: K survivors, 3 nibbles tried around the rounded one."""
    n = len(x)
    s1 = np.empty(K, dtype=np.int64)
    s2 = np.empty(K, dtype=np.int64)
    dl = np.empty(K, dtype=np.int64)
    er = np.empty(K, dtype=np.float64)
    alive = 1
    s1[0] = x[1]
    s2[0] = x[0]
    dl[0] = delta0
    er[0] = 0.0
    parent = np.zeros((n, K), dtype=np.int16)
    choice = np.zeros((n, K), dtype=np.int8)
    C = 3
    cs1 = np.empty(K * C, dtype=np.int64)
    cs2 = np.empty(K * C, dtype=np.int64)
    cdl = np.empty(K * C, dtype=np.int64)
    cer = np.empty(K * C, dtype=np.float64)
    cpa = np.empty(K * C, dtype=np.int16)
    cch = np.empty(K * C, dtype=np.int8)
    for i in range(2, n):
        m = 0
        for k in range(alive):
            pred = _cdiv256(s1[k] * c1 + s2[k] * c2)
            d = x[i] - pred
            q0 = int(np.floor(d / dl[k] + 0.5))
            for dq in range(-1, 2):
                q = q0 + dq
                if q > 7 or q < -8:
                    if dq != 0:
                        continue
                    q = 7 if q > 7 else -8
                v = pred + q * dl[k]
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                e = x[i] - v
                nd = (ADAPT[q & 15] * dl[k]) >> 8
                if nd < 16:
                    nd = 16
                cs1[m] = v
                cs2[m] = s1[k]
                cdl[m] = nd
                cer[m] = er[k] + e * e
                cpa[m] = k
                cch[m] = q
                m += 1
        order = np.argsort(cer[:m])
        alive = min(K, m)
        for k in range(alive):
            j = order[k]
            s1[k] = cs1[j]
            s2[k] = cs2[j]
            dl[k] = cdl[j]
            er[k] = cer[j]
            parent[i, k] = cpa[j]
            choice[i, k] = cch[j]
    nib = np.zeros(n, dtype=np.int8)
    k = 0
    best_delta = dl[0]
    best_err = er[0]
    for i in range(n - 1, 1, -1):
        nib[i] = choice[i, k]
        k = parent[i, k]
    return best_err, nib, best_delta


@njit(cache=True)
def _encode_channel_block(x, carried_delta, K, fixed_predictor):
    """Best (predictor, initial delta, nibbles) for one channel of one block."""
    n = len(x)
    best_err = 1e300
    best_p = 0
    best_d0 = 16
    best_nib = np.zeros(n, dtype=np.int8)
    best_dend = 16
    p_lo, p_hi = 0, 7
    if fixed_predictor >= 0:
        p_lo, p_hi = fixed_predictor, fixed_predictor + 1
    for p in range(p_lo, p_hi):
        c1 = COEF1[p]
        c2 = COEF2[p]
        # residual scale over the first samples of the block -> candidate initial deltas
        m = min(n, 34)
        acc = 0.0
        for i in range(2, m):
            pred = _cdiv256(x[i - 1] * c1 + x[i - 2] * c2)
            acc += abs(x[i] - pred)
        mean_abs = acc / max(1, m - 2)
        cands = np.empty(4, dtype=np.int64)
        cands[0] = carried_delta
        cands[1] = max(16, int(mean_abs / 2.0))
        cands[2] = max(16, int(mean_abs / 4.0))
        cands[3] = max(16, int(mean_abs))
        ncand = 4 if fixed_predictor < 0 else 1
        for ci in range(ncand):
            d0 = cands[ci]
            if d0 > 32767:
                d0 = 32767
            err, nib, dend = _greedy(x, c1, c2, d0)
            if err < best_err:
                best_err = err
                best_p = p
                best_d0 = d0
                best_nib = nib
                best_dend = dend
    if K > 1:
        err, nib, dend = _beam(x, COEF1[best_p], COEF2[best_p], best_d0, K)
        if err < best_err:
            best_err = err
            best_nib = nib
            best_dend = dend
    return best_p, best_d0, best_nib, best_dend, best_err


@njit(cache=True)
def _encode(pcm, channels, align, K, fixed_predictor):
    """pcm int16 [frames, channels] -> block bytes (uint8), with the last block padded with its last sample."""
    spb = 2 + (align - 7 * channels) * 2 // channels
    frames = pcm.shape[0]
    blocks = (frames + spb - 1) // spb
    out = np.zeros(blocks * align, dtype=np.uint8)
    carried = np.full(channels, 16, dtype=np.int64)
    x = np.empty(spb, dtype=np.int64)
    nibs = np.zeros((channels, spb), dtype=np.int8)
    total_err = 0.0
    for b in range(blocks):
        base = b * align
        for ch in range(channels):
            for i in range(spb):
                f = b * spb + i
                if f >= frames:
                    f = frames - 1
                x[i] = pcm[f, ch]
            p, d0, nib, dend, err = _encode_channel_block(x, carried[ch], K, fixed_predictor)
            carried[ch] = dend
            total_err += err
            nibs[ch] = nib
            out[base + ch] = p
            o = base + channels + 2 * ch
            out[o] = d0 & 255
            out[o + 1] = (d0 >> 8) & 255
            o = base + 3 * channels + 2 * ch
            v = x[1] & 0xFFFF
            out[o] = v & 255
            out[o + 1] = v >> 8
            o = base + 5 * channels + 2 * ch
            v = x[0] & 0xFFFF
            out[o] = v & 255
            out[o + 1] = v >> 8
        pos = base + 7 * channels
        if channels == 1:
            for i in range(2, spb, 2):
                out[pos] = ((nibs[0, i] & 15) << 4) | (nibs[0, i + 1] & 15)
                pos += 1
        else:
            for i in range(2, spb):
                out[pos] = ((nibs[0, i] & 15) << 4) | (nibs[1, i] & 15)
                pos += 1
    return out, total_err


def encode_wav(pcm, rate, align=None, beam=4, fixed_predictor=-1):
    """pcm int16 [frames, channels] -> bytes of a RIFF/WAVE MS-ADPCM file (fmt 50 bytes, fact, data)."""
    pcm = np.ascontiguousarray(pcm, dtype=np.int16)
    channels = pcm.shape[1]
    if align is None:
        align = 1024 if channels == 1 else 2048
    spb = 2 + (align - 7 * channels) * 2 // channels
    data, _ = _encode(pcm, channels, align, beam, fixed_predictor)
    data = data.tobytes()
    fmt = struct.pack("<HHIIHHHHH", 2, channels, rate, rate * align // spb, align, 4, 32, spb, 7)
    fmt += b"".join(struct.pack("<hh", int(a), int(b)) for a, b in zip(COEF1, COEF2))
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"fact" + struct.pack("<II", 4, pcm.shape[0])
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@njit(cache=True)
def _decode(data, channels, align, frames):
    spb = 2 + (align - 7 * channels) * 2 // channels
    blocks = len(data) // align
    out = np.zeros((blocks * spb, channels), dtype=np.int16)
    for b in range(blocks):
        base = b * align
        for ch in range(channels):
            p = np.int64(data[base + ch])
            c1 = COEF1[p]
            c2 = COEF2[p]
            o = base + channels + 2 * ch
            delta = np.int64(data[o]) | (np.int64(data[o + 1]) << 8)
            if delta >= 32768:
                delta -= 65536
            o = base + 3 * channels + 2 * ch
            s1 = np.int64(data[o]) | (np.int64(data[o + 1]) << 8)
            if s1 >= 32768:
                s1 -= 65536
            o = base + 5 * channels + 2 * ch
            s2 = np.int64(data[o]) | (np.int64(data[o + 1]) << 8)
            if s2 >= 32768:
                s2 -= 65536
            out[b * spb, ch] = s2
            out[b * spb + 1, ch] = s1
            for i in range(2, spb):
                if channels == 1:
                    byte = np.int64(data[base + 7 + (i - 2) // 2])
                    q = (byte >> 4) if (i - 2) % 2 == 0 else (byte & 15)
                else:
                    byte = np.int64(data[base + 14 + (i - 2)])
                    q = (byte >> 4) if ch == 0 else (byte & 15)
                sq = q - 16 if q >= 8 else q
                pred = _cdiv256(s1 * c1 + s2 * c2)
                v = pred + sq * delta
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                out[b * spb + i, ch] = v
                s2 = s1
                s1 = v
                delta = (ADAPT[q] * delta) >> 8
                if delta < 16:
                    delta = 16
    return out[:frames]


def decode_wav(blob):
    """bytes of an MS-ADPCM WAV written by encode_wav (or ffmpeg) -> int16 [frames, channels], rate."""
    pos = 12
    fmt = fact = data = None
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        if cid == b"fmt ":
            fmt = blob[pos + 8:pos + 8 + size]
        elif cid == b"fact":
            fact = struct.unpack_from("<I", blob, pos + 8)[0]
        elif cid == b"data":
            data = blob[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    tag, channels, rate, _, align, _ = struct.unpack_from("<HHIIHH", fmt, 0)
    assert tag == 2
    spb = 2 + (align - 7 * channels) * 2 // channels
    frames = fact if fact is not None else len(data) // align * spb
    arr = np.frombuffer(data, dtype=np.uint8)
    return _decode(arr, channels, align, min(frames, len(data) // align * spb)), rate
