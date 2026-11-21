"""MS-ADPCM de laboratório com ruído moldado (noise shaping), mesmo formato de arquivo.

O decodificador MS-ADPCM é fixo; o codificador escolhe os códigos. Aqui o erro de reconstrução r[n] é realimentado
no alvo de cada amostra: alvo[n] = x[n] - soma(w_k * r[n-k]), com w = coeficientes LPC do bloco com largura de banda
expandida (gamma). O ruído final fica R(z) = Q(z) / A(z/gamma): acompanha o espectro do som (fica embaixo dele,
mascarado) em vez de ser um chiado plano. gamma = 0 dá o codificador comum (adpcm.py).
Preditor por bloco e passo inicial como em adpcm.py; busca em feixe com o erro moldado como custo.
"""
import struct

import numpy as np
from numba import njit

from adpcm import ADAPT, COEF1, COEF2, _cdiv256

ORDER = 16


@njit(cache=True)
def _lpc(seg, order, wnc):
    """LPC (autocorrelation, Hann window, white-noise correction) -> a[0..order], a[0] = 1."""
    n = len(seg)
    w = np.empty(n)
    for i in range(n):
        w[i] = seg[i] * (0.5 - 0.5 * np.cos(2.0 * np.pi * i / (n - 1)))
    r = np.zeros(order + 1)
    for k in range(order + 1):
        s = 0.0
        for i in range(k, n):
            s += w[i] * w[i - k]
        r[k] = s
    a = np.zeros(order + 1)
    a[0] = 1.0
    if r[0] <= 1e-9:
        return a
    r[0] *= 1.0 + wnc
    for k in range(1, order + 1):
        r[k] *= np.exp(-0.5 * (2.0 * np.pi * 60.0 * k / 44100.0) ** 2)      # lag window
    err = r[0]
    tmp = np.zeros(order + 1)
    for i in range(1, order + 1):
        acc = r[i]
        for j in range(1, i):
            acc += a[j] * r[i - j]
        k = -acc / err
        for j in range(1, i):
            tmp[j] = a[j] + k * a[i - j]
        for j in range(1, i):
            a[j] = tmp[j]
        a[i] = k
        err *= 1.0 - k * k
        if err <= 1e-12:
            break
    return a


@njit(cache=True)
def _greedy_ns(x, c1, c2, delta, w, hist):
    """hist: last ORDER reconstruction errors before the block (most recent first). Returns cost, nibbles, delta,
    new hist."""
    n = len(x)
    P = len(w) - 1
    nib = np.zeros(n, dtype=np.int8)
    r = np.zeros(n + P)
    for k in range(P):
        r[P - 1 - k] = hist[k]
    s2 = x[0]
    s1 = x[1]
    cost = 0.0
    for i in range(2, n):
        fb = 0.0
        for k in range(1, P + 1):
            fb += w[k] * r[P + i - k]
        target = x[i] - fb
        pred = _cdiv256(s1 * c1 + s2 * c2)
        q = int(np.floor((target - pred) / delta + 0.5))
        if q > 7:
            q = 7
        elif q < -8:
            q = -8
        v = pred + q * delta
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        e = v - target
        cost += e * e
        r[P + i] = v - x[i]
        nib[i] = q
        s2 = s1
        s1 = v
        delta = (ADAPT[q & 15] * delta) >> 8
        if delta < 16:
            delta = 16
    out_hist = np.empty(P)
    for k in range(P):
        out_hist[k] = r[P + n - 1 - k]
    return cost, nib, delta, out_hist


@njit(cache=True)
def _beam_ns(x, c1, c2, delta0, w, hist, K):
    n = len(x)
    P = len(w) - 1
    C = 3
    s1 = np.empty(K, dtype=np.int64)
    s2 = np.empty(K, dtype=np.int64)
    dl = np.empty(K, dtype=np.int64)
    er = np.empty(K)
    hs = np.zeros((K, P))                 # per path: last P errors, most recent first
    alive = 1
    s1[0] = x[1]
    s2[0] = x[0]
    dl[0] = delta0
    er[0] = 0.0
    for k in range(P):
        hs[0, k] = hist[k]
    parent = np.zeros((n, K), dtype=np.int16)
    choice = np.zeros((n, K), dtype=np.int8)
    cs1 = np.empty(K * C, dtype=np.int64)
    cdl = np.empty(K * C, dtype=np.int64)
    cer = np.empty(K * C)
    cpa = np.empty(K * C, dtype=np.int16)
    cch = np.empty(K * C, dtype=np.int8)
    crr = np.empty(K * C)
    nh = np.zeros((K, P))
    ns1 = np.empty(K, dtype=np.int64)
    ns2 = np.empty(K, dtype=np.int64)
    ndl = np.empty(K, dtype=np.int64)
    ner = np.empty(K)
    for i in range(2, n):
        m = 0
        for k in range(alive):
            fb = 0.0
            for j in range(P):
                fb += w[j + 1] * hs[k, j]
            target = x[i] - fb
            pred = _cdiv256(s1[k] * c1 + s2[k] * c2)
            q0 = int(np.floor((target - pred) / dl[k] + 0.5))
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
                e = v - target
                nd = (ADAPT[q & 15] * dl[k]) >> 8
                if nd < 16:
                    nd = 16
                cs1[m] = v
                cdl[m] = nd
                cer[m] = er[k] + e * e
                cpa[m] = k
                cch[m] = q
                crr[m] = v - x[i]
                m += 1
        order = np.argsort(cer[:m])
        alive = min(K, m)
        for k in range(alive):
            j = order[k]
            p = cpa[j]
            ns1[k] = cs1[j]
            ns2[k] = s1[p]
            ndl[k] = cdl[j]
            ner[k] = cer[j]
            nh[k, 0] = crr[j]
            for t in range(1, P):
                nh[k, t] = hs[p, t - 1]
            parent[i, k] = p
            choice[i, k] = cch[j]
        for k in range(alive):
            s1[k] = ns1[k]
            s2[k] = ns2[k]
            dl[k] = ndl[k]
            er[k] = ner[k]
            for t in range(P):
                hs[k, t] = nh[k, t]
    nib = np.zeros(n, dtype=np.int8)
    k = 0
    for i in range(n - 1, 1, -1):
        nib[i] = choice[i, k]
        k = parent[i, k]
    return er[0], nib, dl[0], hs[0].copy()


@njit(cache=True)
def _encode_ns(pcm, channels, align, K, gamma, wnc):
    spb = 2 + (align - 7 * channels) * 2 // channels
    frames = pcm.shape[0]
    blocks = (frames + spb - 1) // spb
    out = np.zeros(blocks * align, dtype=np.uint8)
    carried = np.full(channels, 16, dtype=np.int64)
    hists = np.zeros((channels, ORDER))
    x = np.empty(spb, dtype=np.int64)
    nibs = np.zeros((channels, spb), dtype=np.int8)
    seg = np.empty(2 * spb)
    w = np.zeros(ORDER + 1)
    for b in range(blocks):
        base = b * align
        for ch in range(channels):
            for i in range(spb):
                f = b * spb + i
                if f >= frames:
                    f = frames - 1
                x[i] = pcm[f, ch]
            # analysis window: the block with half a block on each side
            for i in range(2 * spb):
                f = b * spb - spb // 2 + i
                if f < 0:
                    f = 0
                elif f >= frames:
                    f = frames - 1
                seg[i] = pcm[f, ch]
            a = _lpc(seg, ORDER, wnc)
            g = 1.0
            for k in range(ORDER + 1):
                w[k] = a[k] * g
                g *= gamma
            best_cost = 1e300
            best_p = 0
            best_d0 = 16
            best_nib = np.zeros(spb, dtype=np.int8)
            best_dend = 16
            best_hist = hists[ch].copy()
            for p in range(7):
                c1 = COEF1[p]
                c2 = COEF2[p]
                m = min(spb, 34)
                acc = 0.0
                for i in range(2, m):
                    acc += abs(x[i] - _cdiv256(x[i - 1] * c1 + x[i - 2] * c2))
                mean_abs = acc / max(1, m - 2)
                for ci in range(4):
                    if ci == 0:
                        d0 = carried[ch]
                    elif ci == 1:
                        d0 = max(16, int(mean_abs / 2.0))
                    elif ci == 2:
                        d0 = max(16, int(mean_abs / 4.0))
                    else:
                        d0 = max(16, int(mean_abs))
                    if d0 > 32767:
                        d0 = 32767
                    cost, nib, dend, hist = _greedy_ns(x, c1, c2, d0, w, hists[ch])
                    if cost < best_cost:
                        best_cost = cost
                        best_p = p
                        best_d0 = d0
                        best_nib = nib
                        best_dend = dend
                        best_hist = hist
            if K > 1:
                cost, nib, dend, hist = _beam_ns(x, COEF1[best_p], COEF2[best_p], best_d0, w, hists[ch], K)
                if cost < best_cost:
                    best_nib = nib
                    best_dend = dend
                    best_hist = hist
            carried[ch] = best_dend
            hists[ch] = best_hist
            nibs[ch] = best_nib
            out[base + ch] = best_p
            o = base + channels + 2 * ch
            out[o] = best_d0 & 255
            out[o + 1] = (best_d0 >> 8) & 255
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
    return out


def encode_wav(pcm, rate, gamma=0.9, beam=8, wnc=1e-3, align=None):
    pcm = np.ascontiguousarray(pcm, dtype=np.int16)
    channels = pcm.shape[1]
    if align is None:
        align = 1024 if channels == 1 else 2048
    spb = 2 + (align - 7 * channels) * 2 // channels
    data = _encode_ns(pcm, channels, align, beam, gamma, wnc).tobytes()
    fmt = struct.pack("<HHIIHHHHH", 2, channels, rate, rate * align // spb, align, 4, 32, spb, 7)
    fmt += b"".join(struct.pack("<hh", int(a), int(b)) for a, b in zip(COEF1, COEF2))
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"fact" + struct.pack("<II", 4, pcm.shape[0])
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body
