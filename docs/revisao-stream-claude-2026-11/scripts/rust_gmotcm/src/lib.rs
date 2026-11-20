//! Lab prototype (review/claude): context-modelled binary range coding of GMOT bands.
//! Lossless: the same quantized coefficients and noise codes as GMOT v2, so the same image in game.
//! One coder (and one fresh model) per GMOT chunk, so chunks stay independently decodable.
//!
//! Range coder: the LZMA one (32-bit range, carry propagation by cache/low), probabilities of PBITS bits,
//! adapted by shift SHIFT. Model per band:
//!  - noise map: 3-bit code, binary tree, context = previous code
//!  - LL band: MED (LOCO-I) prediction residual, then like any coefficient
//!  - tile flag (8x8): any non-zero coefficient in the tile; context = left and upper tile flags
//!  - significance: context = class of 2W + 2N + NW + NE + WW + NN (8 classes), x band class in chunk 0
//!  - sign: context = signs of W and N (9)
//!  - magnitude: unary bins 1..14 (bin x 3 neighbourhood classes), then Exp-Golomb with direct bits

use std::slice;

const CAP: i32 = 14;

#[derive(Clone, Copy)]
pub struct Cfg {
    pub pbits: u32,
    pub shift: u32,
    pub tiles: bool,
    pub sign_ctx: bool,
    pub cross: bool,
}

// ------------------------------------------------------------------ range coder (LZMA style)
struct Enc {
    low: u64,
    range: u32,
    cache: u8,
    cache_size: u64,
    out: Vec<u8>,
}

impl Enc {
    fn new() -> Self {
        Enc { low: 0, range: 0xFFFF_FFFF, cache: 0, cache_size: 1, out: Vec::new() }
    }
    fn shift_low(&mut self) {
        if (self.low as u32) < 0xFF00_0000 || (self.low >> 32) != 0 {
            let carry = (self.low >> 32) as u8;
            let mut temp = self.cache;
            loop {
                self.out.push(temp.wrapping_add(carry));
                temp = 0xFF;
                self.cache_size -= 1;
                if self.cache_size == 0 {
                    break;
                }
            }
            self.cache = ((self.low >> 24) & 0xFF) as u8;
        }
        self.cache_size += 1;
        self.low = (self.low & 0x00FF_FFFF) << 8;
    }
    fn bit(&mut self, p: &mut u32, b: u32, c: &Cfg) {
        let one = 1u32 << c.pbits;
        let bound = (self.range >> c.pbits) * *p;
        if b == 0 {
            self.range = bound;
            *p += (one - *p) >> c.shift;
        } else {
            self.low += bound as u64;
            self.range -= bound;
            *p -= *p >> c.shift;
        }
        while self.range < (1 << 24) {
            self.range <<= 8;
            self.shift_low();
        }
    }
    fn direct(&mut self, b: u32) {
        self.range >>= 1;
        if b != 0 {
            self.low += self.range as u64;
        }
        while self.range < (1 << 24) {
            self.range <<= 8;
            self.shift_low();
        }
    }
    fn finish(mut self) -> Vec<u8> {
        for _ in 0..5 {
            self.shift_low();
        }
        self.out
    }
}

struct Dec<'a> {
    code: u32,
    range: u32,
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Dec<'a> {
    fn new(buf: &'a [u8]) -> Self {
        let mut d = Dec { code: 0, range: 0xFFFF_FFFF, buf, pos: 0 };
        for _ in 0..5 {
            d.code = (d.code << 8) | d.next() as u32;
        }
        d
    }
    fn next(&mut self) -> u8 {
        let b = if self.pos < self.buf.len() { self.buf[self.pos] } else { 0 };
        self.pos += 1;
        b
    }
    fn bit(&mut self, p: &mut u32, c: &Cfg) -> u32 {
        let one = 1u32 << c.pbits;
        let bound = (self.range >> c.pbits) * *p;
        let b;
        if self.code < bound {
            self.range = bound;
            *p += (one - *p) >> c.shift;
            b = 0;
        } else {
            self.code -= bound;
            self.range -= bound;
            *p -= *p >> c.shift;
            b = 1;
        }
        while self.range < (1 << 24) {
            self.range <<= 8;
            self.code = (self.code << 8) | self.next() as u32;
        }
        b
    }
    fn direct(&mut self) -> u32 {
        self.range >>= 1;
        let b = if self.code >= self.range {
            self.code -= self.range;
            1
        } else {
            0
        };
        while self.range < (1 << 24) {
            self.range <<= 8;
            self.code = (self.code << 8) | self.next() as u32;
        }
        b
    }
}

// ------------------------------------------------------------------ model
const NCLS: usize = 8;

struct Model {
    noise: Vec<u32>, // 8 prev x 8 tree nodes
    tile: Vec<u32>,  // 4
    sig: Vec<u32>,   // bandclass x NCLS x 2 (cross)
    sign: Vec<u32>,  // 9
    mag: Vec<u32>,   // bandclass x 15 bins x 3 classes x 2
    eg: Vec<u32>,    // exp-golomb prefix bits
}

impl Model {
    fn new(c: &Cfg, nclass: usize) -> Self {
        let half = 1u32 << (c.pbits - 1);
        Model {
            noise: vec![half; 64],
            tile: vec![half; 4 * nclass],
            sig: vec![half; nclass * NCLS * 3],
            sign: vec![half; 9 * nclass],
            mag: vec![half; nclass * 16 * 3 * 3],
            eg: vec![half; 32 * nclass],
        }
    }
}

fn cls(s: i32) -> usize {
    (if s < 3 { s } else if s < 5 { 3 } else if s < 8 { 4 } else if s < 13 { 5 } else if s < 21 { 6 } else { 7 }) as usize
}

#[inline]
fn at(m: &[i32], w: usize, h: usize, y: isize, x: isize) -> i32 {
    if y < 0 || x < 0 || x >= w as isize || y >= h as isize {
        0
    } else {
        m[y as usize * w + x as usize]
    }
}

/// neighbourhood class of (y, x) from the magnitudes decoded so far
#[inline]
fn nctx(mag: &[i32], w: usize, h: usize, y: usize, x: usize) -> usize {
    let (yi, xi) = (y as isize, x as isize);
    let s = 2 * at(mag, w, h, yi, xi - 1) + 2 * at(mag, w, h, yi - 1, xi) + at(mag, w, h, yi - 1, xi - 1)
        + at(mag, w, h, yi - 1, xi + 1) + at(mag, w, h, yi, xi - 2) + at(mag, w, h, yi - 2, xi);
    cls((s + 1) / 2)
}

fn med(q: &[i32], w: usize, y: usize, x: usize) -> i32 {
    if y == 0 && x == 0 {
        return 0;
    }
    if y == 0 {
        return q[x - 1];
    }
    if x == 0 {
        return q[(y - 1) * w];
    }
    let a = q[y * w + x - 1];
    let b = q[(y - 1) * w + x];
    let c = q[(y - 1) * w + x - 1];
    if c >= a.max(b) {
        a.min(b)
    } else if c <= a.min(b) {
        a.max(b)
    } else {
        a + b - c
    }
}

pub struct Band<'a> {
    pub h: usize,
    pub w: usize,
    pub class: usize,
    pub ll: bool,
    pub q: &'a [i32],
    pub codes: Option<&'a [u8]>,
    pub cross: Option<&'a [i32]>, // magnitudes of the plane-0 band at the same place (lockstep decode)
}

fn sgn(v: i32) -> usize {
    if v < 0 {
        0
    } else if v == 0 {
        1
    } else {
        2
    }
}

fn enc_value(e: &mut Enc, m: &mut Model, c: &Cfg, v: i32, ctx: usize, bc: usize, sctx: usize, crossbit: usize) {
    let a = v.abs();
    let si = (bc * NCLS + ctx) * 3 + crossbit;
    e.bit(&mut m.sig[si], (a > 0) as u32, c);
    if a == 0 {
        return;
    }
    if c.sign_ctx {
        e.bit(&mut m.sign[bc * 9 + sctx], (v < 0) as u32, c);
    } else {
        e.direct((v < 0) as u32);
    }
    let g = if ctx < 2 { 0 } else if ctx < 5 { 1 } else { 2 };
    for j in 1..=CAP {
        let bin = (j.min(15)) as usize;
        let mi = ((bc * 16 + bin) * 3 + g) * 3 + crossbit;
        let more = (a > j) as u32;
        e.bit(&mut m.mag[mi], more, c);
        if more == 0 {
            return;
        }
    }
    // a > CAP: Exp-Golomb of a - CAP - 1 (prefix adaptive, suffix direct)
    let r = (a - CAP - 1) as u32 + 1;
    let nb = 32 - r.leading_zeros(); // bits of r
    for k in 0..(nb - 1) {
        e.bit(&mut m.eg[bc * 32 + k as usize], 1, c);
    }
    e.bit(&mut m.eg[bc * 32 + (nb - 1) as usize], 0, c);
    for k in (0..(nb - 1)).rev() {
        e.direct((r >> k) & 1);
    }
}

fn dec_value(d: &mut Dec, m: &mut Model, c: &Cfg, ctx: usize, bc: usize, sctx: usize, crossbit: usize) -> i32 {
    let si = (bc * NCLS + ctx) * 3 + crossbit;
    if d.bit(&mut m.sig[si], c) == 0 {
        return 0;
    }
    let neg = if c.sign_ctx { d.bit(&mut m.sign[bc * 9 + sctx], c) } else { d.direct() };
    let g = if ctx < 2 { 0 } else if ctx < 5 { 1 } else { 2 };
    let mut a = 1;
    loop {
        if a > CAP {
            break;
        }
        let bin = (a.min(15)) as usize;
        let mi = ((bc * 16 + bin) * 3 + g) * 3 + crossbit;
        if d.bit(&mut m.mag[mi], c) == 0 {
            return if neg == 1 { -a } else { a };
        }
        a += 1;
    }
    let mut nb = 1u32;
    while d.bit(&mut m.eg[bc * 32 + (nb - 1) as usize], c) == 1 {
        nb += 1;
    }
    let mut r = 1u32;
    for _ in 0..(nb - 1) {
        r = (r << 1) | d.direct();
    }
    let a = (r - 1) as i32 + CAP + 1;
    if neg == 1 {
        -a
    } else {
        a
    }
}

fn tiles_of(b: &Band, mag: &[i32]) -> (usize, usize, Vec<u8>) {
    let th = (b.h + 7) / 8;
    let tw = (b.w + 7) / 8;
    let mut f = vec![0u8; th * tw];
    for y in 0..b.h {
        for x in 0..b.w {
            if mag[y * b.w + x] != 0 {
                f[(y / 8) * tw + x / 8] = 1;
            }
        }
    }
    (th, tw, f)
}

fn crossbit(b: &Band, i: usize) -> usize {
    match b.cross {
        Some(cm) => (cm[i].min(2)) as usize,
        None => 0,
    }
}

/// encode the bands of one chunk; returns the coded bytes
pub fn encode_chunk(bands: &[Band], c: &Cfg, nclass: usize) -> Vec<u8> {
    encode_chunk_with(bands, c, Model::new(c, nclass)).0
}

fn encode_chunk_with(bands: &[Band], c: &Cfg, mut m: Model) -> (Vec<u8>, Model) {
    let mut e = Enc::new();
    for b in bands {
        if let Some(codes) = b.codes {
            let mut prev = 0usize;
            for &v in codes {
                let mut node = 1usize;
                for k in (0..3).rev() {
                    let bit = ((v >> k) & 1) as u32;
                    e.bit(&mut m.noise[prev * 8 + node], bit, c);
                    node = node * 2 + bit as usize;
                }
                prev = v as usize;
            }
        }
        let (h, w) = (b.h, b.w);
        let vals: Vec<i32> = if b.ll {
            (0..h * w).map(|i| b.q[i] - med(b.q, w, i / w, i % w)).collect()
        } else {
            b.q.to_vec()
        };
        let mag: Vec<i32> = vals.iter().map(|v| v.abs()).collect();
        let bc = b.class;
        let (th, tw, flags) = tiles_of(b, &mag);
        let use_tiles = c.tiles && !b.ll;
        if use_tiles {
            for ty in 0..th {
                for tx in 0..tw {
                    let l = if tx > 0 { flags[ty * tw + tx - 1] } else { 0 } as usize;
                    let u = if ty > 0 { flags[(ty - 1) * tw + tx] } else { 0 } as usize;
                    e.bit(&mut m.tile[bc * 4 + l + 2 * u], flags[ty * tw + tx] as u32, c);
                }
            }
        }
        for y in 0..h {
            for x in 0..w {
                if use_tiles && flags[(y / 8) * tw + x / 8] == 0 {
                    continue;
                }
                let i = y * w + x;
                let ctx = nctx(&mag, w, h, y, x);
                let sw = if x > 0 { sgn(vals[i - 1]) } else { 1 };
                let sn = if y > 0 { sgn(vals[i - w]) } else { 1 };
                enc_value(&mut e, &mut m, c, vals[i], ctx, bc, sw * 3 + sn, crossbit(b, i));
            }
        }
    }
    (e.finish(), m)
}

/// decode a chunk coded by encode_chunk (shapes and classes known), -> per band values
pub fn decode_chunk(data: &[u8], shapes: &[(usize, usize, usize, bool, bool)], cross: &[Option<Vec<i32>>],
                    c: &Cfg, nclass: usize) -> Vec<(Option<Vec<u8>>, Vec<i32>)> {
    let mut d = Dec::new(data);
    let mut m = Model::new(c, nclass);
    let mut out = Vec::new();
    for (bi, &(h, w, bc, ll, has_codes)) in shapes.iter().enumerate() {
        let codes = if has_codes {
            let n = (h / 8).max(1) * (w / 8).max(1);
            let mut v = Vec::with_capacity(n);
            let mut prev = 0usize;
            for _ in 0..n {
                let mut node = 1usize;
                for _ in 0..3 {
                    let bit = d.bit(&mut m.noise[prev * 8 + node], c);
                    node = node * 2 + bit as usize;
                }
                let val = node - 8;
                v.push(val as u8);
                prev = val;
            }
            Some(v)
        } else {
            None
        };
        let th = (h + 7) / 8;
        let tw = (w + 7) / 8;
        let use_tiles = c.tiles && !ll;
        let mut flags = vec![1u8; th * tw];
        if use_tiles {
            for ty in 0..th {
                for tx in 0..tw {
                    let l = if tx > 0 { flags[ty * tw + tx - 1] } else { 0 } as usize;
                    let u = if ty > 0 { flags[(ty - 1) * tw + tx] } else { 0 } as usize;
                    flags[ty * tw + tx] = d.bit(&mut m.tile[bc * 4 + l + 2 * u], c) as u8;
                }
            }
        }
        let mut vals = vec![0i32; h * w];
        let mut mag = vec![0i32; h * w];
        let cb = Band { h, w, class: bc, ll, q: &[], codes: None, cross: cross[bi].as_deref() };
        for y in 0..h {
            for x in 0..w {
                if use_tiles && flags[(y / 8) * tw + x / 8] == 0 {
                    continue;
                }
                let i = y * w + x;
                let ctx = nctx(&mag, w, h, y, x);
                let sw = if x > 0 { sgn(vals[i - 1]) } else { 1 };
                let sn = if y > 0 { sgn(vals[i - w]) } else { 1 };
                let v = dec_value(&mut d, &mut m, c, ctx, bc, sw * 3 + sn, crossbit(&cb, i));
                vals[i] = v;
                mag[i] = v.abs();
            }
        }
        if ll {
            for i in 0..h * w {
                let p = med(&vals, w, i / w, i % w);
                vals[i] += p;
            }
            // raster order: the neighbours MED reads are already reconstructed
        }
        out.push((codes, vals));
    }
    out
}

// ------------------------------------------------------------------ C ABI for the Python driver
/// bands: nb rows of meta (h, w, class, ll, has_codes, cross_band (-1 none)), q concatenated (i32), codes
/// concatenated (u8). cfg: pbits, shift, tiles, sign_ctx, cross. Returns the coded size, or -1 when the
/// decoder does not give the input back (checked every call).
#[no_mangle]
pub extern "C" fn cm_chunk(meta: *const i32, nb: i32, q: *const i32, nq: usize, codes: *const u8, ncodes: usize,
                           cfg: *const i32, out_size: *mut usize) -> i32 {
    let meta = unsafe { slice::from_raw_parts(meta, nb as usize * 6) };
    let q = unsafe { slice::from_raw_parts(q, nq) };
    let codes = if ncodes > 0 { unsafe { slice::from_raw_parts(codes, ncodes) } } else { &[][..] };
    let cf = unsafe { slice::from_raw_parts(cfg, 5) };
    let c = Cfg { pbits: cf[0] as u32, shift: cf[1] as u32, tiles: cf[2] != 0, sign_ctx: cf[3] != 0, cross: cf[4] != 0 };
    let mut bands = Vec::new();
    let (mut qo, mut co) = (0usize, 0usize);
    let mut nclass = 1;
    let mut offsets = Vec::new();
    for b in 0..nb as usize {
        let (h, w) = (meta[b * 6] as usize, meta[b * 6 + 1] as usize);
        let class = meta[b * 6 + 2] as usize;
        nclass = nclass.max(class + 1);
        let has_codes = meta[b * 6 + 4] != 0;
        let n = h * w;
        let nc = if has_codes { (h / 8).max(1) * (w / 8).max(1) } else { 0 };
        offsets.push((qo, n));
        bands.push((h, w, class, meta[b * 6 + 3] != 0, has_codes, meta[b * 6 + 5], qo, n, co, nc));
        qo += n;
        co += nc;
    }
    let mags: Vec<Vec<i32>> = offsets.iter().map(|&(o, n)| q[o..o + n].iter().map(|v| v.abs()).collect()).collect();
    let mut bl = Vec::new();
    for &(h, w, class, ll, has_codes, cross, qo, n, co, nc) in &bands {
        let cross_m = if c.cross && cross >= 0 { Some(&mags[cross as usize][..]) } else { None };
        bl.push(Band { h, w, class, ll, q: &q[qo..qo + n], codes: if has_codes { Some(&codes[co..co + nc]) } else { None },
                       cross: cross_m });
    }
    let data = encode_chunk(&bl, &c, nclass);
    unsafe { *out_size = data.len() };
    // round trip
    let shapes: Vec<(usize, usize, usize, bool, bool)> = bands.iter().map(|b| (b.0, b.1, b.2, b.3, b.4)).collect();
    let cross: Vec<Option<Vec<i32>>> = bands.iter().map(|b| if c.cross && b.5 >= 0 { Some(mags[b.5 as usize].clone()) } else { None }).collect();
    let dec = decode_chunk(&data, &shapes, &cross, &c, nclass);
    for (i, b) in bl.iter().enumerate() {
        let (ref dc, ref dv) = dec[i];
        if dv[..] != b.q[..] {
            return -1;
        }
        if let (Some(a), Some(bb)) = (dc, b.codes) {
            if a[..] != bb[..] {
                return -3;
            }
        }
    }
    0
}

/// like cm_chunk, but also hands out the coded bytes (out must hold out_cap bytes); no round trip
#[no_mangle]
pub extern "C" fn cm_chunk_bytes(meta: *const i32, nb: i32, q: *const i32, nq: usize, codes: *const u8, ncodes: usize,
                                 cfg: *const i32, out: *mut u8, out_cap: usize) -> isize {
    let meta = unsafe { slice::from_raw_parts(meta, nb as usize * 6) };
    let q = unsafe { slice::from_raw_parts(q, nq) };
    let codes = if ncodes > 0 { unsafe { slice::from_raw_parts(codes, ncodes) } } else { &[][..] };
    let cf = unsafe { slice::from_raw_parts(cfg, 5) };
    let c = Cfg { pbits: cf[0] as u32, shift: cf[1] as u32, tiles: cf[2] != 0, sign_ctx: cf[3] != 0, cross: false };
    let mut bl = Vec::new();
    let (mut qo, mut co) = (0usize, 0usize);
    let mut nclass = 1;
    for b in 0..nb as usize {
        let (h, w) = (meta[b * 6] as usize, meta[b * 6 + 1] as usize);
        let class = meta[b * 6 + 2] as usize;
        nclass = nclass.max(class + 1);
        let has_codes = meta[b * 6 + 4] != 0;
        let n = h * w;
        let nc = if has_codes { (h / 8).max(1) * (w / 8).max(1) } else { 0 };
        bl.push(Band { h, w, class, ll: meta[b * 6 + 3] != 0, q: &q[qo..qo + n],
                       codes: if has_codes { Some(&codes[co..co + nc]) } else { None }, cross: None });
        qo += n;
        co += nc;
    }
    let data = encode_chunk(&bl, &c, nclass);
    if data.len() > out_cap {
        return -(data.len() as isize);
    }
    unsafe { std::ptr::copy_nonoverlapping(data.as_ptr(), out, data.len()) };
    data.len() as isize
}

impl Model {
    fn len_all(&self) -> usize {
        self.noise.len() + self.tile.len() + self.sig.len() + self.sign.len() + self.mag.len() + self.eg.len()
    }
    fn export(&self) -> Vec<u32> {
        let mut v = Vec::with_capacity(self.len_all());
        for t in [&self.noise, &self.tile, &self.sig, &self.sign, &self.mag, &self.eg] {
            v.extend_from_slice(t);
        }
        v
    }
    fn import(&mut self, v: &[u32]) {
        let mut o = 0;
        for t in [&mut self.noise, &mut self.tile, &mut self.sig, &mut self.sign, &mut self.mag, &mut self.eg] {
            let n = t.len();
            t.copy_from_slice(&v[o..o + n]);
            o += n;
        }
    }
}

/// encode with a fixed number of contexts classes, optional initial probabilities (prior, may be null) and the
/// final probabilities written to state_out (may be null); returns the coded size (no round trip)
#[no_mangle]
pub extern "C" fn cm_chunk_prior(meta: *const i32, nb: i32, q: *const i32, nq: usize, codes: *const u8, ncodes: usize,
                                 cfg: *const i32, nclass: i32, prior: *const u32, state_out: *mut u32) -> isize {
    let meta = unsafe { slice::from_raw_parts(meta, nb as usize * 6) };
    let q = unsafe { slice::from_raw_parts(q, nq) };
    let codes = if ncodes > 0 { unsafe { slice::from_raw_parts(codes, ncodes) } } else { &[][..] };
    let cf = unsafe { slice::from_raw_parts(cfg, 5) };
    let c = Cfg { pbits: cf[0] as u32, shift: cf[1] as u32, tiles: cf[2] != 0, sign_ctx: cf[3] != 0, cross: false };
    let mut bl = Vec::new();
    let (mut qo, mut co) = (0usize, 0usize);
    for b in 0..nb as usize {
        let (h, w) = (meta[b * 6] as usize, meta[b * 6 + 1] as usize);
        let has_codes = meta[b * 6 + 4] != 0;
        let n = h * w;
        let nc = if has_codes { (h / 8).max(1) * (w / 8).max(1) } else { 0 };
        bl.push(Band { h, w, class: meta[b * 6 + 2] as usize, ll: meta[b * 6 + 3] != 0, q: &q[qo..qo + n],
                       codes: if has_codes { Some(&codes[co..co + nc]) } else { None }, cross: None });
        qo += n;
        co += nc;
    }
    let mut m = Model::new(&c, nclass as usize);
    if !prior.is_null() {
        let p = unsafe { slice::from_raw_parts(prior, m.len_all()) };
        m.import(p);
    }
    let (data, m) = encode_chunk_with(&bl, &c, m);
    if !state_out.is_null() {
        let st = m.export();
        unsafe { std::ptr::copy_nonoverlapping(st.as_ptr(), state_out, st.len()) };
    }
    data.len() as isize
}

#[no_mangle]
pub extern "C" fn cm_model_len(nclass: i32) -> usize {
    let c = Cfg { pbits: 12, shift: 4, tiles: true, sign_ctx: true, cross: false };
    Model::new(&c, nclass as usize).len_all()
}
