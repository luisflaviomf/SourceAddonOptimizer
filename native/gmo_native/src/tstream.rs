//! Texture stream step search (`texture_stream/choose.py`): the reconstruction of a plane at a
//! quantization step and the squared error against the source, bit-identical to
//! `choose._recon` (`codec.quant`, `codec.idwt`, `codec.inv53`) and to the terms numpy feeds its
//! mean in `codec.psnr`. The mean itself stays in numpy (its pairwise summation is not redone
//! here): these functions return the array numpy would have built.
//!
//! Every operation is the numpy one in the same order: separate multiply and add (Rust never
//! fuses them), `np.round` = round half to even, a Python `int` step becomes an f64 before the
//! division.

const S2: f64 = std::f64::consts::SQRT_2; // float(np.sqrt(2.0))

/// `codec.inv53` along the first axis: `s`, `d` are (n x w), `out` is (2n x w).
fn inv53_rows(s: &[f64], d: &[f64], n: usize, w: usize, out: &mut [f64]) {
    let a = 1.0 / S2;
    let b = 0.25 * S2;
    // s' = s * _A - _B * (dp + d), dp = d shifted by one (first element repeated)
    for i in 0..n {
        let dp = &d[(if i == 0 { 0 } else { i - 1 }) * w..][..w];
        let di = &d[i * w..][..w];
        let si = &s[i * w..][..w];
        let o = &mut out[2 * i * w..][..w];
        for c in 0..w {
            o[c] = si[c] * a - b * (dp[c] + di[c]);
        }
    }
    // d' = d * S2 + 0.5 * (s' + s' shifted the other way, last element repeated)
    for i in 0..n {
        let next = if i + 1 < n { i + 1 } else { n - 1 };
        let di = &d[i * w..][..w];
        for c in 0..w {
            let v = di[c] * S2 + 0.5 * (out[2 * i * w + c] + out[2 * next * w + c]);
            out[(2 * i + 1) * w + c] = v;
        }
    }
}

/// `codec.inv53` along the last axis: `s`, `d` are (h x n), `out` is (h x 2n).
fn inv53_cols(s: &[f64], d: &[f64], h: usize, n: usize, out: &mut [f64]) {
    let a = 1.0 / S2;
    let b = 0.25 * S2;
    for y in 0..h {
        let sr = &s[y * n..][..n];
        let dr = &d[y * n..][..n];
        let o = &mut out[y * 2 * n..][..2 * n];
        for i in 0..n {
            let dp = dr[if i == 0 { 0 } else { i - 1 }];
            o[2 * i] = sr[i] * a - b * (dp + dr[i]);
        }
        for i in 0..n {
            let next = if i + 1 < n { i + 1 } else { n - 1 };
            o[2 * i + 1] = dr[i] * S2 + 0.5 * (o[2 * i] + o[2 * next]);
        }
    }
}

/// `codec.quant(c, st) * st` as numpy leaves it for the float code that follows: an integer in an f64.
#[inline]
fn dequant(c: f64, st: f64, step: i64) -> f64 {
    let sign = if c > 0.0 {
        1.0
    } else if c < 0.0 {
        -1.0
    } else {
        0.0
    };
    let q = (sign * ((c.abs() / st + 0.4).floor())) as i64;
    (q * step) as f64
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
    let mut lh = Vec::new();
    let mut hl = Vec::new();
    let mut hh = Vec::new();
    let mut lo = Vec::new();
    let mut hi = Vec::new();
    for level in 0..levels {
        let n = ch * cw;
        let fine = level + drop >= levels;
        for (band, k) in [(&mut lh, 0usize), (&mut hl, 1), (&mut hh, 2)] {
            band.clear();
            if fine {
                band.resize(n, 0.0);
            } else {
                band.extend(coef[pos + k * n..pos + (k + 1) * n].iter().map(|&c| dequant(c, st, step)));
            }
        }
        pos += 3 * n;
        lo.clear();
        lo.resize(2 * n, 0.0);
        hi.clear();
        hi.resize(2 * n, 0.0);
        inv53_rows(&ll, &lh, ch, cw, &mut lo);
        inv53_rows(&hl, &hh, ch, cw, &mut hi);
        if level + 1 == levels {
            inv53_cols(&lo, &hi, 2 * ch, cw, out);
        } else {
            ll.clear();
            ll.resize(4 * n, 0.0);
            inv53_cols(&lo, &hi, 2 * ch, cw, &mut ll);
        }
        ch *= 2;
        cw *= 2;
    }
    if levels == 0 {
        out.copy_from_slice(&ll);
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
            let d = clip255(rgb[k].round_ties_even()) - reference[3 * i + k];
            out[3 * i + k] = d * d;
        }
    }
}

/// `(np.clip(np.round(plane + 128), 0, 255) - reference) ** 2` (the alpha plane).
pub fn sqerr_plane(plane: &[f64], reference: &[f64], out: &mut [f64]) {
    for i in 0..plane.len() {
        let d = clip255((plane[i] + 128.0).round_ties_even()) - reference[i];
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
