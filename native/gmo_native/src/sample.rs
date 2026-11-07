//! Box-blurred band energy sampled at triangle UVs, bit-identical to
//! `texlab/allocate.py::_sample_map` (np.pad wrap by 4, float32 cumsum over rows then columns,
//! 8x8 window / 64, sampled at floor(uv * size) mod size).

const K: usize = 4;

/// `m`: (h, w) float32 map, `uv`: n (u, v) float64 pairs, `out`: n float32 samples.
pub fn sample_map(m: &[f32], h: usize, w: usize, uv: &[f64], out: &mut [f32]) {
    let (ph, pw) = (h + 2 * K, w + 2 * K);
    // c[i][j]: cumsum over axis 0 (sequential, starting from the first element), then axis 1
    let mut c = vec![0f32; ph * pw];
    let mut col = vec![0f32; pw];
    for i in 0..ph {
        let src_row = (i + h * (K / h + 1) - K) % h; // (i - K) mod h, never negative
        let row = &m[src_row * w..(src_row + 1) * w];
        for (j, acc) in col.iter_mut().enumerate() {
            let v = row[(j + w * (K / w + 1) - K) % w];
            *acc = if i == 0 { v } else { *acc + v };
        }
        let crow = &mut c[i * pw..(i + 1) * pw];
        crow[0] = col[0];
        for j in 1..pw {
            crow[j] = crow[j - 1] + col[j];
        }
    }
    let div = (4 * K * K) as f64 as f32;
    let (wf, hf) = (w as f64, h as f64);
    for (t, o) in out.iter_mut().enumerate() {
        let x = ((uv[2 * t] * wf).floor() as i64).rem_euclid(w as i64) as usize;
        let y = ((uv[2 * t + 1] * hf).floor() as i64).rem_euclid(h as i64) as usize;
        let a = c[(y + 2 * K) * pw + x + 2 * K];
        let b = c[y * pw + x + 2 * K];
        let cc = c[(y + 2 * K) * pw + x];
        let d = c[y * pw + x];
        *o = (a - b - cc + d) / div;
    }
}
