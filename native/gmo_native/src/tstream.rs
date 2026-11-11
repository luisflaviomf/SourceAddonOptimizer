//! Texture stream encoder (`texture_stream/choose.py`, `texture_stream/codec.py`): the wavelet transform
//! of a plane, its reconstruction at a quantization step, the squared error against the source and its
//! mean, the bytes of a quantized band, the colour planes. All bit-identical to the numpy code they
//! stand for (`codec.dwt`, `choose._recon`, `codec.psnr`, `codec.put_band`...), which stays as the
//! reference and the fallback; the mean follows numpy's pairwise summation (`pairwise_sum` below).
//!
//! Every operation is the numpy one in the same order: separate multiply and add (Rust never
//! fuses them), `np.round` = round half to even, a Python `int` step becomes an f64 before the
//! division.

const S2: f64 = std::f64::consts::SQRT_2; // float(np.sqrt(2.0))

/// One output row of `codec.inv53` along the last axis: `s`, `d` hold n values, `o` 2n; `even` is a
/// scratch row of n values (the even outputs, kept apart so both loops are plain element-wise ones).
#[inline]
fn inv53_row(s: &[f64], d: &[f64], even: &mut [f64], o: &mut [f64]) {
    let n = s.len();
    let (d, even, o) = (&d[..n], &mut even[..n], &mut o[..2 * n]);
    let a = 1.0 / S2;
    let b = 0.25 * S2;
    // s' = s * _A - _B * (d before + d); the first element's "before" is itself
    even[0] = s[0] * a - b * (d[0] + d[0]);
    for i in 1..n {
        even[i] = s[i] * a - b * (d[i - 1] + d[i]);
    }
    // d' = d * S2 + 0.5 * (s' + s' after); the last element's "after" is itself
    for i in 0..n - 1 {
        o[2 * i] = even[i];
        o[2 * i + 1] = d[i] * S2 + 0.5 * (even[i] + even[i + 1]);
    }
    o[2 * n - 2] = even[n - 1];
    o[2 * n - 1] = d[n - 1] * S2 + 0.5 * (even[n - 1] + even[n - 1]);
}

/// `codec.quant(c, st)` as the integer numpy stores: sign(c) * floor(|c| / st + 0.4). The value under the
/// floor is never negative, so the truncating conversion is the floor (one instruction, where `floor` is a
/// library call on the baseline x86-64 this crate is built for).
#[inline]
fn quant(c: f64, st: f64) -> i64 {
    let m = (c.abs() / st + 0.4) as i64;
    // -m for a negative coefficient, without a branch (the sign of a coefficient is a coin toss)
    let negative = (c < 0.0) as i64;
    (m ^ -negative) + negative
}

/// `codec.quant(c, st) * st` as numpy leaves it for the float code that follows: an integer in an f64.
#[inline]
fn dequant(c: f64, st: f64, step: i64) -> f64 {
    (quant(c, st) * step) as f64
}

/// The row buffers of one level of `recon`, all as wide as the level's input: the dequantized rows of the
/// bands the vertical step still needs (two of LH and HH, one of HL) and the rows it produces.
struct Rows {
    lh: [Vec<f64>; 2],
    hh: [Vec<f64>; 2],
    hl: Vec<f64>,
    lo_s: [Vec<f64>; 2],
    hi_s: [Vec<f64>; 2],
    lo_d: Vec<f64>,
    hi_d: Vec<f64>,
    even: Vec<f64>,
}

impl Rows {
    fn new(w: usize) -> Rows {
        let v = || vec![0f64; w];
        Rows {
            lh: [v(), v()],
            hh: [v(), v()],
            hl: v(),
            lo_s: [v(), v()],
            hi_s: [v(), v()],
            lo_d: v(),
            hi_d: v(),
            even: v(),
        }
    }

    /// The second half of the vertical step for row `i` (d' = d * S2 + 0.5 * (s' + s' of the row below))
    /// and the horizontal step of the two output rows it completes. `at`, `below`: which buffers hold row
    /// `i` and the row under it.
    fn finish(&mut self, at: usize, below: usize, i: usize, out: &mut [f64]) {
        let w = self.hl.len();
        {
            let (d, s, sb) = (&self.lh[at][..w], &self.lo_s[at][..w], &self.lo_s[below][..w]);
            let o = &mut self.lo_d[..w];
            for c in 0..w {
                o[c] = d[c] * S2 + 0.5 * (s[c] + sb[c]);
            }
            let (d, s, sb) = (&self.hh[at][..w], &self.hi_s[at][..w], &self.hi_s[below][..w]);
            let o = &mut self.hi_d[..w];
            for c in 0..w {
                o[c] = d[c] * S2 + 0.5 * (s[c] + sb[c]);
            }
        }
        let w2 = 2 * w;
        inv53_row(&self.lo_s[at], &self.hi_s[at], &mut self.even, &mut out[2 * i * w2..][..w2]);
        inv53_row(&self.lo_d, &self.hi_d, &mut self.even, &mut out[(2 * i + 1) * w2..][..w2]);
    }
}

/// One level of `codec.idwt`: `ll` (ch x cw) and the bands LH, HL, HH (`bands`, ch * cw each, None for a
/// level taken as zero) to `out` (2ch x 2cw). `codec.inv53` runs along the first axis for (LL, LH) and
/// (HL, HH) and then along the last axis for the two results; here both steps go row by row over buffers
/// that stay in the cache, each value by the same expression as the array-at-a-time code, so the result
/// is the same to the last bit.
fn level_up(ll: &[f64], bands: Option<&[f64]>, ch: usize, cw: usize, st: f64, step: i64, out: &mut [f64]) {
    let a = 1.0 / S2;
    let b = 0.25 * S2;
    let n = ch * cw;
    let mut r = Rows::new(cw);
    // which buffers hold row i ("cur") and row i - 1 ("prev")
    let (mut cur, mut prev) = (0usize, 1usize);
    for i in 0..ch {
        if let Some(bands) = bands {
            for (dst, k) in [(&mut r.lh[cur], 0usize), (&mut r.hl, 1), (&mut r.hh[cur], 2)] {
                let src = &bands[k * n + i * cw..][..cw];
                let dst = &mut dst[..cw];
                for c in 0..cw {
                    dst[c] = dequant(src[c], st, step);
                }
            }
        }
        // first half of the vertical step: s' = s * _A - _B * (d of the row above + d); the first row's
        // "above" is itself
        let above = if i == 0 { cur } else { prev };
        {
            let s = &ll[i * cw..][..cw];
            let (dp, d) = (&r.lh[above][..cw], &r.lh[cur][..cw]);
            let o = &mut r.lo_s[cur][..cw];
            for c in 0..cw {
                o[c] = s[c] * a - b * (dp[c] + d[c]);
            }
            let s = &r.hl[..cw];
            let (dp, d) = (&r.hh[above][..cw], &r.hh[cur][..cw]);
            let o = &mut r.hi_s[cur][..cw];
            for c in 0..cw {
                o[c] = s[c] * a - b * (dp[c] + d[c]);
            }
        }
        if i > 0 {
            // row i - 1 is complete now that the s' of row i is known
            r.finish(prev, cur, i - 1, out);
        }
        std::mem::swap(&mut cur, &mut prev);
    }
    // the last row pairs with itself
    r.finish(prev, prev, ch - 1, out);
}

/// `choose._recon(ll, bands, step, drop, levels)` for a plane of `h` x `w`.
///
/// `coef` holds LL (h >> levels x w >> levels) and then, coarse to fine, LH, HL and HH of every
/// level, each row-major. The `drop` finest levels are taken as zero.
pub fn recon(coef: &[f64], h: usize, w: usize, levels: usize, step: i64, drop: usize, out: &mut [f64]) {
    let st = step as f64;
    let (mut ch, mut cw) = (h >> levels, w >> levels);
    let mut pos = ch * cw;
    // np.round(ll / st) * st
    let mut ll: Vec<f64> = coef[..pos].iter().map(|&x| (x / st).round_ties_even() * st).collect();
    for level in 0..levels {
        let n = ch * cw;
        let bands = if level + drop >= levels { None } else { Some(&coef[pos..pos + 3 * n]) };
        pos += 3 * n;
        if level + 1 == levels {
            level_up(&ll, bands, ch, cw, st, step, out);
        } else {
            let mut next = vec![0f64; 4 * n];
            level_up(&ll, bands, ch, cw, st, step, &mut next);
            ll = next;
        }
        ch *= 2;
        cw *= 2;
    }
    if levels == 0 {
        out.copy_from_slice(&ll);
    }
}

/// `np.round` (half to even) for the squared errors below. Adding and taking away 1.5 * 2^52 rounds to
/// the nearest integer, ties to even, in the default rounding mode: two additions where `round_ties_even`
/// is a library call on baseline x86-64. A value in [-0.5, -0.0] comes out as +0.0 instead of -0.0, which
/// the subtraction and the square that follow do not see.
#[inline]
fn round_even(v: f64) -> f64 {
    const MAGIC: f64 = 6755399441055744.0;
    if v.abs() < 2251799813685248.0 {
        (v + MAGIC) - MAGIC
    } else {
        v.round_ties_even()
    }
}

#[inline]
fn clip255(v: f64) -> f64 {
    // np.clip(x, 0, 255)
    if v < 0.0 {
        0.0
    } else if v > 255.0 {
        255.0
    } else {
        v
    }
}

/// `(np.clip(np.round(codec.ycocg_to_rgb(y, co, cg)), 0, 255) - reference) ** 2`, interleaved RGB.
pub fn sqerr_ycocg(y: &[f64], co: &[f64], cg: &[f64], reference: &[f64], out: &mut [f64]) {
    for i in 0..y.len() {
        let yy = y[i] + 128.0;
        let t = yy - cg[i];
        let rgb = [t + co[i], yy + cg[i], t - co[i]];
        for k in 0..3 {
            let d = clip255(round_even(rgb[k])) - reference[3 * i + k];
            out[3 * i + k] = d * d;
        }
    }
}

/// `(np.clip(np.round(plane + 128), 0, 255) - reference) ** 2` (the alpha plane).
pub fn sqerr_plane(plane: &[f64], reference: &[f64], out: &mut [f64]) {
    for i in 0..plane.len() {
        let d = clip255(round_even(plane[i] + 128.0)) - reference[i];
        out[i] = d * d;
    }
}

/// Normal maps: `x`, `y` are the reconstructed planes; with `px = np.clip(x + 128, 0, 255)` (and `py`),
/// `(codec.unit_normals(np.stack([px, py, codec.normal_z(px, py)], -1)) - reference) ** 2`.
pub fn sqerr_normal(x: &[f64], y: &[f64], reference: &[f64], out: &mut [f64]) {
    for i in 0..x.len() {
        let px = clip255(x[i] + 128.0);
        let py = clip255(y[i] + 128.0);
        // normal_z
        let nx = px / 127.5 - 1.0;
        let ny = py / 127.5 - 1.0;
        let mut inside = 1.0 - nx * nx - ny * ny;
        if inside < 0.0 {
            inside = 0.0;
        } else if inside > 1.0 {
            inside = 1.0;
        }
        let pz = (inside.sqrt() + 1.0) * 127.5;
        // unit_normals: n = rgb / 127.5 - 1, length from the sum over the last axis (sequential)
        let n = [px / 127.5 - 1.0, py / 127.5 - 1.0, pz / 127.5 - 1.0];
        let ln = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
        let ln = if ln > 1e-6 { ln } else { 1e-6 };
        for k in 0..3 {
            let d = (n[k] / ln + 1.0) * 127.5 - reference[3 * i + k];
            out[3 * i + k] = d * d;
        }
    }
}

/// numpy's pairwise summation (`DOUBLE_pairwise_sum`) of `n` values starting at index `start`, which
/// `fill(first index, buffer)` hands over in order: blocks of at most 128 values added with eight running
/// sums, halves split at a multiple of eight. `np.mean` of a contiguous float64 array is this sum over all
/// its values divided by their count.
fn pairwise_sum<F: FnMut(usize, &mut [f64])>(start: usize, n: usize, fill: &mut F) -> f64 {
    if n > 128 {
        let mut half = n / 2;
        half -= half % 8;
        let left = pairwise_sum(start, half, fill);
        return left + pairwise_sum(start + half, n - half, fill);
    }
    let mut block = [0f64; 128];
    let a = &mut block[..n];
    fill(start, a);
    if n < 8 {
        let mut res = 0.0;
        for &v in a.iter() {
            res += v;
        }
        return res;
    }
    let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
    let whole = n - n % 8;
    let mut i = 8;
    while i < whole {
        for k in 0..8 {
            r[k] += a[i + k];
        }
        i += 8;
    }
    let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    while i < n {
        res += a[i];
        i += 1;
    }
    res
}

/// `np.mean` of the array a `sqerr_*` function would write, without building it: `produce(first item,
/// count, out)` writes `per` values for each of `count` items; the values go to the pairwise summation a
/// block at a time (it reads them strictly in order).
fn mean_of<P: FnMut(usize, usize, &mut [f64])>(items: usize, per: usize, mut produce: P) -> f64 {
    const BLOCK: usize = 1024;
    let mut block = vec![0f64; BLOCK * per];
    let (mut first, mut count) = (0usize, 0usize);
    let mut fill = |start: usize, buf: &mut [f64]| {
        let mut done = 0;
        while done < buf.len() {
            let index = start + done;
            if index >= (first + count) * per {
                first += count;
                count = BLOCK.min(items - first);
                produce(first, count, &mut block[..count * per]);
            }
            let offset = index - first * per;
            let n = (buf.len() - done).min(count * per - offset);
            buf[done..done + n].copy_from_slice(&block[offset..offset + n]);
            done += n;
        }
    };
    let total = items * per;
    pairwise_sum(0, total, &mut fill) / total as f64
}

/// `np.mean` of `sqerr_ycocg`'s array.
pub fn mse_ycocg(y: &[f64], co: &[f64], cg: &[f64], reference: &[f64]) -> f64 {
    mean_of(y.len(), 3, |a, n, out| {
        sqerr_ycocg(&y[a..a + n], &co[a..a + n], &cg[a..a + n], &reference[3 * a..3 * (a + n)], out)
    })
}

/// `np.mean` of `sqerr_plane`'s array.
pub fn mse_plane(plane: &[f64], reference: &[f64]) -> f64 {
    mean_of(plane.len(), 1, |a, n, out| sqerr_plane(&plane[a..a + n], &reference[a..a + n], out))
}

/// `np.mean` of `sqerr_normal`'s array.
pub fn mse_normal(x: &[f64], y: &[f64], reference: &[f64]) -> f64 {
    mean_of(x.len(), 3, |a, n, out| sqerr_normal(&x[a..a + n], &y[a..a + n], &reference[3 * a..3 * (a + n)], out))
}

/// `codec.fwd53` along the last axis: `x` is (h x 2n); writes the low band to `s` and the high band to
/// `d` (h x n each).
fn fwd53_cols(x: &[f64], h: usize, n: usize, s: &mut [f64], d: &mut [f64]) {
    for y in 0..h {
        let row = &x[y * 2 * n..][..2 * n];
        let sr = &mut s[y * n..][..n];
        let dr = &mut d[y * n..][..n];
        // d -= 0.5 * (s + s shifted, last element repeated)
        for i in 0..n {
            let next = row[2 * (if i + 1 < n { i + 1 } else { n - 1 })];
            dr[i] = row[2 * i + 1] - 0.5 * (row[2 * i] + next);
        }
        // s += 0.25 * (d + d shifted the other way, first element repeated)
        for i in 0..n {
            let prev = dr[if i == 0 { 0 } else { i - 1 }];
            sr[i] = row[2 * i] + 0.25 * (dr[i] + prev);
        }
        for i in 0..n {
            sr[i] *= S2;
            dr[i] /= S2;
        }
    }
}

/// `codec.fwd53` along the first axis: `x` is (2n x w); `s` and `d` are (n x w).
fn fwd53_rows(x: &[f64], n: usize, w: usize, s: &mut [f64], d: &mut [f64]) {
    for i in 0..n {
        let even = &x[2 * i * w..][..w];
        let odd = &x[(2 * i + 1) * w..][..w];
        let next = &x[2 * (if i + 1 < n { i + 1 } else { n - 1 }) * w..][..w];
        let dr = &mut d[i * w..][..w];
        for c in 0..w {
            dr[c] = odd[c] - 0.5 * (even[c] + next[c]);
        }
    }
    for i in 0..n {
        let prev = if i == 0 { 0 } else { i - 1 };
        for c in 0..w {
            s[i * w + c] = x[2 * i * w + c] + 0.25 * (d[i * w + c] + d[prev * w + c]);
        }
    }
    for v in s[..n * w].iter_mut() {
        *v *= S2;
    }
    for v in d[..n * w].iter_mut() {
        *v /= S2;
    }
}

/// `codec.dwt(plane, levels)` for a plane of `h` x `w`: `out` receives LL and then, coarse to fine,
/// LH, HL and HH of every level (the layout `recon` reads), h * w doubles in all.
pub fn dwt(plane: &[f64], h: usize, w: usize, levels: usize, out: &mut [f64]) {
    let mut ll = plane.to_vec();
    let (mut ch, mut cw) = (h, w);
    // the finest level is computed first and sits at the end of `out`
    let mut end = h * w;
    for _ in 0..levels {
        let (nh, nw) = (ch / 2, cw / 2);
        let n = nh * nw;
        let mut lo = vec![0f64; ch * nw];
        let mut hi = vec![0f64; ch * nw];
        fwd53_cols(&ll, ch, nw, &mut lo, &mut hi); // horizontal
        let mut lolo = vec![0f64; n];
        let (lh, rest) = out[end - 3 * n..end].split_at_mut(n);
        let (hl, hh) = rest.split_at_mut(n);
        fwd53_rows(&lo, nh, nw, &mut lolo, lh); // vertical: low, high of the low half
        fwd53_rows(&hi, nh, nw, hl, hh); // and of the high half
        end -= 3 * n;
        ll = lolo;
        ch = nh;
        cw = nw;
    }
    out[..ch * cw].copy_from_slice(&ll);
}

const TILE: usize = 8; // codec.TILE
const NLEV: f64 = 8.0; // codec.NLEV

/// The bytes `codec.put_band(out, codec.quant(band, st))` appends (`noise` false) or the bytes of
/// `codec.put_band_nf(out, q, codec.noise_codes(band, q, st))` (`noise` true), for a band of `h` x `w`.
/// None when a coefficient is not finite or leaves the int16 range (the Python code raises there).
pub fn put_band(band: &[f64], h: usize, w: usize, st: f64, noise: bool) -> Option<Vec<u8>> {
    let n = h * w;
    let mut q = Vec::with_capacity(n);
    let (mut lo, mut hi) = (0i64, 0i64);
    for &c in band {
        if !c.is_finite() {
            return None;
        }
        let v = quant(c, st);
        lo = lo.min(v);
        hi = hi.max(v);
        q.push(v);
    }
    if lo < -32768 || hi > 32767 {
        return None;
    }
    let any = lo != 0 || hi != 0;
    let wide = lo < -128 || hi > 127;
    let mut out = Vec::with_capacity(1 + n * if wide { 2 } else { 1 } + if noise { n / 64 + 1 } else { 0 });
    if noise {
        // put_band_nf: type | 16, the noise map, then the coefficients (zeros written out)
        out.push((if wide { 2u8 } else { 1u8 }) | 16);
        let th = (h / TILE).max(1);
        let tw = (w / TILE).max(1);
        // np.bincount: the weights of a tile are added in raster order
        let mut e = vec![0f64; th * tw];
        let mut cnt = vec![0f64; th * tw];
        for y in 0..h {
            let ty = (y / TILE).min(th - 1);
            for x in 0..w {
                if q[y * w + x] == 0 {
                    let t = ty * tw + (x / TILE).min(tw - 1);
                    let b = band[y * w + x];
                    e[t] += b * b;
                    cnt[t] += 1.0;
                }
            }
        }
        let unit = 0.6 / NLEV * st;
        for t in 0..th * tw {
            let rms = (e[t] / if cnt[t] > 1.0 { cnt[t] } else { 1.0 }).sqrt();
            let code = (rms / unit).round_ties_even();
            out.push(if code < 0.0 {
                0
            } else if code > NLEV - 1.0 {
                (NLEV - 1.0) as u8
            } else {
                code as u8
            });
        }
    } else {
        if !any {
            out.push(0);
            return Some(out);
        }
        out.push(if wide { 2 } else { 1 });
    }
    if wide {
        for v in q {
            out.extend_from_slice(&(v as i16).to_le_bytes());
        }
    } else {
        out.extend(q.iter().map(|&v| v as i8 as u8));
    }
    Some(out)
}

/// `codec.rgb_to_ycocg`: `rgb` holds pixels of `stride` doubles (R, G, B first); writes the three planes.
pub fn ycocg(rgb: &[f64], stride: usize, y: &mut [f64], co: &mut [f64], cg: &mut [f64]) {
    for i in 0..y.len() {
        let (r, g, b) = (rgb[i * stride], rgb[i * stride + 1], rgb[i * stride + 2]);
        y[i] = 0.25 * r + 0.5 * g + 0.25 * b - 128.0;
        co[i] = 0.5 * r - 0.5 * b;
        cg[i] = -0.25 * r + 0.5 * g - 0.25 * b;
    }
}

/// `codec.unit_normals`: `rgb` holds pixels of `stride` doubles; `out` receives 3 doubles per pixel.
pub fn unit_normals(rgb: &[f64], stride: usize, out: &mut [f64]) {
    for i in 0..out.len() / 3 {
        let n = [
            rgb[i * stride] / 127.5 - 1.0,
            rgb[i * stride + 1] / 127.5 - 1.0,
            rgb[i * stride + 2] / 127.5 - 1.0,
        ];
        let ln = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
        let ln = if ln > 1e-6 { ln } else { 1e-6 };
        for k in 0..3 {
            out[3 * i + k] = (n[k] / ln + 1.0) * 127.5;
        }
    }
}
