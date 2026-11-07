//! Native hot paths of the GMod Addon Optimizer worker.
//!
//! Every function here is a drop-in replacement for a Python/numpy routine and must return
//! exactly the same bytes (the Python versions stay as the reference and as the fallback when
//! the DLL is missing). The C ABI is loaded with ctypes; ctypes releases the GIL during calls,
//! so worker threads run these in parallel.

pub mod band;
pub mod bc;
pub mod sample;

/// Bumped whenever an exported function changes behaviour or signature.
pub const ABI_VERSION: u32 = 3;

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
