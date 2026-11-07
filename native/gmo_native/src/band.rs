//! Band energy between two mip levels, bit-identical to `texlab/allocate.py::band_energy_maps`
//! (numpy float32 with NEP 50 weak Python scalars, `texlab/npsolve.py::up` with f = 2 and wrap
//! addressing on both axes).
//!
//! Every Python float constant is first rounded to f64 and then to f32, exactly like numpy
//! casts a Python scalar, and every operation keeps numpy's order (no fused multiply-add).

#[inline]
fn c(x: f64) -> f32 {
    x as f32
}

/// Bilinear 2x magnification of `x` (h, w, 4 channels) along columns, wrap addressing.
fn up_cols(x: &[f32], h: usize, w: usize) -> Vec<f32> {
    let (q, t) = (c(0.25), c(0.75));
    let mut out = vec![0f32; h * 2 * w * 4];
    for y in 0..h {
        let row = &x[y * w * 4..(y + 1) * w * 4];
        let orow = &mut out[y * 2 * w * 4..(y + 1) * 2 * w * 4];
        for i in 0..w {
            let prev = (i + w - 1) % w;
            let next = (i + 1) % w;
            for ch in 0..4 {
                let xi = row[i * 4 + ch];
                orow[(2 * i) * 4 + ch] = q * row[prev * 4 + ch] + t * xi;
                orow[(2 * i + 1) * 4 + ch] = t * xi + q * row[next * 4 + ch];
            }
        }
    }
    out
}

/// Bilinear 2x magnification of `x` (h rows of `rowlen` floats) along rows, wrap addressing.
fn up_rows(x: &[f32], h: usize, rowlen: usize) -> Vec<f32> {
    let (q, t) = (c(0.25), c(0.75));
    let mut out = vec![0f32; 2 * h * rowlen];
    for y in 0..h {
        let prev = &x[((y + h - 1) % h) * rowlen..((y + h - 1) % h + 1) * rowlen];
        let cur = &x[y * rowlen..(y + 1) * rowlen];
        let next = &x[((y + 1) % h) * rowlen..((y + 1) % h + 1) * rowlen];
        let (top, bottom) = out[2 * y * rowlen..(2 * y + 2) * rowlen].split_at_mut(rowlen);
        for k in 0..rowlen {
            top[k] = q * prev[k] + t * cur[k];
            bottom[k] = t * cur[k] + q * next[k];
        }
    }
    out
}

/// `hi`: (2*hl, 2*wl, 4) RGBA8, `lo`: (hl, wl, 4) RGBA8, `out`: (2*hl, 2*wl) energy map.
pub fn band_energy(hi: &[u8], lo: &[u8], hl: usize, wl: usize, contrast: bool, out: &mut [f32]) {
    let inv = c(255.0);
    let lo_f: Vec<f32> = lo.iter().map(|&v| v as f32 / inv).collect();
    let u = up_rows(&up_cols(&lo_f, hl, wl), hl, 2 * wl * 4);
    let (kr, kg, kb, ka) = (c(0.299), c(0.587), c(0.114), c(0.25));
    let (bias, scale) = (c(0.1), c(0.36));
    for (p, o) in out.iter_mut().enumerate() {
        let hp = &hi[p * 4..p * 4 + 4];
        let up = &u[p * 4..p * 4 + 4];
        let d0 = hp[0] as f32 / inv - up[0];
        let d1 = hp[1] as f32 / inv - up[1];
        let d2 = hp[2] as f32 / inv - up[2];
        let d3 = hp[3] as f32 / inv - up[3];
        let mut e = kr * (d0 * d0) + kg * (d1 * d1) + kb * (d2 * d2) + ka * (d3 * d3);
        if contrast {
            let luma = kr * up[0] + kg * up[1] + kb * up[2];
            let l = luma + bias;
            e = e / (l * l) * scale;
        }
        *o = e;
    }
}
