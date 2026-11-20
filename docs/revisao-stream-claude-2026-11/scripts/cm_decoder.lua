-- Lab prototype (review/claude): Lua decoder of the context-modelled GMOT bands (rust/gmotcm, pbits 12,
-- shift 4, 8x8 tile flags, sign contexts). Plain arithmetic on doubles (exact below 2^53), no bit library.
CM = CM or {}
local floor, abs, byte = math.floor, math.abs, string.byte
local CAP = 14
local PONE = 4096           -- 2^pbits
local TOP = 16777216        -- 2^24
local NCLS = 8
local CLS = { [0] = 0, 1, 2, 3, 3, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6 }

local function new_probs(n) local t = {}; for i = 1, n do t[i] = 2048 end; return t end

local function med(V, W2, i)
    local a, b, c = V[i - 1], V[i - W2], V[i - W2 - 1]
    local mx, mn = a, b
    if b > a then mx, mn = b, a end
    if c >= mx then return mn elseif c <= mn then return mx end
    return a + b - c
end

-- data: coded chunk; shapes: { {h=, w=, class=, ll=, codes=bool}, ... }; Y: optional yield every few rows
-- returns { {codes = {..} or nil, vals = {..} (flat 1-based, h*w)}, ... }
function CM.DecodeChunk(data, shapes, nclass, Y)
    local pos = 1
    local code, range = 0, 4294967295
    for _ = 1, 5 do code = (code * 256 + (byte(data, pos) or 0)) % 4294967296; pos = pos + 1 end
    nclass = nclass or 1
    local NOISE = new_probs(64)
    local TILE = new_probs(4 * nclass)
    local SIG = new_probs(nclass * NCLS * 3)
    local SIGN = new_probs(9 * nclass)
    local MAG = new_probs(nclass * 16 * 3 * 3)
    local EG = new_probs(32 * nclass)
    -- one adaptive binary decision (inlined copies below in the hot loop)
    local function bit(P, i)
        local p = P[i]
        local bound = floor(range / PONE) * p
        local b
        if code < bound then range = bound; P[i] = p + floor((PONE - p) / 16); b = 0
        else code = code - bound; range = range - bound; P[i] = p - floor(p / 16); b = 1 end
        while range < TOP do range = range * 256; code = code * 256 + (byte(data, pos) or 0); pos = pos + 1 end
        return b
    end
    local function direct()
        range = floor(range / 2)
        local b = 0
        if code >= range then code = code - range; b = 1 end
        while range < TOP do range = range * 256; code = code * 256 + (byte(data, pos) or 0); pos = pos + 1 end
        return b
    end
    local out = {}
    for bi = 1, #shapes do
        local sh = shapes[bi]
        local h, w, bc, ll = sh.h, sh.w, sh.class or 0, sh.ll
        local codes
        if sh.codes then
            codes = {}
            local prev = 0
            local nt = math.max(1, floor(h / 8)) * math.max(1, floor(w / 8))
            for t = 1, nt do
                local node = 1
                for _ = 1, 3 do node = node * 2 + bit(NOISE, prev * 8 + node + 1) end
                local v = node - 8
                codes[t] = v
                prev = v
            end
        end
        local th, tw = floor((h + 7) / 8), floor((w + 7) / 8)
        local flags
        local use_tiles = not ll
        if use_tiles then
            flags = {}
            for ty = 0, th - 1 do
                for tx = 0, tw - 1 do
                    local l = tx > 0 and flags[ty * tw + tx] or 0
                    local u = ty > 0 and flags[(ty - 1) * tw + tx + 1] or 0
                    flags[ty * tw + tx + 1] = bit(TILE, bc * 4 + l + 2 * u + 1)
                end
            end
        end
        -- padded planes: 2 rows on top, 2 columns left, 1 right, zeros
        local W2 = w + 3
        local V, M = {}, {}
        for i = 1, (h + 2) * W2 + 1 do V[i] = 0; M[i] = 0 end
        local sigb, signb, magb, egb = bc * NCLS * 3, bc * 9, bc * 16, bc * 32
        for y = 0, h - 1 do
            if Y and y % 16 == 15 then Y() end
            local rowi = (y + 2) * W2 + 2 + 1          -- index of x = 0
            local trow = floor(y / 8) * tw + 1
            for x = 0, w - 1 do
                if use_tiles and flags[trow + floor(x / 8)] == 0 then
                    -- stays 0
                else
                    local i = rowi + x
                    local s = 2 * M[i - 1] + 2 * M[i - W2] + M[i - W2 - 1] + M[i - W2 + 1] + M[i - 2] + M[i - 2 * W2]
                    local t = floor((s + 1) / 2)
                    local ctx = t <= 20 and CLS[t] or 7
                    -- significance
                    local si = sigb + ctx * 3 + 1
                    local p = SIG[si]
                    local bound = floor(range / PONE) * p
                    if code < bound then
                        range = bound; SIG[si] = p + floor((PONE - p) / 16)
                        while range < TOP do range = range * 256; code = code * 256 + (byte(data, pos) or 0); pos = pos + 1 end
                    else
                        code = code - bound; range = range - bound; SIG[si] = p - floor(p / 16)
                        while range < TOP do range = range * 256; code = code * 256 + (byte(data, pos) or 0); pos = pos + 1 end
                        local vw, vn = V[i - 1], V[i - W2]
                        local sw = vw < 0 and 0 or (vw == 0 and 1 or 2)
                        local sn = vn < 0 and 0 or (vn == 0 and 1 or 2)
                        local neg = bit(SIGN, signb + sw * 3 + sn + 1)
                        local g = ctx < 2 and 0 or (ctx < 5 and 1 or 2)
                        local a = 1
                        while true do
                            if a > CAP then
                                local nb = 1
                                while bit(EG, egb + nb) == 1 do nb = nb + 1 end
                                local r = 1
                                for _ = 1, nb - 1 do r = r * 2 + direct() end
                                a = r - 1 + CAP + 1
                                break
                            end
                            local bin = a < 15 and a or 15
                            if bit(MAG, ((magb + bin) * 3 + g) * 3 + 1) == 0 then break end
                            a = a + 1
                        end
                        M[i] = a
                        V[i] = neg == 1 and -a or a
                    end
                end
            end
        end
        if ll then
            -- residuals -> values in raster order (MED of the values already rebuilt)
            for y = 0, h - 1 do
                local rowi = (y + 2) * W2 + 3
                for x = 0, w - 1 do
                    local i = rowi + x
                    local pr
                    if y == 0 and x == 0 then pr = 0
                    elseif y == 0 then pr = V[i - 1]
                    elseif x == 0 then pr = V[i - W2]
                    else pr = med(V, W2, i) end
                    V[i] = V[i] + pr
                end
            end
        end
        -- unpad
        local vals = {}
        local k = 0
        for y = 0, h - 1 do
            local rowi = (y + 2) * W2 + 3
            for x = 0, w - 1 do k = k + 1; vals[k] = V[rowi + x] end
        end
        out[bi] = { codes = codes, vals = vals }
    end
    return out
end
