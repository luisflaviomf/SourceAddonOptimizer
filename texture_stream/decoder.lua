-- GMOT v2 decoder: wavelet planes -> DXT1/DXT5 VTF bytes (texture_stream/codec.py mirrors it: lua_vtf)
-- GMod Addon Optimizer texture stream. Part of the shared runtime (see runtime.lua).
GMOT = GMOT or {}
local byte, char, floor, sqrt = string.byte, string.char, math.floor, math.sqrt
local concat = table.concat
local S2 = sqrt(2)
local A, B, C = 1 / S2, 0.25 * S2, S2

-- cooperative pause point: the runtime sets it to a budget check that coroutine.yield()s
local Y = function() end
function GMOT.SetYield(f) Y = f or function() end end

local function u16(s, p) local a, b = byte(s, p, p + 1); return a + b * 256 end
local function u32(s, p) local a, b, c, d = byte(s, p, p + 3); return a + b * 256 + c * 65536 + d * 16777216 end
local function f32(s, p)
    local a, b, c, d = byte(s, p, p + 3)
    local sign = d >= 128 and -1 or 1
    local e = (d % 128) * 2 + floor(c / 128)
    local m = (c % 128) * 65536 + b * 256 + a
    if e == 0 then return sign * m * 2 ^ -149 end
    return sign * (1 + m / 8388608) * 2 ^ (e - 127)
end

-- band -> flat table (0-based index i at [i + 1]) of dequantized values; zeros noise-filled when the band has a map
local TILE, AMPK = 8, 0.6 / 8 * sqrt(3)
local LA, LC, M32 = 1664525, 1013904223, 4294967296
local function read_band(s, pos, w2, h2, st, index)
    local n = w2 * h2
    local out = {}
    local t = byte(s, pos); pos = pos + 1
    local amps, th, tw
    if t >= 16 then
        t = t - 16
        th, tw = math.max(1, floor(h2 / TILE)), math.max(1, floor(w2 / TILE))
        amps = {}
        for i = 1, th * tw do amps[i] = byte(s, pos + i - 1) * AMPK * st end
        pos = pos + th * tw
    end
    if t == 0 then
        for i = 1, n do out[i] = 0 end
        return out, pos
    end
    local wide = t == 2
    local p0 = wide and (pos - 2) or (pos - 1)
    if not amps then
        for y = 0, h2 - 1 do
            local r = y * w2
            if wide then
                for i = r + 1, r + w2 do
                    local a, b = byte(s, p0 + 2 * i, p0 + 2 * i + 1)
                    local v = a + b * 256
                    if v > 32767 then v = v - 65536 end
                    out[i] = v * st
                end
            else
                for i = r + 1, r + w2 do
                    local v = byte(s, p0 + i)
                    if v > 127 then v = v - 256 end
                    out[i] = v * st
                end
            end
            if y % 16 == 15 then Y() end
        end
    else
        local x = (index * 2654435761 + 12345) % M32
        local i = 0
        for y = 0, h2 - 1 do
            if y % 16 == 15 then Y() end
            local ty = floor(y / TILE); if ty >= th then ty = th - 1 end
            local trow = ty * tw
            for xx = 0, w2 - 1 do
                i = i + 1
                x = (LA * x + LC) % M32
                local v
                if wide then
                    local a, b = byte(s, p0 + 2 * i, p0 + 2 * i + 1)
                    v = a + b * 256
                    if v > 32767 then v = v - 65536 end
                else
                    v = byte(s, p0 + i)
                    if v > 127 then v = v - 256 end
                end
                if v == 0 then
                    local tx = floor(xx / TILE); if tx >= tw then tx = tw - 1 end
                    out[i] = (2 * x / M32 - 1) * amps[trow + tx + 1]
                else
                    out[i] = v * st
                end
            end
        end
    end
    return out, pos + (wide and 2 * n or n)
end

-- one synthesis level: LL, LH, HL, HH (h2 x w2) -> image (2h2 x 2w2); every table is written in order
local function vsynth(Sb, Db, w2, h2)
    local O = {}
    for k = 0, h2 - 1 do
        if k % 16 == 15 then Y() end
        local r = k * w2
        local rp = (k > 0) and (r - w2) or r
        local rn = (k < h2 - 1) and (r + w2) or r
        local ro = 2 * r
        for x = 1, w2 do
            O[ro + x] = Sb[r + x] * A - B * (Db[rp + x] + Db[r + x])
        end
        ro = ro + w2
        if k < h2 - 1 then
            for x = 1, w2 do
                local d = Db[r + x]
                local e1 = Sb[rn + x] * A - B * (d + Db[rn + x])
                O[ro + x] = d * C + 0.5 * (O[ro - w2 + x] + e1)
            end
        else
            for x = 1, w2 do
                O[ro + x] = Db[r + x] * C + O[ro - w2 + x]
            end
        end
    end
    return O
end

local function synth(LL, LH, HL, HH, w2, h2)
    local lo = vsynth(LL, LH, w2, h2)
    local hi = vsynth(HL, HH, w2, h2)
    local out = {}
    local w = 2 * w2
    for y = 0, 2 * h2 - 1 do
        if y % 16 == 15 then Y() end
        local r = y * w2
        local ro = y * w - 1
        local d0 = hi[r + 1]
        local e = lo[r + 1] * A - B * (d0 + d0)
        for i = 1, w2 do
            local d = hi[r + i]
            local en
            if i < w2 then en = lo[r + i + 1] * A - B * (d + hi[r + i + 1]) else en = e end
            out[ro + 2 * i] = e
            out[ro + 2 * i + 1] = d * C + 0.5 * (e + en)
            e = en
        end
    end
    return out
end

local function down2(p, w, h)
    local nw, nh = math.max(1, floor(w / 2)), math.max(1, floor(h / 2))
    local out = {}
    if w > 1 and h > 1 then
        for y = 0, nh - 1 do
            local r0, r1, ro = 2 * y * w, (2 * y + 1) * w, y * nw
            for x = 1, nw do
                out[ro + x] = 0.25 * (p[r0 + 2 * x - 1] + p[r0 + 2 * x] + p[r1 + 2 * x - 1] + p[r1 + 2 * x])
            end
        end
    elseif h > 1 then
        for y = 0, nh - 1 do out[y + 1] = 0.5 * (p[2 * y + 1] + p[2 * y + 2]) end
    else
        for x = 1, nw do out[x] = 0.5 * (p[2 * x - 1] + p[2 * x]) end
    end
    return out, nw, nh
end

-- ---------------------------------------------------------------- DXT encoding of one level
local R, G, Bc, Al = {}, {}, {}, {}
local POW4 = { 1, 4, 16, 64, 1, 4, 16, 64, 1, 4, 16, 64, 1, 4, 16, 64 }
local function q5(v) v = floor(v * 31 / 255 + 0.5); if v < 0 then return 0 elseif v > 31 then return 31 end return v end
local function q6(v) v = floor(v * 63 / 255 + 0.5); if v < 0 then return 0 elseif v > 63 then return 63 end return v end

-- DXT5 alpha blocks of one level, at parts[1], parts[3], ... (a loop of its own: one trace per job)
local function encode_alpha(A_, w, h, parts)
    local bw, bh = math.max(1, floor((w + 3) / 4)), math.max(1, floor((h + 3) / 4))
    local np = -1
    for by = 0, bh - 1 do
        Y()
        for bx = 0, bw - 1 do
            local mna, mxa = 255, 0
            for j = 0, 3 do
                local y = by * 4 + j; if y >= h then y = h - 1 end
                for i = 0, 3 do
                    local x = bx * 4 + i; if x >= w then x = w - 1 end
                    local a = A_[y * w + x + 1] + 128
                    if a < 0 then a = 0 elseif a > 255 then a = 255 end
                    Al[j * 4 + i + 1] = a
                    if a < mna then mna = a end; if a > mxa then mxa = a end
                end
            end
            local a0, a1 = floor(mxa + 0.5), floor(mna + 0.5)
            local b0, b1, b2, b3, b4, b5 = 0, 0, 0, 0, 0, 0
            if a0 > a1 then
                local sc = 7 / (a0 - a1)
                local acc, mul = 0, 1
                for k = 1, 16 do
                    local p = floor((a0 - Al[k]) * sc + 0.5)
                    local ix = (p <= 0) and 0 or ((p >= 7) and 1 or (p + 1))
                    acc = acc + ix * mul
                    mul = mul * 8
                    if k == 8 then
                        b0 = acc % 256; acc = floor(acc / 256); b1 = acc % 256; b2 = floor(acc / 256)
                        acc, mul = 0, 1
                    end
                end
                b3 = acc % 256; acc = floor(acc / 256); b4 = acc % 256; b5 = floor(acc / 256)
            end
            np = np + 2
            parts[np] = char(a0, a1, b0, b1, b2, b3, b4, b5)
        end
    end
end

-- colour blocks of one level at parts[stride], parts[2 * stride], ...
local function encode_level(P, w, h, mode, stride, parts)
    local bw, bh = math.max(1, floor((w + 3) / 4)), math.max(1, floor((h + 3) / 4))
    local P1, P2, P3 = P[1], P[2], P[3]
    local np = 0
    for by = 0, bh - 1 do
        Y()
        for bx = 0, bw - 1 do
            -- gather 16 pixels (edge clamp), convert colour, clamp
            local sr, sg, sb = 0, 0, 0
            local mnr, mng, mnb, mxr, mxg, mxb = 255, 255, 255, 0, 0, 0
            for j = 0, 3 do
                local y = by * 4 + j; if y >= h then y = h - 1 end
                for i = 0, 3 do
                    local x = bx * 4 + i; if x >= w then x = w - 1 end
                    local idx = y * w + x + 1
                    local r, g, b
                    if mode == 1 then
                        r, g = P1[idx] + 128, P2[idx] + 128
                        if r < 0 then r = 0 elseif r > 255 then r = 255 end
                        if g < 0 then g = 0 elseif g > 255 then g = 255 end
                        local nx, ny = r / 127.5 - 1, g / 127.5 - 1
                        local nz = 1 - nx * nx - ny * ny
                        b = (nz > 0 and sqrt(nz) or 0) * 127.5 + 127.5
                    else
                        local yy = P1[idx] + 128
                        local co, cg = P2[idx], P3[idx]
                        local t = yy - cg
                        r, g, b = t + co, yy + cg, t - co
                        if r < 0 then r = 0 elseif r > 255 then r = 255 end
                        if g < 0 then g = 0 elseif g > 255 then g = 255 end
                        if b < 0 then b = 0 elseif b > 255 then b = 255 end
                    end
                    local k = j * 4 + i + 1
                    R[k], G[k], Bc[k] = r, g, b
                    sr, sg, sb = sr + r, sg + g, sb + b
                    if r < mnr then mnr = r end; if r > mxr then mxr = r end
                    if g < mng then mng = g end; if g > mxg then mxg = g end
                    if b < mnb then mnb = b end; if b > mxb then mxb = b end
                end
            end
            -- colour endpoints: bounding box diagonal from covariance signs, inset
            local mr, mg, mb = sr / 16, sg / 16, sb / 16
            local crg, cbg = 0, 0
            for k = 1, 16 do
                local dg = G[k] - mg
                crg = crg + (R[k] - mr) * dg
                cbg = cbg + (Bc[k] - mb) * dg
            end
            local hr, lr, hg, lg, hb, lb = mxr, mnr, mxg, mng, mxb, mnb
            if crg < 0 then hr, lr = mnr, mxr end
            if cbg < 0 then hb, lb = mnb, mxb end
            local ir, ig, ib = (hr - lr) / 16, (hg - lg) / 16, (hb - lb) / 16
            hr, hg, hb, lr, lg, lb = hr - ir, hg - ig, hb - ib, lr + ir, lg + ig, lb + ib
            local c0 = q5(hr) * 2048 + q6(hg) * 32 + q5(hb)
            local c1 = q5(lr) * 2048 + q6(lg) * 32 + q5(lb)
            local s0, s1, s2, s3 = 0, 0, 0, 0
            for it = 0, 1 do
                if c0 < c1 then c0, c1 = c1, c0 end
                local r0, g0, b0 = floor(c0 / 2048), floor(c0 / 32) % 64, c0 % 32
                local r1, g1, b1 = floor(c1 / 2048), floor(c1 / 32) % 64, c1 % 32
                r0, g0, b0 = r0 * 8 + floor(r0 / 4), g0 * 4 + floor(g0 / 16), b0 * 8 + floor(b0 / 4)
                r1, g1, b1 = r1 * 8 + floor(r1 / 4), g1 * 4 + floor(g1 / 16), b1 * 8 + floor(b1 / 4)
                local dr, dg, db = r1 - r0, g1 - g0, b1 - b0
                local dd = dr * dr + dg * dg + db * db
                if c0 == c1 or dd == 0 then
                    s0, s1, s2, s3 = 0, 0, 0, 0
                    break
                end
                local sc = 3 / dd
                -- selectors by projection; LS sums for refinement
                local a11, a22, a12 = 0, 0, 0
                local x1r, x1g, x1b, x2r, x2g, x2b = 0, 0, 0, 0, 0, 0
                s0, s1, s2, s3 = 0, 0, 0, 0
                for k = 1, 16 do
                    local t = floor(((R[k] - r0) * dr + (G[k] - g0) * dg + (Bc[k] - b0) * db) * sc + 0.5)
                    if t < 0 then t = 0 elseif t > 3 then t = 3 end
                    local ix = (t == 0) and 0 or ((t == 3) and 1 or (t + 1))
                    local v = ix * POW4[k]
                    if k <= 4 then s0 = s0 + v elseif k <= 8 then s1 = s1 + v elseif k <= 12 then s2 = s2 + v else s3 = s3 + v end
                    local wt = 1 - t / 3
                    local wu = 1 - wt
                    a11 = a11 + wt * wt; a22 = a22 + wu * wu; a12 = a12 + wt * wu
                    x1r, x1g, x1b = x1r + wt * R[k], x1g + wt * G[k], x1b + wt * Bc[k]
                    x2r, x2g, x2b = x2r + wu * R[k], x2g + wu * G[k], x2b + wu * Bc[k]
                end
                if it == 1 then break end
                local det = a11 * a22 - a12 * a12
                if det > 1e-6 or det < -1e-6 then
                    local e0r = (a22 * x1r - a12 * x2r) / det
                    local e0g = (a22 * x1g - a12 * x2g) / det
                    local e0b = (a22 * x1b - a12 * x2b) / det
                    local e1r = (a11 * x2r - a12 * x1r) / det
                    local e1g = (a11 * x2g - a12 * x1g) / det
                    local e1b = (a11 * x2b - a12 * x1b) / det
                    c0 = q5(e0r) * 2048 + q6(e0g) * 32 + q5(e0b)
                    c1 = q5(e1r) * 2048 + q6(e1g) * 32 + q5(e1b)
                else
                    break
                end
            end
            np = np + stride
            parts[np] = char(c0 % 256, floor(c0 / 256), c1 % 256, floor(c1 / 256), s0, s1, s2, s3)
        end
    end
end

-- uncompressed output (the source format): byte order per pixel as indices into { r, g, b, a, 255 }
local RAW = { [0] = { 1, 2, 3, 4 }, [1] = { 4, 3, 2, 1 }, [2] = { 1, 2, 3 }, [3] = { 3, 2, 1 },
              [12] = { 3, 2, 1, 4 }, [16] = { 3, 2, 1, 5 } }
local unpack = unpack or table.unpack
local function encode_raw(P, w, h, mode, alpha, order, parts)
    local P1, P2, P3 = P[1], P[2], P[3]
    local nc = #order
    local o1, o2, o3, o4 = order[1], order[2], order[3], order[4]
    local px = { 0, 0, 0, 255, 255 }
    local buf, nb = {}, 0
    for y = 0, h - 1 do
        if y % 16 == 15 then Y() end
        for x = 1, w do
            local idx = y * w + x
            local r, g, b
            if mode == 1 then
                r, g = P1[idx] + 128, P2[idx] + 128
                if r < 0 then r = 0 elseif r > 255 then r = 255 end
                if g < 0 then g = 0 elseif g > 255 then g = 255 end
                local nx, ny = r / 127.5 - 1, g / 127.5 - 1
                local nz = 1 - nx * nx - ny * ny
                b = (nz > 0 and sqrt(nz) or 0) * 127.5 + 127.5
            else
                local yy = P1[idx] + 128
                local co, cg = P2[idx], P3[idx]
                local t = yy - cg
                r, g, b = t + co, yy + cg, t - co
                if r < 0 then r = 0 elseif r > 255 then r = 255 end
                if g < 0 then g = 0 elseif g > 255 then g = 255 end
                if b < 0 then b = 0 elseif b > 255 then b = 255 end
            end
            px[1], px[2], px[3] = floor(r + 0.5), floor(g + 0.5), floor(b + 0.5)
            if alpha then
                local a = alpha[idx] + 128
                if a < 0 then a = 0 elseif a > 255 then a = 255 end
                px[4] = floor(a + 0.5)
            end
            buf[nb + 1], buf[nb + 2], buf[nb + 3] = px[o1], px[o2], px[o3]
            if nc == 4 then buf[nb + 4] = px[o4] end
            nb = nb + nc
            if nb >= 4000 then
                parts[#parts + 1] = char(unpack(buf, 1, nb))
                nb = 0
            end
        end
    end
    if nb > 0 then parts[#parts + 1] = char(unpack(buf, 1, nb)) end
end

-- data: whole GMOT file string. Returns VTF bytes, stats table.
-- preview: decode only the first chunk (largest side <= the encoder's preview size)
function GMOT.Decode(data, preview)
    local stats = {}
    local t0 = SysTime()
    assert(data:sub(1, 4) == "GMOT" and byte(data, 5) == 2, "not GMOT v2")
    local nch = byte(data, 6)
    local sizes, offs = {}, {}
    local p = 7
    for i = 1, nch do sizes[i] = u32(data, p); p = p + 4 end
    for i = 1, nch do offs[i] = p; p = p + sizes[i] end
    local function chunk(i)
        local c = util.Decompress(data:sub(offs[i], offs[i] + sizes[i] - 1))
        assert(c, "decompress failed")
        Y()
        return c
    end
    local s = chunk(1)
    local w, h = u16(s, 1), u16(s, 3)
    local L, n, mode, cdrop, P = byte(s, 5, 9)
    local pos = 10
    local steps = {}
    for i = 1, n do steps[i] = f32(s, pos); pos = pos + 4 end
    local hlen = u32(s, pos); pos = pos + 4
    local header = s:sub(pos, pos + hlen - 1); pos = pos + hlen
    local out_fmt, mips = byte(s, pos, pos + 1); pos = pos + 2
    local t1 = SysTime()
    local PL = {}
    for pi = 1, n do
        local st = steps[pi]
        local LL
        LL, pos = read_band(s, pos, floor(w / 2 ^ L), floor(h / 2 ^ L), st, (pi - 1) * 64 + L * 4)
        for lvl = L, P + 1, -1 do
            local w2, h2 = floor(w / 2 ^ lvl), floor(h / 2 ^ lvl)
            local LH, HL, HH
            LH, pos = read_band(s, pos, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 1)
            HL, pos = read_band(s, pos, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 2)
            HH, pos = read_band(s, pos, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 3)
            LL = synth(LL, LH, HL, HH, w2, h2)
        end
        PL[pi] = LL
    end
    s = nil
    if preview or P == 0 then
        if P > 0 then
            local k = 1 / 2 ^ P
            for pi = 1, n do local q = PL[pi]; for i = 1, #q do q[i] = q[i] * k end end
            w, h = floor(w / 2 ^ P), floor(h / 2 ^ P)
            if mips > 1 then mips = math.max(1, mips - P) end
            header = header:sub(1, 16) .. char(w % 256, floor(w / 256), h % 256, floor(h / 256)) .. header:sub(21, 56)
                .. char(mips) .. header:sub(58)
        end
    else
        local c = 2
        for lvl = P, 1, -1 do
            local w2, h2 = floor(w / 2 ^ lvl), floor(h / 2 ^ lvl)
            for pi = 1, n do
                local st = steps[pi]
                local LH = read_band(chunk(c), 1, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 1)
                local HL = read_band(chunk(c + 1), 1, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 2)
                local HH = read_band(chunk(c + 2), 1, w2, h2, st, (pi - 1) * 64 + lvl * 4 + 3)
                c = c + 3
                PL[pi] = synth(PL[pi], LH, HL, HH, w2, h2)
            end
        end
    end
    stats.wavelet = SysTime() - t1
    local P_ = PL
    local has_alpha = n == ((mode == 1) and 2 or 3) + 1
    local t2 = SysTime()
    local levels = {}
    local cw, ch = w, h
    for m = 1, mips do
        local parts = {}
        if RAW[out_fmt] then
            encode_raw(P_, cw, ch, mode, has_alpha and P_[n] or nil, RAW[out_fmt], parts)
        else
            if has_alpha then encode_alpha(P_[n], cw, ch, parts) end
            encode_level(P_, cw, ch, mode, has_alpha and 2 or 1, parts)
        end
        levels[m] = concat(parts)
        if m < mips then
            local nw, nh
            for i = 1, n do P_[i], nw, nh = down2(P_[i], cw, ch) end
            cw, ch = nw, nh
        end
        Y()
    end
    stats.dxt = SysTime() - t2
    local order = { header }
    for m = mips, 1, -1 do order[#order + 1] = levels[m] end
    local vtf = concat(order)
    stats.total = SysTime() - t0
    stats.w, stats.h, stats.fmt, stats.mips = w, h, out_fmt, mips
    return vtf, stats
end
