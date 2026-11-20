-- Lab prototype (review/claude): JIT-friendly decoder of the CM band stream (same bits as cm_decoder.lua).
-- Few live values in the hot loop; the per-chunk constants live in module locals the rare path reads.
CM3 = CM3 or {}
local floor, byte, abs, max, min = math.floor, string.byte, math.abs, math.max, math.min
local CLS = { [0] = 0, 1, 2, 3, 3, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6 }
local VBUF, VCAP = {}, 0
local D, SIGN, MAG, EG, SIGNB, MAGB, EGB, W2   -- per chunk / band constants of the rare path

local function probs(n) local t = {}; for i = 1, n do t[i] = 2048 end; return t end

-- one decision on P[i] from (code, range, pos) -> bit, code, range, pos
local function bit(P, i, code, range, pos)
    local p = P[i]
    local b = floor(range * 0.000244140625) * p
    local r
    if code < b then range = b; P[i] = p + floor((4096 - p) * 0.0625); r = 0
    else code = code - b; range = range - b; P[i] = p - floor(p * 0.0625); r = 1 end
    while range < 16777216 do range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1 end
    return r, code, range, pos
end

-- sign and magnitude of a significant coefficient at V index i
local function nz(V, i, ctx, code, range, pos)
    local vw, vn = V[i - 1], V[i - W2]
    local s = (vw < 0 and 0 or (vw == 0 and 1 or 2)) * 3 + (vn < 0 and 0 or (vn == 0 and 1 or 2))
    local neg, b
    neg, code, range, pos = bit(SIGN, SIGNB + s, code, range, pos)
    local g = ctx < 2 and 0 or (ctx < 5 and 1 or 2)
    local a = 1
    repeat
        local bin = a < 15 and a or 15
        b, code, range, pos = bit(MAG, ((MAGB + bin) * 3 + g) * 3 + 1, code, range, pos)
        if b == 0 then return neg == 1 and -a or a, code, range, pos end
        a = a + 1
    until a > 14
    local nb = 1
    while true do
        b, code, range, pos = bit(EG, EGB + nb, code, range, pos)
        if b == 0 then break end
        nb = nb + 1
    end
    local r = 1
    for _ = 1, nb - 1 do
        range = floor(range * 0.5)
        local d = 0
        if code >= range then code = code - range; d = 1 end
        while range < 16777216 do range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1 end
        r = r * 2 + d
    end
    a = r + 14
    return neg == 1 and -a or a, code, range, pos
end

-- the significant-or-not loop over one row segment [x0, x1) of padded row base rowi
local function segment(V, SIG, sigb, rowi, x0, x1, code, range, pos)
    local W = W2
    for x = x0, x1 - 1 do
        local i = rowi + x
        local t = floor((2 * abs(V[i - 1]) + 2 * abs(V[i - W]) + abs(V[i - W - 1]) + abs(V[i - W + 1])
                         + abs(V[i - 2]) + abs(V[i - 2 * W]) + 1) * 0.5)
        local ctx = t <= 20 and CLS[t] or 7
        local si = sigb + ctx * 3
        local p = SIG[si]
        local b = floor(range * 0.000244140625) * p
        if code < b then
            range = b; SIG[si] = p + floor((4096 - p) * 0.0625)
            if range < 16777216 then
                range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1
                if range < 16777216 then range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1 end
            end
        else
            code = code - b; range = range - b; SIG[si] = p - floor(p * 0.0625)
            if range < 16777216 then
                range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1
                if range < 16777216 then range = range * 256; code = code * 256 + byte(D, pos); pos = pos + 1 end
            end
            local v
            v, code, range, pos = nz(V, i, ctx, code, range, pos)
            V[i] = v
        end
    end
    return code, range, pos
end

function CM3.DecodeChunk(data, shapes, nclass, Y)
    D = data .. "\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0"
    local pos = 1
    local code, range = 0, 4294967295
    for _ = 1, 5 do code = (code * 256 + byte(D, pos)) % 4294967296; pos = pos + 1 end
    nclass = nclass or 1
    local NOISE, TILE, SIG = probs(64), probs(4 * nclass), probs(nclass * 24)
    SIGN, MAG, EG = probs(9 * nclass), probs(nclass * 144), probs(32 * nclass)
    local out = {}
    local b
    for bi = 1, #shapes do
        local sh = shapes[bi]
        local h, w, bc, ll = sh.h, sh.w, sh.class or 0, sh.ll
        local codes
        if sh.codes then
            codes = {}
            local prev = 0
            for t = 1, max(1, floor(h / 8)) * max(1, floor(w / 8)) do
                local node = 1
                for _ = 1, 3 do
                    b, code, range, pos = bit(NOISE, prev * 8 + node + 1, code, range, pos)
                    node = node * 2 + b
                end
                prev = node - 8
                codes[t] = prev
            end
        end
        local th, tw = floor((h + 7) / 8), floor((w + 7) / 8)
        local flags = {}
        if not ll then
            for ty = 0, th - 1 do
                for tx = 0, tw - 1 do
                    local l = tx > 0 and flags[ty * tw + tx] or 0
                    local u = ty > 0 and flags[(ty - 1) * tw + tx + 1] or 0
                    b, code, range, pos = bit(TILE, bc * 4 + l + 2 * u + 1, code, range, pos)
                    flags[ty * tw + tx + 1] = b
                end
            end
        else
            for t = 1, th * tw do flags[t] = 1 end
        end
        W2 = w + 3
        local need = (h + 2) * W2 + 1
        local V = VBUF
        if need > VCAP then for i = VCAP + 1, need do V[i] = 0 end; VCAP = need end
        for i = 1, need do V[i] = 0 end
        SIGNB, MAGB, EGB = bc * 9 + 1, bc * 16, bc * 32
        local sigb = bc * 24 + 1
        for ty = 0, th - 1 do
            local frow = ty * tw + 1
            for y = ty * 8, min(h, ty * 8 + 8) - 1 do
                local rowi = (y + 2) * W2 + 3
                local tx = 0
                while tx < tw do
                    if flags[frow + tx] == 1 then
                        -- a run of non-zero tiles is one segment
                        local tx1 = tx + 1
                        while tx1 < tw and flags[frow + tx1] == 1 do tx1 = tx1 + 1 end
                        code, range, pos = segment(V, SIG, sigb, rowi, tx * 8, min(w, tx1 * 8), code, range, pos)
                        tx = tx1
                    else
                        tx = tx + 1
                    end
                end
            end
            if Y then Y() end
        end
        if ll then
            for y = 0, h - 1 do
                local rowi = (y + 2) * W2 + 3
                for x = 0, w - 1 do
                    local i = rowi + x
                    local pr
                    if y == 0 and x == 0 then pr = 0
                    elseif y == 0 then pr = V[i - 1]
                    elseif x == 0 then pr = V[i - W2]
                    else
                        local a, bb, c = V[i - 1], V[i - W2], V[i - W2 - 1]
                        local mx, mn = a, bb
                        if bb > a then mx, mn = bb, a end
                        if c >= mx then pr = mn elseif c <= mn then pr = mx else pr = a + bb - c end
                    end
                    V[i] = V[i] + pr
                end
            end
        end
        if sh.copy then
            local vals, k = {}, 0
            for y = 0, h - 1 do
                local rowi = (y + 2) * W2 + 3
                for x = 0, w - 1 do k = k + 1; vals[k] = V[rowi + x] end
            end
            out[bi] = { codes = codes, vals = vals }
        else
            out[bi] = { codes = codes }
        end
    end
    return out
end
