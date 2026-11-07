//! Native hot paths of the GMod Addon Optimizer worker.
//!
//! Every function here is a drop-in replacement for a Python/numpy routine and must return
//! exactly the same bytes (the Python versions stay as the reference and as the fallback when
//! the DLL is missing). The C ABI is loaded with ctypes; ctypes releases the GIL during calls,
//! so worker threads run these in parallel.

pub mod band;
pub mod bc;
pub mod sample;
pub mod seams;

/// Bumped whenever an exported function changes behaviour or signature.
pub const ABI_VERSION: u32 = 4;

/// `texlab.allocate._sample_map`: 8x8 wrapped box blur of `m` (h x w float32) sampled at `n`
/// UV pairs (float64, interleaved u, v). Writes `n` floats to `out`.
///
/// # Safety
/// `m` must hold h*w floats, `uv` 2*n doubles and `out` n floats.
#[no_mangle]
pub unsafe extern "C" fn gmo_sample_map(m: *const f32, h: u32, w: u32, uv: *const f64, n: usize, out: *mut f32) -> i32 {
    if m.is_null() || h == 0 || w == 0 || (n > 0 && (uv.is_null() || out.is_null())) {
        return -3;
    }
    let (h, w) = (h as usize, w as usize);
    let m = std::slice::from_raw_parts(m, h * w);
    if n == 0 {
        return 0;
    }
    let uv = std::slice::from_raw_parts(uv, 2 * n);
    let out = std::slice::from_raw_parts_mut(out, n);
    sample::sample_map(m, h, w, uv, out);
    0
}

/// Band energy map between mip j (`hi`, 2*hl x 2*wl RGBA8) and mip j+1 (`lo`, hl x wl RGBA8),
/// as `texlab.allocate.band_energy_maps` computes it. `out` holds 4*hl*wl floats.
///
/// # Safety
/// `hi` must hold 16*hl*wl bytes, `lo` 4*hl*wl bytes and `out` 4*hl*wl floats.
#[no_mangle]
pub unsafe extern "C" fn gmo_band_energy(hi: *const u8, lo: *const u8, hl: u32, wl: u32, contrast: i32,
                                         out: *mut f32) -> i32 {
    if hi.is_null() || lo.is_null() || out.is_null() || hl == 0 || wl == 0 {
        return -3;
    }
    let (hl, wl) = (hl as usize, wl as usize);
    let hi = std::slice::from_raw_parts(hi, 16 * hl * wl);
    let lo = std::slice::from_raw_parts(lo, 4 * hl * wl);
    let out = std::slice::from_raw_parts_mut(out, 4 * hl * wl);
    band::band_energy(hi, lo, hl, wl, contrast != 0, out);
    0
}

#[no_mangle]
pub extern "C" fn gmo_native_abi_version() -> u32 {
    ABI_VERSION
}

/// Decode a BC1/BC2/BC3 (VTF DXT1 = 13, DXT1_ONEBITALPHA = 20, DXT3 = 14, DXT5 = 15) image to RGBA8.
///
/// `out` must hold `w * h * 4` bytes. Returns 0 on success, -1 for an unknown format,
/// -2 when `len` is shorter than the block data, -3 for a null pointer or zero size.
///
/// # Safety
/// `data` must point to `len` readable bytes and `out` to `w * h * 4` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn gmo_decode_bc(data: *const u8, len: usize, w: u32, h: u32, fmt: i32, out: *mut u8) -> i32 {
    if data.is_null() || out.is_null() || w == 0 || h == 0 {
        return -3;
    }
    let src = std::slice::from_raw_parts(data, len);
    let dst = std::slice::from_raw_parts_mut(out, (w as usize) * (h as usize) * 4);
    match bc::decode(src, w as usize, h as usize, fmt, dst) {
        Ok(()) => 0,
        Err(bc::DecodeError::UnknownFormat) => -1,
        Err(bc::DecodeError::Truncated) => -2,
    }
}

/// Pass 1 of `model_screen.simplify` (`_seams`), in place. Inputs as numpy arrays (float64 /
/// int64, C order): `pos`/`nrm` nV*3, `uv` nV*2, `wid`/`bkey`/`uv_lim` nV, `pw` nW*3 (welded
/// positions), `comp`/`comp_eps` nW, `tris` nT*3 (rewritten), `alive` nT (written, 0/1).
/// Returns 0 (and the removed count), -3 null/empty, -4 an index out of range, -5 an internal
/// inconsistency; on error `tris`/`alive` may be partly written (callers pass copies).
///
/// # Safety
/// Every pointer must hold the number of elements given above.
#[no_mangle]
pub unsafe extern "C" fn gmo_seams(
    n_v: usize, pos: *const f64, nrm: *const f64, uv: *const f64, wid: *const i64, bkey: *const i64,
    uv_lim: *const f64, n_w: usize, pw: *const f64, comp: *const i64, comp_eps: *const f64, n_t: usize,
    tris: *mut i64, alive: *mut u8, cos_n: f64, floor: f64, rel_size: f64, removed: *mut i64,
) -> i32 {
    let ptrs_ok = !(pos.is_null() || nrm.is_null() || uv.is_null() || wid.is_null() || bkey.is_null()
        || uv_lim.is_null() || pw.is_null() || comp.is_null() || comp_eps.is_null() || tris.is_null()
        || alive.is_null() || removed.is_null());
    if !ptrs_ok || n_v == 0 || n_w == 0 || n_t == 0 {
        return -3;
    }
    let inp = seams::Input {
        pos: std::slice::from_raw_parts(pos, n_v * 3),
        nrm: std::slice::from_raw_parts(nrm, n_v * 3),
        uv: std::slice::from_raw_parts(uv, n_v * 2),
        wid: std::slice::from_raw_parts(wid, n_v),
        bkey: std::slice::from_raw_parts(bkey, n_v),
        uv_lim: std::slice::from_raw_parts(uv_lim, n_v),
        pw: std::slice::from_raw_parts(pw, n_w * 3),
        comp: std::slice::from_raw_parts(comp, n_w),
        comp_eps: std::slice::from_raw_parts(comp_eps, n_w),
        cos_n,
        floor,
        rel_size,
    };
    let tris = std::slice::from_raw_parts_mut(tris as *mut [i64; 3], n_t);
    let mut live = vec![true; n_t];
    match seams::seams(&inp, tris, &mut live) {
        Ok(count) => {
            let out = std::slice::from_raw_parts_mut(alive, n_t);
            for (o, &a) in out.iter_mut().zip(live.iter()) {
                *o = a as u8;
            }
            *removed = count;
            0
        }
        Err(seams::SeamsError::BadIndex) => -4,
        Err(seams::SeamsError::Inconsistent) => -5,
    }
}

/// Test hook: insert `n` non-negative keys into a CPython-3.11-ordered set and write its
/// iteration order to `out` (n slots). Returns the set size, or -1 for a negative key.
///
/// # Safety
/// `keys` must hold n values and `out` n slots.
#[no_mangle]
pub unsafe extern "C" fn gmo_pyset_order(keys: *const i64, n: usize, out: *mut i64) -> i64 {
    if n > 0 && (keys.is_null() || out.is_null()) {
        return -1;
    }
    let mut s = seams::PySet::new();
    if n > 0 {
        for &k in std::slice::from_raw_parts(keys, n) {
            if k < 0 {
                return -1;
            }
            s.add(k);
        }
        let out = std::slice::from_raw_parts_mut(out, n);
        for (o, k) in out.iter_mut().zip(s.iter()) {
            *o = k;
        }
    }
    s.len() as i64
}

/// `model_screen.simplify.components` (connected components, label = smallest node index).
/// `edges` holds n_edges (a, b) int64 pairs; `out` n labels. Returns 0, -3 null, -4 bad index.
///
/// # Safety
/// `edges` must hold 2*n_edges values and `out` n slots.
#[no_mangle]
pub unsafe extern "C" fn gmo_components(n: usize, edges: *const i64, n_edges: usize, out: *mut i64) -> i32 {
    if out.is_null() || (n_edges > 0 && edges.is_null()) {
        return -3;
    }
    let e = if n_edges > 0 { std::slice::from_raw_parts(edges as *const [i64; 2], n_edges) } else { &[] };
    match seams::components(n, e) {
        Some(lab) => {
            std::slice::from_raw_parts_mut(out, n).copy_from_slice(&lab);
            0
        }
        None => -4,
    }
}
