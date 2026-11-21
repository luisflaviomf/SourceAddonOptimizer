"""Métricas de qualidade de áudio do estudo de sons.

sdr / segsnr / lsd: as três do Adaptativo conservador, portadas de Systems/Audio/AudioQuality.cs (mesmas fórmulas,
mesmo tamanho de quadro, mesmos limites).
nmr: ruído sobre limiar de mascaramento (modelo de ouvido FFT do PEAQ básico, ITU-R BS.1387, simplificado): o
"ruído" é a diferença dos módulos dos espectros (não a diferença de forma de onda), agrupado em 109 bandas de 1/4
de Bark, contra o limiar de mascaramento calculado do original (espalhamento em frequência e no tempo, ruído
interno do ouvido, fundo de escala = 92 dB SPL). Abaixo de 0 dB o erro fica sob o limiar; quanto menor, melhor.
"""
import numpy as np

FRAME = 2048


def _align(x, n):
    if len(x) == n:
        return x
    out = np.zeros((n,) + x.shape[1:], dtype=x.dtype)
    m = min(n, len(x))
    out[:m] = x[:m]
    return out


def sdr(ref, cand):
    d = ref.astype(np.float64) - cand
    return 10.0 * np.log10((np.sum(ref.astype(np.float64) ** 2) + 1e-30) / (np.sum(d * d) + 1e-30))


def segsnr(ref, cand):
    r = ref.astype(np.float64)
    d = r - cand
    n = len(r)
    vals = []
    for s in range(0, n, FRAME):
        sig = np.sum(r[s:s + FRAME] ** 2)
        if sig < 1e-10:
            continue
        err = np.sum(d[s:s + FRAME] ** 2)
        vals.append(min(60.0, max(-10.0, 10.0 * np.log10((sig + 1e-30) / (err + 1e-30)))))
    return float(np.mean(vals)) if vals else sdr(ref, cand)


def _pow2_at_most(v):
    if v <= 0:
        return 0
    r = 1
    while r <= v // 2:
        r *= 2
    return r


def _spec_db(x, start, size):
    w = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(size) / (size - 1))
    seg = (x[start:start + size].astype(np.float32) * w.astype(np.float64)).astype(np.float32)
    mag = np.abs(np.fft.rfft(seg.astype(np.float64))) / size        # NAudio's forward FFT divides by N
    return 20.0 * np.log10(np.maximum(1e-7, mag))


def lsd(ref, cand):
    frames = len(ref)
    size = min(FRAME, _pow2_at_most(frames))
    if size < 64:
        return 0.0
    count = min(32, max(1, frames // size))
    last = max(0, frames - size)
    vals = []
    for o in range(count):
        start = 0 if count == 1 else int(round(o * last / (count - 1)))
        sq, n = 0.0, 0
        for ch in range(ref.shape[1]):
            a = _spec_db(ref[:, ch], start, size)
            b = _spec_db(cand[:, ch], start, size)
            keep = a >= a.max() - 60.0
            sq += float(np.sum((a[keep] - b[keep]) ** 2))
            n += int(keep.sum())
        if n:
            vals.append(np.sqrt(sq / n))
    return float(np.mean(vals)) if vals else 0.0


def product_scores(ref, cand):
    """(SDR dB, segmental SNR dB, LSD): ref and cand are float arrays [frames, channels] at the same rate."""
    cand = _align(cand, len(ref))
    return float(sdr(ref, cand)), float(segsnr(ref, cand)), float(lsd(ref, cand))


def no_worse(score, base):
    return score[0] >= base[0] and score[1] >= base[1] and score[2] <= base[2]


# ---------------------------------------------------------------- PEAQ-like noise-to-mask ratio
class EarModel:
    def __init__(self, fs=44100, n=2048, res=0.25):
        self.fs, self.n, self.hop = fs, n, n // 2
        self.win = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / (n - 1))
        self.scale = 10 ** (92 / 20.0) / (n / 4.0)          # full-scale sine -> 92 dB SPL
        f = np.fft.rfftfreq(n, 1.0 / fs)
        fk = np.maximum(f, 1.0) / 1000.0
        wdb = -0.6 * 3.64 * fk ** -0.8 + 6.5 * np.exp(-0.6 * (fk - 3.3) ** 2) - 1e-3 * fk ** 3.6
        self.ear = 10 ** (wdb / 20.0)
        bark = lambda hz: 7.0 * np.arcsinh(hz / 650.0)
        hz = lambda z: 650.0 * np.sinh(z / 7.0)
        zl, zu = bark(80.0), bark(min(18000.0, fs / 2.0))
        nb = int(np.ceil((zu - zl) / res))
        lo = hz(zl + res * np.arange(nb))
        hi = hz(np.minimum(zl + res * (np.arange(nb) + 1), zu))
        self.fc = hz(np.minimum(zl + res * (np.arange(nb) + 0.5), zu))
        self.nb = nb
        # fractional bin-to-band matrix
        df = fs / n
        edges_lo = (np.arange(len(f)) - 0.5) * df
        edges_hi = edges_lo + df
        g = np.zeros((nb, len(f)))
        for i in range(nb):
            ov = np.minimum(hi[i], edges_hi) - np.maximum(lo[i], edges_lo)
            g[i] = np.clip(ov, 0, None) / df
        self.group = g
        self.pthres = 10 ** (0.4 * 0.364 * (self.fc / 1000.0) ** -0.8) * (res / 0.25)
        k = np.arange(nb)
        mdb = np.where(k <= 12 / res, 3.0, 0.25 * k * res)
        self.gm = 10 ** (-mdb / 10.0)
        tau = 0.008 + (100.0 / self.fc) * (0.030 - 0.008)
        self.alpha = np.exp(-self.hop / (fs * tau))
        self.res = res
        # normalisation of the spreading with a flat unit pattern (level 0 dB)
        self.bs = self._spread_raw(np.ones((1, nb)))[0]

    def _spread_raw(self, pp):
        """pp [frames, bands] -> spread (power law 0.4), not normalised by bs."""
        nb, res = self.nb, self.res
        ldb = 10.0 * np.log10(np.maximum(pp, 1e-30))
        su = -24.0 - 230.0 / self.fc[None, :] + 0.2 * ldb            # dB/Bark, per source band (and frame)
        dist = (np.arange(nb)[:, None] - np.arange(nb)[None, :]) * res  # [target i, source j], in Bark
        out = np.zeros_like(pp)
        low = np.where(dist < 0, 10 ** (27.0 * dist / 10.0), 0.0)      # targets below the source: 27 dB/Bark
        up_mask = dist >= 0
        for start in range(0, len(pp), 256):
            p = pp[start:start + 256]
            s_up = np.where(up_mask[None], 10 ** (dist[None] * su[start:start + 256, None, :] / 10.0), 0.0)
            s = s_up + low[None]                                     # [frames, i, j]
            norm = s.sum(axis=1, keepdims=True)                      # sum over targets, per source
            contrib = (p[:, None, :] * s / norm) ** 0.4
            out[start:start + 256] = contrib.sum(axis=2) ** (1 / 0.4)
        return out

    def spectrum(self, x):
        """weighted magnitude spectra [frames, bins] of a mono float signal."""
        n, hop = self.n, self.hop
        if len(x) < n:
            x = np.concatenate([x, np.zeros(n - len(x), dtype=x.dtype)])
        count = 1 + (len(x) - n) // hop
        idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
        frames = x[idx] * self.win[None, :]
        return np.abs(np.fft.rfft(frames, axis=1)) * self.scale * self.ear[None, :]

    def mask(self, mag):
        pe = np.maximum((mag ** 2) @ self.group.T, 1e-12)
        pp = pe + self.pthres[None, :]
        es = self._spread_raw(pp) / self.bs[None, :]
        # time-domain spreading (forward masking)
        sm = np.empty_like(es)
        prev = np.zeros(self.nb)
        a = self.alpha
        for t in range(len(es)):
            prev = a * prev + (1 - a) * es[t]
            sm[t] = np.maximum(prev, es[t])
        return self.gm[None, :] * sm

    def nmr(self, ref_mag, mask, test_mag):
        m = min(len(ref_mag), len(test_mag))
        noise = ((ref_mag[:m] - test_mag[:m]) ** 2) @ self.group.T
        ratio = noise / mask[:m]
        return ratio.mean(axis=1)            # per frame, linear


_EAR = {}


def ear(fs=44100, n=2048, res=0.25):
    if (fs, n, res) not in _EAR:
        _EAR[(fs, n, res)] = EarModel(fs, n, res)
    return _EAR[(fs, n, res)]


class NmrReference:
    """Masking threshold of the original, computed once and reused for every candidate.
    short=True: quadros de 256 amostras (5,8 ms) e bandas de 1 Bark, com mascaramento só para a frente no tempo:
    acusa ruído que aparece ANTES de um ataque (pré-eco), que o quadro longo de 46 ms dilui."""

    def __init__(self, ref44, short=False):
        self.model = ear(44100, 256, 1.0) if short else ear(44100)
        self.mags = [self.model.spectrum(ref44[:, ch].astype(np.float64)) for ch in range(ref44.shape[1])]
        self.masks = [self.model.mask(m) for m in self.mags]
        self.frames = len(ref44)

    def score(self, cand44):
        cand44 = _align(cand44, self.frames)
        per = [self.model.nmr(self.mags[ch], self.masks[ch], self.model.spectrum(cand44[:, ch].astype(np.float64)))
               for ch in range(len(self.mags))]
        frame = np.mean(per, axis=0)
        total = 10.0 * np.log10(max(float(frame.mean()), 1e-12))
        p95 = 10.0 * np.log10(max(float(np.percentile(frame, 95)), 1e-12))
        over = float((frame > 1.0).mean())
        return total, p95, over


def band_energy(x, fs):
    """fraction of the energy above 5.5 kHz and above 11 kHz (mono mix)."""
    m = x.mean(axis=1).astype(np.float64)
    if len(m) < 64:
        return 0.0, 0.0
    spec = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1.0 / fs)
    tot = spec.sum() + 1e-30
    return float(spec[f >= 5512.5].sum() / tot), float(spec[f >= 11025].sum() / tot)


# ---------------------------------------------------------------- LSD exactly as the product computes it
# AudioQuality.cs uses NAudio's FastFourierTransform on single-precision complex numbers; the float32 round-off
# sets the floor of the quiet bins, so a double-precision FFT gives another LSD on some sounds. This is the same
# algorithm (bit reversal + butterflies in float32, forward transform divided by N).
from numba import njit as _njit


@_njit(cache=True)
def _naudio_fft_db(seg):
    n = len(seg)
    m = 0
    while (1 << m) < n:
        m += 1
    x = seg.copy()
    y = np.zeros(n, dtype=np.float32)
    i2 = n >> 1
    j = 0
    for i in range(n - 1):
        if i < j:
            tx = x[i]
            ty = y[i]
            x[i] = x[j]
            y[i] = y[j]
            x[j] = tx
            y[j] = ty
        k = i2
        while k <= j:
            j -= k
            k >>= 1
        j += k
    c1 = np.float32(-1.0)
    c2 = np.float32(0.0)
    l2 = 1
    for l in range(m):
        l1 = l2
        l2 <<= 1
        u1 = np.float32(1.0)
        u2 = np.float32(0.0)
        for j in range(l1):
            for i in range(j, n, l2):
                i1 = i + l1
                t1 = np.float32(u1 * x[i1] - u2 * y[i1])
                t2 = np.float32(u1 * y[i1] + u2 * x[i1])
                x[i1] = x[i] - t1
                y[i1] = y[i] - t2
                x[i] += t1
                y[i] += t2
            z = np.float32(u1 * c1 - u2 * c2)
            u2 = np.float32(u1 * c2 + u2 * c1)
            u1 = z
        c2 = np.float32(-np.sqrt(np.float64((np.float32(1.0) - c1) / np.float32(2.0))))
        c1 = np.float32(np.sqrt(np.float64((np.float32(1.0) + c1) / np.float32(2.0))))
    out = np.empty(n // 2 + 1, dtype=np.float64)
    fn = np.float32(n)
    for i in range(n // 2 + 1):
        a = np.float32(x[i] / fn)
        b = np.float32(y[i] / fn)
        mag = np.sqrt(np.float64(np.float32(a * a + b * b)))
        out[i] = 20.0 * np.log10(max(1e-7, mag))
    return out


def lsd32(ref, cand):
    """LogSpectralDistance of AudioQuality.cs, with the product's float32 FFT."""
    cand = _align(cand, len(ref))
    frames = len(ref)
    size = min(FRAME, _pow2_at_most(frames))
    if size < 64:
        return 0.0
    count = min(32, max(1, frames // size))
    last = max(0, frames - size)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(size) / (size - 1))
    vals = []
    for o in range(count):
        start = 0 if count == 1 else int(round(o * last / (count - 1)))
        sq, n = 0.0, 0
        for ch in range(ref.shape[1]):
            a = _naudio_fft_db((ref[start:start + size, ch].astype(np.float64) * hann).astype(np.float32))
            b = _naudio_fft_db((cand[start:start + size, ch].astype(np.float64) * hann).astype(np.float32))
            keep = a >= a.max() - 60.0
            sq += float(np.sum((a[keep] - b[keep]) ** 2))
            n += int(keep.sum())
        if n:
            vals.append(np.sqrt(sq / n))
    return float(np.mean(vals)) if vals else 0.0
