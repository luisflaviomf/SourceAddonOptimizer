//! BC1/BC2/BC3 decoding, bit-identical to `texlab/bcnp.py::decode_bc` (the numpy reference).

#[derive(Debug, PartialEq, Eq)]
pub enum DecodeError {
    UnknownFormat,
    Truncated,
}

#[inline]
fn rgb565(c: u32) -> [u32; 3] {
    let r = (c >> 11) & 31;
    let g = (c >> 5) & 63;
    let b = c & 31;
    [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)]
}

/// Colour palette of one 8-byte colour block; `force_four` is true for DXT3/DXT5.
#[inline]
fn colour_palette(cb: &[u8], force_four: bool) -> ([[u8; 4]; 4], u32) {
    let c0 = cb[0] as u32 | ((cb[1] as u32) << 8);
    let c1 = cb[2] as u32 | ((cb[3] as u32) << 8);
    let idx = cb[4] as u32 | ((cb[5] as u32) << 8) | ((cb[6] as u32) << 16) | ((cb[7] as u32) << 24);
    let [r0, g0, b0] = rgb565(c0);
    let [r1, g1, b1] = rgb565(c1);
    let mut pal = [[0u8; 4]; 4];
    pal[0] = [r0 as u8, g0 as u8, b0 as u8, 255];
    pal[1] = [r1 as u8, g1 as u8, b1 as u8, 255];
    if c0 > c1 || force_four {
        pal[2] = [((2 * r0 + r1) / 3) as u8, ((2 * g0 + g1) / 3) as u8, ((2 * b0 + b1) / 3) as u8, 255];
        pal[3] = [((r0 + 2 * r1) / 3) as u8, ((g0 + 2 * g1) / 3) as u8, ((b0 + 2 * b1) / 3) as u8, 255];
    } else {
        pal[2] = [((r0 + r1) / 2) as u8, ((g0 + g1) / 2) as u8, ((b0 + b1) / 2) as u8, 255];
        pal[3] = [0, 0, 0, 0];
    }
    (pal, idx)
}

#[inline]
fn dxt5_alpha(ab: &[u8]) -> ([u8; 8], u64) {
    let a0 = ab[0] as u32;
    let a1 = ab[1] as u32;
    let mut bits = 0u64;
    for k in 0..6 {
        bits |= (ab[2 + k] as u64) << (8 * k);
    }
    let mut apal = [0u8; 8];
    apal[0] = a0 as u8;
    apal[1] = a1 as u8;
    if a0 > a1 {
        for k in 1..7u32 {
            apal[(k + 1) as usize] = (((7 - k) * a0 + k * a1) / 7) as u8;
        }
    } else {
        for k in 1..5u32 {
            apal[(k + 1) as usize] = (((5 - k) * a0 + k * a1) / 5) as u8;
        }
        apal[6] = 0;
        apal[7] = 255;
    }
    (apal, bits)
}

/// Decode `data` (`fmt` 13, 20, 14 or 15) into `out` (`h * w * 4` RGBA bytes, row-major).
pub fn decode(data: &[u8], w: usize, h: usize, fmt: i32, out: &mut [u8]) -> Result<(), DecodeError> {
    let bsize = match fmt {
        13 | 20 => 8usize,
        14 | 15 => 16usize,
        _ => return Err(DecodeError::UnknownFormat),
    };
    let bw = ((w + 3) / 4).max(1);
    let bh = ((h + 3) / 4).max(1);
    if data.len() < bw * bh * bsize {
        return Err(DecodeError::Truncated);
    }
    debug_assert_eq!(out.len(), w * h * 4);
    let mut block = [[0u8; 4]; 16];
    for by in 0..bh {
        for bx in 0..bw {
            let b = &data[(by * bw + bx) * bsize..(by * bw + bx + 1) * bsize];
            let (pal, idx) = colour_palette(&b[bsize - 8..], bsize == 16);
            for (i, px) in block.iter_mut().enumerate() {
                *px = pal[((idx >> (2 * i)) & 3) as usize];
            }
            if fmt == 15 {
                let (apal, bits) = dxt5_alpha(&b[0..8]);
                for (i, px) in block.iter_mut().enumerate() {
                    px[3] = apal[((bits >> (3 * i)) & 7) as usize];
                }
            } else if fmt == 14 {
                for (i, px) in block.iter_mut().enumerate() {
                    px[3] = ((b[i / 2] >> (4 * (i % 2))) & 15) * 17;
                }
            }
            // fmt 13: the numpy reference maps alpha to 0/255, which the palette already is.
            for (i, px) in block.iter().enumerate() {
                let y = by * 4 + i / 4;
                let x = bx * 4 + i % 4;
                if y < h && x < w {
                    let o = (y * w + x) * 4;
                    out[o..o + 4].copy_from_slice(px);
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn three_colour_dxt1_has_transparent_black() {
        // c0 < c1 -> 3-colour mode, index 3 -> transparent black
        let block = [0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF];
        let mut out = vec![0u8; 4 * 4 * 4];
        decode(&block, 4, 4, 13, &mut out).unwrap();
        assert_eq!(&out[0..4], &[0, 0, 0, 0]);
    }

    #[test]
    fn small_images_are_cropped() {
        let block = [0xFF, 0xFF, 0x00, 0x00, 0, 0, 0, 0];
        let mut out = vec![0u8; 2 * 1 * 4];
        decode(&block, 2, 1, 13, &mut out).unwrap();
        assert_eq!(out, vec![255, 255, 255, 255, 255, 255, 255, 255]);
    }

    #[test]
    fn truncated_data_is_rejected() {
        let mut out = vec![0u8; 8 * 8 * 4];
        assert_eq!(decode(&[0u8; 8], 8, 8, 13, &mut out), Err(DecodeError::Truncated));
    }
}
