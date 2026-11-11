-- GMod Addon Optimizer: compressed model geometry ("GMOS" streams) drawn with IMeshes.
-- The addon ships a coarse model (physics, shadows, far distance, fallback) and the full geometry
-- compressed in data_static/. This file decodes a stream the first time its model is drawn
-- (a few milliseconds per frame) and from then on draws the full geometry in place of the model.
-- Console: gmo_stream 0 draws the shipped models only (compare / disable), gmo_stream_far sets the
-- distance beyond which the coarse model is drawn. Streams unused for 5 minutes are freed.
if SERVER then return end

local VERSION = 6          -- GMOS stream format (encoder.VERSION)
local REVISION = 4         -- runtime behaviour: 2 = view models, material overrides, candidate materials;
                           -- 3 = strips longer than one game mesh are cut (they froze the game);
                           -- 4 = bullet marks on the stream (the server part is server.lua)
if GMOS and ((GMOS.VERSION or 0) > VERSION
             or ((GMOS.VERSION or 0) == VERSION and (GMOS.REVISION or 1) >= REVISION)) then
    -- an equal or newer runtime is already loaded: only merge this addon's manifest
    if GMOS_PENDING_MANIFEST then GMOS.AddManifest(GMOS_PENDING_MANIFEST) end
    GMOS_PENDING_MANIFEST = nil
    return
end

local old = GMOS
GMOS = { VERSION = VERSION, REVISION = REVISION, manifest = old and old.manifest or {} }
local G = GMOS

local cv_enable = CreateClientConVar("gmo_stream", "1", true, false, "Draw optimized model geometry streams")
local cv_far = CreateClientConVar("gmo_stream_far", "4000", true, false, "Beyond this distance the coarse model is drawn")
local cv_budget = CreateClientConVar("gmo_stream_budget_ms", "4", true, false, "Decode time per frame")
local cv_decals = CreateClientConVar("gmo_stream_decals", "1", true, false,
    "Bullet marks on optimized models (the game's own marks do not show on them)")

local byte, sub, find, floor, ldexp = string.byte, string.sub, string.find, math.floor, math.ldexp
local band = bit.band
local MAX_STRIP = 65534    -- vertices of one strip IMesh: the game takes 65535, an even cut keeps the winding

function G.AddManifest(t)
    for mdl, path in pairs(t) do G.manifest[string.lower(mdl)] = path end
    if G.reset_lookup then G.reset_lookup() end
end

-- ------------------------------------------------------------------ decoding
local function f32(b1, b2, b3, b4)
    local sign = b4 >= 128 and -1 or 1
    local e = (b4 % 128) * 2 + floor(b3 / 128)
    local m = (b3 % 128) * 65536 + b2 * 256 + b1
    if e == 0 then return sign * ldexp(m, -149) end
    if e == 255 then return 0 end
    return sign * ldexp(m + 8388608, e - 150)
end

local abs_, sqrt_, floor_ = math.abs, math.sqrt, math.floor

-- The engine sends model normals/tangents to the GPU packed with PackNormal_UBYTE4 (2 bytes) and the
-- vertex shader unpacks them like this; the streams store those bytes so the IMeshes get the same vectors.
local function unpack_ubyte4(cx, cy)
    local zs = (cx - 128 < 0) and 1 or 0
    local ts = (cy - 128 < 0) and 1 or 0
    local ax, ay = abs_(cx - 128) - zs, abs_(cy - 128) - ts
    local xs = (ax - 64 < 0) and 1 or 0
    local ys = (ay - 64 < 0) and 1 or 0
    local x, y = (abs_(ax - 64) - xs) / 63, (abs_(ay - 64) - ys) / 63
    local z = 1 - x - y
    local l = sqrt_(x * x + y * y + z * z)
    x, y, z = x / l, y / l, z / l
    if xs == 1 then x = -x end
    if ys == 1 then y = -y end
    if zs == 1 then z = -z end
    return x, y, z, (ts == 1) and -1 or 1
end

local function pack_ubyte4(nx, ny, nz, tsign)
    local xs = nx < 0 and -1 or 1
    local ys = ny < 0 and -1 or 1
    local zs = nz < 0 and -1 or 1
    local ax, ay, az = xs * nx, ys * ny, zs * nz
    local s = ax + ay + az
    if s <= 0 then return 128 + 64, 128 + 64 end
    local xb, yb = ax / s * 63, ay / s * 63
    xb = xb * xs - 0.5 * (1 - xs) + 64
    yb = yb * ys - 0.5 * (1 - ys) + 64
    xb = xb * zs - 0.5 * (1 - zs) + 128
    yb = yb * tsign - 0.5 * (1 - tsign) + 128
    return floor_(xb), floor_(yb)
end

local function decode(blob, yield)
    local pos = 1
    local function u8() local a = byte(blob, pos); pos = pos + 1; return a end
    local function u16() local a, b = byte(blob, pos, pos + 1); pos = pos + 2; return a + b * 256 end
    local function u32() local a, b, c, d = byte(blob, pos, pos + 3); pos = pos + 4; return a + b * 256 + c * 65536 + d * 16777216 end
    local function zstr() local e = find(blob, "\0", pos, true); local s = sub(blob, pos, e - 1); pos = e + 1; return s end
    local function stream(count, signed)
        local nb = u8()
        local vals = {}
        local base = pos
        for i = 1, count do
            local v = byte(blob, base + i - 1)
            if nb > 1 then v = v + byte(blob, base + count + i - 1) * 256 end
            if nb > 2 then v = v + byte(blob, base + 2 * count + i - 1) * 65536 end
            if nb > 3 then v = v + byte(blob, base + 3 * count + i - 1) * 16777216 end
            if signed then
                if v % 2 == 0 then v = v / 2 else v = -(v + 1) / 2 end
            end
            vals[i] = v
            if yield and i % 32768 == 0 then yield() end
        end
        pos = base + nb * count
        return vals
    end
    local out = { mats = {}, skins = {}, groups = {} }
    local ix = f32(byte(blob, 1, 4)); local iy = f32(byte(blob, 5, 8)); local iz = f32(byte(blob, 9, 12))
    pos = 13
    out.illum = Vector(ix, iy, iz)
    out.posq = f32(byte(blob, 13, 16)); out.uvq = f32(byte(blob, 17, 20))
    pos = 21
    local ntex = u32()
    for i = 0, ntex - 1 do out.mats[i] = zstr() end
    local nfam, nref = u32(), u32()
    for fam = 0, nfam - 1 do
        local row = {}
        for r = 0, nref - 1 do row[r] = u16() end
        out.skins[fam] = row
    end
    local ng = u32()
    local nv, ni = 0, 0
    for g = 1, ng do
        local gr = { b = u16(), m = u16(), slot = u16(), bone = u16(), flags = u8(), nv = u32() }
        local nch = u16()
        gr.chunks_len = {}
        gr.nidx = 0
        for c = 1, nch do
            local l = u32()
            gr.chunks_len[c] = l
            gr.nidx = gr.nidx + l
        end
        local p = {}
        for k = 1, 12 do p[k] = f32(byte(blob, pos, pos + 3)); pos = pos + 4 end
        gr.ptb = Matrix({ { p[1], p[2], p[3], p[4] }, { p[5], p[6], p[7], p[8] }, { p[9], p[10], p[11], p[12] }, { 0, 0, 0, 1 } })
        nv = nv + gr.nv
        ni = ni + gr.nidx
        out.groups[g] = gr
    end
    local np_, nn_, nu_ = u32(), u32(), u32()
    local d = {}
    d.pref = stream(nv); d.px = stream(np_, true); d.py = stream(np_, true); d.pz = stream(np_, true)
    d.nref = stream(nv); d.ncx = stream(nn_); d.ncy = stream(nn_)
    d.uvref = stream(nv); d.u = stream(nu_, true); d.v = stream(nu_, true)
    d.idx = stream(ni)
    return out, d
end

local function build(st, yield)
    local raw = file.Read(st.path, "GAME")
    if not raw or sub(raw, 1, 4) ~= "GMOS" or byte(raw, 5) ~= 6 then error("missing or bad stream " .. tostring(st.path)) end
    local blob = util.Decompress(sub(raw, 6))
    if not blob then error("decompress failed " .. st.path) end
    raw = nil
    yield()
    local info, d = decode(blob, yield)
    blob = nil
    local mats = {}
    for i, name in pairs(info.mats) do
        if string.find(name, "|", 1, true) then
            -- material not found when the stream was made (shared texture packs): the engine's own
            -- order of cdmaterials candidates, first one the game has
            local first
            for cand in string.gmatch(name, "[^|]+") do
                local m = Material(cand)
                first = first or m
                if not m:IsError() then first = m break end
            end
            mats[i] = first
        else
            mats[i] = Material(name)
        end
    end
    local vbase, ibase = 0, 0
    local cp, cn, cu = 0, 0, 0          -- read cursors of the "new value" streams
    local vp, vn = Vector(), Vector()
    local pref, px, py, pz = d.pref, d.px, d.py, d.pz
    local nref, ncx, ncy = d.nref, d.ncx, d.ncy
    local uvref, uu, vv, idx = d.uvref, d.u, d.v, d.idx
    local sqrt = math.sqrt
    local POS_Q, UV_Q = info.posq, info.uvq
    for gi, g in ipairs(info.groups) do
        local nvg = g.nv
        local X, Y, Z, NX, NY, NZ, U, V = {}, {}, {}, {}, {}, {}, {}, {}
        local x, y, z, u, v = 0, 0, 0, 0, 0
        for i = 1, nvg do
            local k = vbase + i
            local r = pref[k]
            if r == 0 then
                cp = cp + 1
                x = x + px[cp]; y = y + py[cp]; z = z + pz[cp]
                X[i], Y[i], Z[i] = x * POS_Q, y * POS_Q, z * POS_Q
            else
                X[i], Y[i], Z[i] = X[i - r], Y[i - r], Z[i - r]
            end
            r = nref[k]
            if r == 0 then
                cn = cn + 1
                local a, b, c = unpack_ubyte4(ncx[cn], ncy[cn])
                NX[i], NY[i], NZ[i] = a, b, c
            else
                NX[i], NY[i], NZ[i] = NX[i - r], NY[i - r], NZ[i - r]
            end
            r = uvref[k]
            if r == 0 then
                cu = cu + 1
                u = u + uu[cu]; v = v + vv[cu]
                U[i], V[i] = u * UV_Q, v * UV_Q
            else
                U[i], V[i] = U[i - r], V[i - r]
            end
        end
        -- strip indices of all chunks (high-water mark code)
        local nt3 = g.nidx
        local C = {}
        local hwm = 0
        for c = 1, nt3 do
            local code = idx[ibase + c]
            if code == 0 then hwm = hwm + 1; C[c] = hwm else C[c] = hwm + 1 - code end
        end
        yield()
        -- surface area (the flashlight pass draws the biggest groups only)
        local area = 0
        do
            local o = 0
            for _, len in ipairs(g.chunks_len) do
                for c = o + 1, o + len - 2 do
                    local i0, i1, i2 = C[c], C[c + 1], C[c + 2]
                    if i0 ~= i1 and i1 ~= i2 and i0 ~= i2 then
                        local ax, ay, az = X[i1] - X[i0], Y[i1] - Y[i0], Z[i1] - Z[i0]
                        local bx, by, bz = X[i2] - X[i0], Y[i2] - Y[i0], Z[i2] - Z[i0]
                        local cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
                        area = area + sqrt(cx * cx + cy * cy + cz * cz)
                    end
                end
                o = o + len
            end
        end
        g.area = area
        -- tangents as studiomdl builds them: UV derivatives per triangle, summed per vertex
        local hastan = g.flags % 2 == 1
        local TX, TY, TZ, TW = {}, {}, {}, {}
        if hastan then
            local SX, SY, SZ, BX, BY, BZ = {}, {}, {}, {}, {}, {}
            for i = 1, nvg do SX[i], SY[i], SZ[i], BX[i], BY[i], BZ[i] = 0, 0, 0, 0, 0, 0 end
            local o = 0
            for _, len in ipairs(g.chunks_len) do
            for c = o + 1, o + len - 2 do
                local i0, i1, i2 = C[c], C[c + 1], C[c + 2]
                if i0 ~= i1 and i1 ~= i2 and i0 ~= i2 then
                local e1x, e1y, e1z = X[i1] - X[i0], Y[i1] - Y[i0], Z[i1] - Z[i0]
                local e2x, e2y, e2z = X[i2] - X[i0], Y[i2] - Y[i0], Z[i2] - Z[i0]
                local du1, dv1 = U[i1] - U[i0], V[i1] - V[i0]
                local du2, dv2 = U[i2] - U[i0], V[i2] - V[i0]
                local r = du1 * dv2 - du2 * dv1
                if r ~= 0 then
                    r = 1 / r
                    local sx, sy, sz = (e1x * dv2 - e2x * dv1) * r, (e1y * dv2 - e2y * dv1) * r, (e1z * dv2 - e2z * dv1) * r
                    local bx, by, bz = (e2x * du1 - e1x * du2) * r, (e2y * du1 - e1y * du2) * r, (e2z * du1 - e1z * du2) * r
                    SX[i0], SY[i0], SZ[i0] = SX[i0] + sx, SY[i0] + sy, SZ[i0] + sz
                    BX[i0], BY[i0], BZ[i0] = BX[i0] + bx, BY[i0] + by, BZ[i0] + bz
                    SX[i1], SY[i1], SZ[i1] = SX[i1] + sx, SY[i1] + sy, SZ[i1] + sz
                    BX[i1], BY[i1], BZ[i1] = BX[i1] + bx, BY[i1] + by, BZ[i1] + bz
                    SX[i2], SY[i2], SZ[i2] = SX[i2] + sx, SY[i2] + sy, SZ[i2] + sz
                    BX[i2], BY[i2], BZ[i2] = BX[i2] + bx, BY[i2] + by, BZ[i2] + bz
                end
                end
            end
            o = o + len
            end
            for i = 1, nvg do
                local n1, n2, n3 = NX[i], NY[i], NZ[i]
                local s1, s2, s3 = SX[i], SY[i], SZ[i]
                local dd = n1 * s1 + n2 * s2 + n3 * s3
                local t1, t2, t3 = s1 - n1 * dd, s2 - n2 * dd, s3 - n3 * dd
                local l = sqrt(t1 * t1 + t2 * t2 + t3 * t3)
                if l > 1e-12 then t1, t2, t3 = t1 / l, t2 / l, t3 / l else t1, t2, t3 = 0, 0, 0 end
                local cx, cy, cz = n2 * s3 - n3 * s2, n3 * s1 - n1 * s3, n1 * s2 - n2 * s1
                local w = (cx * BX[i] + cy * BY[i] + cz * BZ[i] < 0) and -1 or 1
                if l > 1e-12 then
                    -- the same 2-byte packing the engine applies to model tangents
                    t1, t2, t3, w = unpack_ubyte4(pack_ubyte4(t1, t2, t3, w))
                end
                TX[i], TY[i], TZ[i], TW[i] = t1, t2, t3, w
            end
        end
        -- strip IMeshes: one per chunk, or several when the chunk is longer than a game mesh takes.
        -- mesh.Begin with more than 65535 vertices raises an error and the game stops responding (seen with
        -- a 81436-vertex chunk, 2026-11-10), so it is never asked for. A strip cut at an even offset keeps
        -- its winding; each piece starts on the last two vertices of the one before, so no triangle is lost.
        local chunks = {}
        local c0 = 0
        for _, len in ipairs(g.chunks_len) do
            local s = 0
            while s < len - 2 do
                local n = len - s
                if n > MAX_STRIP then n = MAX_STRIP end
                local obj = Mesh()
                mesh.Begin(obj, MATERIAL_TRIANGLE_STRIP, n - 2)
                for c = c0 + s + 1, c0 + s + n do
                    local vi = C[c]
                    vp:SetUnpacked(X[vi], Y[vi], Z[vi])
                    vn:SetUnpacked(NX[vi], NY[vi], NZ[vi])
                    mesh.Position(vp)
                    mesh.Normal(vn)
                    mesh.TexCoord(0, U[vi], V[vi])
                    if hastan then mesh.UserData(TX[vi], TY[vi], TZ[vi], TW[vi]) else mesh.UserData(0, 0, 0, 1) end
                    mesh.AdvanceVertex()
                end
                mesh.End()
                chunks[#chunks + 1] = obj
                s = s + n - 2
                yield()
            end
            c0 = c0 + len
        end
        g.chunks = chunks
        g.gi = gi               -- its number in the stream (the bullet marks read the triangles by it)
        vbase = vbase + nvg
        ibase = ibase + nt3
    end
    local nbp, used = 0, {}
    for _, g in ipairs(info.groups) do
        if g.b + 1 > nbp then nbp = g.b + 1 end
        for fam, row in pairs(info.skins) do used[row[g.slot] or g.slot] = true end
        used[g.slot] = true
    end
    info.nbodyparts = nbp
    info.used_tex = {}
    for tex in pairs(used) do info.used_tex[#info.used_tex + 1] = tex end
    table.sort(info.used_tex)
    st.info = info
    st.mats = mats
    st.ready = true
end

-- ------------------------------------------------------------------ state per model
local states = {}

local function state_for(mdl)
    local st = states[mdl]
    if st ~= nil then return st end
    local path = G.manifest[mdl]
    if not path then states[mdl] = false; return false end
    st = { path = path, ready = false }
    st.co = coroutine.create(function()
        local t0 = SysTime()
        local budget = cv_budget:GetFloat() / 1000
        local function yield()
            if SysTime() - t0 > budget then coroutine.yield(); t0 = SysTime() end
        end
        build(st, yield)
    end)
    states[mdl] = st
    return st
end

function G.Ready(mdl)
    local st = states[string.lower(mdl or "")]
    return st and (st.ready or st.failed) or false
end

local function step(st)
    if st.failed or st.ready then return end
    st.last_draw = RealTime()
    local ok, err = coroutine.resume(st.co)
    if not ok then
        st.failed = true
        ErrorNoHalt("[gmo_stream] " .. tostring(err) .. "\n")
    end
end

-- ------------------------------------------------------------------ drawing
local meta = FindMetaTable("Entity")
local ORIG = G.ORIG_DRAWMODEL or (old and old.ORIG_DRAWMODEL) or meta.DrawModel
G.ORIG_DRAWMODEL = ORIG

local matcache = {}
local function mat_named(name)
    local m = matcache[name]
    if not m then m = Material(name); matcache[name] = m end
    return m
end

local transl_cache = {}
local function translucent(m)
    local r = transl_cache[m]
    if r == nil then
        local shader = string.lower(m:GetShader() or "")
        r = band(m:GetInt("$flags") or 0, 0x200000) ~= 0 or (m:GetInt("$translucent") or 0) ~= 0
            or (m:GetInt("$additive") or 0) ~= 0 or shader == "refract"
        transl_cache[m] = r
    end
    return r
end

local STUDIO_TRANSPARENCY = 0x80000000
local SHADOW_FLAGS = 0x40000000 + 0x08000000   -- STUDIO_SHADOWDEPTHTEXTURE, STUDIO_SSAODEPTHTEXTURE

-- Per entity draw lists, rebuilt only when bodygroups, skin, material or submaterials change:
-- { bones = { {bone, ptb, items = { {mat, chunks}... }}... } } for the opaque and translucent pass.
local function signature(ent, st)
    local parts = st.info.nbodyparts
    local s = (ent:GetSkin() or 0) .. "|" .. (ent:GetMaterial() or "")
    for b = 0, parts - 1 do s = s .. "," .. ent:GetBodygroup(b) end
    for _, tex in ipairs(st.info.used_tex) do
        local sm = ent:GetSubMaterial(tex)
        if sm and sm ~= "" then s = s .. "|" .. tex .. "=" .. sm end
    end
    return s
end

local function build_lists(ent, st)
    local info = st.info
    local skin_row = info.skins[ent:GetSkin() or 0] or info.skins[0]
    local ov = ent:GetMaterial()
    local override = (ov and ov ~= "") and mat_named(ov) or nil
    local lists = { [false] = {}, [true] = {} }
    local index = { [false] = {}, [true] = {} }
    for _, g in ipairs(info.groups) do
        if ent:GetBodygroup(g.b) == g.m then
            local tex = skin_row and skin_row[g.slot] or g.slot
            local m
            local subname = ent:GetSubMaterial(tex)
            if subname and subname ~= "" then m = mat_named(subname)
            elseif override then m = override
            else m = st.mats[tex] end
            if m then
                local tr = translucent(m)
                local byb = index[tr][g.bone]
                if not byb then
                    byb = { bone = g.bone, ptb = g.ptb, items = {} }
                    index[tr][g.bone] = byb
                    lists[tr][#lists[tr] + 1] = byb
                end
                byb.items[#byb.items + 1] = { mat = m, tex = tex, chunks = g.chunks, g = g, area = g.area or 0, bone = g.bone, ptb = g.ptb,
                                             refract = tr and string.lower(m:GetShader() or "") == "refract" }
            end
        end
    end
    -- flashlight pass: the biggest opaque groups covering most of the surface (each flashlight
    -- repeats the whole pass; small parts are not worth a draw per flashlight)
    local all, total = {}, 0
    for _, byb in ipairs(lists[false]) do
        for _, it in ipairs(byb.items) do all[#all + 1] = it; total = total + it.area end
    end
    table.sort(all, function(a, b) return a.area > b.area end)
    local flash, fidx, acc = {}, {}, 0
    for i, it in ipairs(all) do
        if i > 24 or acc >= total * 0.85 then break end
        acc = acc + it.area
        local byb = fidx[it.bone]
        if not byb then
            byb = { bone = it.bone, ptb = it.ptb, items = {} }
            fidx[it.bone] = byb
            flash[#flash + 1] = byb
        end
        byb.items[#byb.items + 1] = it
    end
    lists.flash = flash
    -- the light probe is drawn with the material of the biggest opaque group: the engine prepares the
    -- lighting state differently for bumped / unbumped / unlit materials
    lists.probe_mat = all[1] and all[1].mat:GetName() or nil
    return lists
end

-- render.MaterialOverride / ModelMaterialOverride / MaterialOverrideByIndex replace the materials of models the
-- engine draws; IMeshes are drawn with render.SetMaterial, so the stream applies them itself (ARC9 draws scope
-- lenses a second time with a dark material and its depth pass with a DepthWrite material this way)
for _, name in ipairs({ "MaterialOverride", "ModelMaterialOverride" }) do
    local f = G["ORIG_" .. name] or (old and old["ORIG_" .. name]) or render[name]
    G["ORIG_" .. name] = f
    render[name] = function(m, ...)
        G["ov_" .. name] = (m ~= nil and m ~= 0) and m or nil
        return f(m, ...)
    end
end
do
    local f = G.ORIG_MaterialOverrideByIndex or (old and old.ORIG_MaterialOverrideByIndex) or render.MaterialOverrideByIndex
    G.ORIG_MaterialOverrideByIndex = f
    render.MaterialOverrideByIndex = function(i, m, ...)
        if i == nil then G.ov_index = nil
        else
            G.ov_index = G.ov_index or {}
            G.ov_index[i] = (m ~= nil and m ~= 0) and m or nil
        end
        return f(i, m, ...)
    end
end

local function draw_list(ent, list, bms)
    local ovr = G.ov_MaterialOverride or G.ov_ModelMaterialOverride
    local ovi = G.ov_index
    for _, byb in ipairs(list) do
        local M = bms[byb.bone]
        if M == nil then
            local bm = ent:GetBoneMatrix(byb.bone)
            M = bm and (bm * byb.ptb) or false
            bms[byb.bone] = M
        end
        if M then
            cam.PushModelMatrix(M)
            for _, it in ipairs(byb.items) do
                -- by index wins over the global override (ARC9's scope mask: all black, the glass white)
                local m = (ovi and ovi[it.tex]) or ovr or it.mat
                if it.refract and m == it.mat then render.UpdateRefractTexture() end
                render.SetMaterial(m)
                local ch = it.chunks
                for k = 1, #ch do ch[k]:Draw() end
            end
            cam.PopModelMatrix()
        end
    end
end

local frame_bones = setmetatable({}, { __mode = "k" })

-- Projected textures created from Lua (vehicle headlights...): the engine culls its own flashlight
-- passes per model, render.RenderFlashlights does not, so we only ask for it when a light can reach us.
local projected = G.projected or (old and old.projected) or setmetatable({}, { __mode = "k" })
G.projected = projected
if not G.ORIG_ProjectedTexture then
    G.ORIG_ProjectedTexture = (old and old.ORIG_ProjectedTexture) or ProjectedTexture
    ProjectedTexture = function(...)
        local pt = G.ORIG_ProjectedTexture(...)
        if pt then projected[pt] = true end
        return pt
    end
end

local deg, acos, asin, min, max = math.deg, math.acos, math.asin, math.min, math.max
local function lit_by_flashlight(ent)
    local c = ent:WorldSpaceCenter()
    local r = ent:BoundingRadius()
    for pt in pairs(projected) do
        if pt:IsValid() then
            local d = c - pt:GetPos()
            local dist = d:Length()
            if dist < pt:GetFarZ() + r then
                if dist <= r then return true end
                local cosang = d:Dot(pt:GetAngles():Forward()) / dist
                local ang = deg(acos(max(-1, min(1, cosang))))
                local half = max(pt:GetHorizontalFOV(), pt:GetVerticalFOV()) * 0.5
                if ang <= half + deg(asin(min(1, r / dist))) then return true end
            end
        else
            projected[pt] = nil
        end
    end
    for _, ply in ipairs(player.GetAll()) do
        if ply:FlashlightIsOn() and ply:EyePos():DistToSqr(c) < (1100 + r) ^ 2 then return true end
    end
    return false
end

local cv_probe = CreateClientConVar("gmo_stream_probe", "1", true, false, "Light the streamed geometry through a single-mesh probe")
local PROBE_MODEL = "models/gmo_stream/probe_v1.mdl"
-- one probe per entity: the engine interpolates each entity's lighting over time, a probe shared
-- between entities would blend their lighting
local probe_ok
local function get_probe(ent)
    if probe_ok == nil then
        probe_ok = file.Exists(PROBE_MODEL, "GAME")
        if probe_ok then util.PrecacheModel(PROBE_MODEL) end
    end
    if not probe_ok then return nil end
    local p = ent.gmo_probe
    if not IsValid(p) then
        p = ClientsideModel(PROBE_MODEL, RENDERGROUP_OPAQUE)
        if not IsValid(p) then return nil end
        p:SetNoDraw(true)
        ent.gmo_probe = p
        ent:CallOnRemove("gmo_stream_probe", function(e) if IsValid(e.gmo_probe) then e.gmo_probe:Remove() end end)
    end
    return p
end

-- is_vm: a view model drawn from its RenderOverride: never fall back to DrawModel there (on a view model it
-- runs Pre/PostDrawViewModel and ViewModelDrawn a second time: attachments drawn twice)
local function draw_stream(ent, flags, st, is_vm)
    if flags and band(flags, SHADOW_FLAGS) ~= 0 then
        if is_vm then return end
        return ORIG(ent, flags)
    end
    local only_tr = flags and band(flags, STUDIO_TRANSPARENCY) ~= 0
    local fn = FrameNumber()
    local cache = ent.gmo_cache
    if not cache or cache.st ~= st then
        cache = { st = st, next_check = 0, fb = { frame = -1, m = {} } }
        ent.gmo_cache = cache
    end
    -- bodygroups/skin/(sub)materials: checked every 8 frames (and at once after a client-side setter)
    if fn >= cache.next_check then
        cache.next_check = fn + 8
        local sig = signature(ent, st)
        if cache.sig ~= sig then
            cache.sig = sig
            cache.lists = build_lists(ent, st)
        end
    end
    if only_tr and #cache.lists[true] == 0 then return end
    -- the engine sets this entity's lighting state (ambient cube, local lights) with an invisible
    -- draw; the IMeshes drawn right after use that state
    render.OverrideColorWriteEnable(true, false)
    render.OverrideDepthEnable(true, false)
    G.priming = true
    local probe = (cv_probe:GetBool() and st.info.illum and cache.lists.probe_mat) and get_probe(ent) or nil
    if probe then
        -- one tiny model at this entity's lighting origin, drawn with one of the entity's own materials:
        -- the engine computes the same ambient cube and local lights, for the cost of one small draw
        if probe.gmo_mat ~= cache.lists.probe_mat then
            probe.gmo_mat = cache.lists.probe_mat
            probe:SetMaterial(cache.lists.probe_mat)
        end
        probe:SetPos(ent:LocalToWorld(st.info.illum))
        probe:SetAngles(ent:GetAngles())
        probe:DrawModel()
    elseif not is_vm then
        ORIG(ent, flags)
    end
    G.priming = false
    render.OverrideDepthEnable(false)
    render.OverrideColorWriteEnable(false)
    -- bone matrices: once per entity and frame (both passes share them)
    local fb = cache.fb
    if fb.frame ~= fn then
        ent:SetupBones()
        fb.frame = fn
        fb.m = {}
    end
    local cr, cg, cb, ca = ent:GetColor4Part()
    local r, g, b = render.GetColorModulation()
    local a = render.GetBlend()
    -- multiply what the caller set (ARC9 hides the view model with SetBlend(0) behind flat scopes)
    render.SetColorModulation(r * cr / 255, g * cg / 255, b * cb / 255)
    render.SetBlend(a * ca / 255)
    if not only_tr then
        local list = cache.lists[false]
        draw_list(ent, list, fb.m)
        if not cache.flash then
            cache.flash = function() draw_list(ent, cache.lists.flash, cache.fb.m) end
        end
        if lit_by_flashlight(ent) then render.RenderFlashlights(cache.flash) end
        local marks = ent.gmo_decals
        if marks and not is_vm and not G.ov_MaterialOverride and not G.ov_ModelMaterialOverride then
            local bms = fb.m
            for k = 1, #marks do
                local d = marks[k]
                if ent:GetBodygroup(d.b) == d.m then        -- the part it is on is still there
                    local M = bms[d.bone]
                    if M == nil then
                        local bm = ent:GetBoneMatrix(d.bone)
                        M = bm and (bm * d.ptb) or false
                        bms[d.bone] = M
                    end
                    if M then
                        cam.PushModelMatrix(M)
                        render.SetMaterial(d.mat)
                        d.mesh:Draw()
                        cam.PopModelMatrix()
                    end
                end
            end
        end
    end
    if only_tr or not flags or band(flags, 8) == 0 then   -- STUDIO_TWOPASS clear: one call draws both
        draw_list(ent, cache.lists[true], fb.m)
    end
    render.SetColorModulation(r, g, b)
    render.SetBlend(a)
end

-- client-side setters invalidate the cached draw lists immediately
for _, name in ipairs({ "SetSubMaterial", "SetMaterial", "SetSkin", "SetBodygroup", "SetBodyGroups" }) do
    local f = G["ORIG_" .. name] or (old and old["ORIG_" .. name]) or meta[name]
    G["ORIG_" .. name] = f
    meta[name] = function(self, ...)
        local c = self.gmo_cache
        if c then c.next_check = 0 end
        return f(self, ...)
    end
end

local by_raw = {}       -- model path as GetModel() returns it -> state (false: not streamed)
function G.reset_lookup()
    by_raw = {}
    for k, v in pairs(states) do if v == false then states[k] = nil end end
end
local function lookup(mdl)
    local st = by_raw[mdl]
    if st == nil then
        st = state_for(string.lower(mdl))
        by_raw[mdl] = st
    end
    return st
end

-- ------------------------------------------------------------------ bullet marks
-- The game keeps an entity's decals on the mesh of the shipped model and draws them with it; a streamed model
-- shows the stream instead, so the game's bullet holes never appear on it. The marks are made here, on the
-- stream's own triangles: the server tells where a bullet stopped on a streamed model (server.lua, one small
-- message), the stream surface is looked for along the bullet's path around that point, and the triangles
-- under a square the size of the game's decal are cut to it and drawn over the stream with the game's decal
-- material. The triangles of a model are read again from its stream file when its first mark is asked for
-- (a few milliseconds per frame, nothing is kept while nobody shoots) and dropped two minutes after the last.
local MARK_MAX, MARK_TOTAL = 24, 256    -- marks per entity and in all; the oldest goes
local MARK_REACH = 8                    -- the surface is looked for this far before and after the reported hit
local MARK_CELL = 6                     -- side of the cells the triangles are sorted into
local MARK_KEEP = 120                   -- seconds the triangles of a model stay in memory after its last mark
local MARK_NAMES = { [67] = "Impact.Concrete", [77] = "Impact.Metal", [86] = "Impact.Metal", [71] = "Impact.Metal",
                     [80] = "Impact.Metal", [87] = "Impact.Wood", [89] = "Impact.Glass", [84] = "Impact.Concrete",
                     [68] = "Impact.Sand", [78] = "Impact.Sand", [70] = "Impact.Flesh", [66] = "Impact.Flesh",
                     [72] = "Impact.AlienFlesh", [65] = "Impact.Antlion" }
local marks_all = {}                    -- every mark, oldest first
local mark_queue = {}                   -- hits waiting for the triangles of their model
local mark_deadline = 0
local min_, max_ = math.min, math.max

-- One group of a stream ready for short rays: its vertices, its triangles (three vertex numbers each, the
-- degenerate joins of the strips left out) and, per cell of a grid, the triangles that touch it.
local function index_group(X, Y, Z, C, chunks_len, yield)
    local T, nt = {}, 0
    local o = 0
    for _, len in ipairs(chunks_len) do
        for c = o + 1, o + len - 2 do
            local i0, i1, i2 = C[c], C[c + 1], C[c + 2]
            if i0 ~= i1 and i1 ~= i2 and i0 ~= i2 then
                T[nt + 1], T[nt + 2], T[nt + 3] = i0, i1, i2
                nt = nt + 3
            end
        end
        o = o + len
    end
    local x0, y0, z0, x1, y1, z1 = math.huge, math.huge, math.huge, -math.huge, -math.huge, -math.huge
    for i = 1, #X do
        local x, y, z = X[i], Y[i], Z[i]
        if x < x0 then x0 = x end
        if x > x1 then x1 = x end
        if y < y0 then y0 = y end
        if y > y1 then y1 = y end
        if z < z0 then z0 = z end
        if z > z1 then z1 = z end
    end
    local cell = MARK_CELL
    if nt > 0 and max_(x1 - x0, y1 - y0, z1 - z0) / cell > 1000 then cell = max_(x1 - x0, y1 - y0, z1 - z0) / 1000 end
    local cells, big = {}, {}
    for t = 1, nt, 3 do
        local i0, i1, i2 = T[t], T[t + 1], T[t + 2]
        local ax, bx = floor((min_(X[i0], X[i1], X[i2]) - x0) / cell), floor((max_(X[i0], X[i1], X[i2]) - x0) / cell)
        local ay, by = floor((min_(Y[i0], Y[i1], Y[i2]) - y0) / cell), floor((max_(Y[i0], Y[i1], Y[i2]) - y0) / cell)
        local az, bz = floor((min_(Z[i0], Z[i1], Z[i2]) - z0) / cell), floor((max_(Z[i0], Z[i1], Z[i2]) - z0) / cell)
        if (bx - ax + 1) * (by - ay + 1) * (bz - az + 1) > 27 then
            big[#big + 1] = t           -- a triangle across many cells: looked at every time
        else
            for cx = ax, bx do
                for cy = ay, by do
                    for cz = az, bz do
                        local key = cx + cy * 1024 + cz * 1048576
                        local l = cells[key]
                        if l then l[#l + 1] = t else cells[key] = { t } end
                    end
                end
            end
        end
        if yield and t % 6000 == 1 then yield() end
    end
    return { X = X, Y = Y, Z = Z, T = T, cells = cells, big = big, x0 = x0, y0 = y0, z0 = z0,
             x1 = x1, y1 = y1, z1 = z1, cell = cell }
end

-- fn(t) for every triangle whose cells the box touches, each once
local function each_near(gg, ax, ay, az, bx, by, bz, fn)
    local cell, cells = gg.cell, gg.cells
    local cx0, cx1 = max_(floor((ax - gg.x0) / cell), 0), min_(floor((bx - gg.x0) / cell), 1023)
    local cy0, cy1 = max_(floor((ay - gg.y0) / cell), 0), min_(floor((by - gg.y0) / cell), 1023)
    local cz0, cz1 = max_(floor((az - gg.z0) / cell), 0), min_(floor((bz - gg.z0) / cell), 1023)
    local seen = {}
    for cx = cx0, cx1 do
        for cy = cy0, cy1 do
            for cz = cz0, cz1 do
                local l = cells[cx + cy * 1024 + cz * 1048576]
                if l then
                    for k = 1, #l do
                        local t = l[k]
                        if not seen[t] then
                            seen[t] = true
                            fn(t)
                        end
                    end
                end
            end
        end
    end
    local big = gg.big
    for k = 1, #big do fn(big[k]) end
end

-- nearest triangle of the group a ray meets within tmax (group space): the distance and the triangle. The ray
-- walks the cells it crosses, nearest first, and stops at the first cell that holds a hit inside it.
local function ray_group(gg, ox, oy, oz, dx, dy, dz, tmax)
    local ex, ey, ez = ox + dx * tmax, oy + dy * tmax, oz + dz * tmax
    if max_(ox, ex) < gg.x0 or min_(ox, ex) > gg.x1 or max_(oy, ey) < gg.y0 or min_(oy, ey) > gg.y1
       or max_(oz, ez) < gg.z0 or min_(oz, ez) > gg.z1 then return nil end
    local X, Y, Z, T = gg.X, gg.Y, gg.Z, gg.T
    local best, bt
    local function try(t)
        local i0, i1, i2 = T[t], T[t + 1], T[t + 2]
        local ax, ay, az = X[i0], Y[i0], Z[i0]
        local e1x, e1y, e1z = X[i1] - ax, Y[i1] - ay, Z[i1] - az
        local e2x, e2y, e2z = X[i2] - ax, Y[i2] - ay, Z[i2] - az
        local hx, hy, hz = dy * e2z - dz * e2y, dz * e2x - dx * e2z, dx * e2y - dy * e2x
        local det = e1x * hx + e1y * hy + e1z * hz
        if det > 1e-9 or det < -1e-9 then
            local inv = 1 / det
            local tx, ty, tz = ox - ax, oy - ay, oz - az
            local u = (tx * hx + ty * hy + tz * hz) * inv
            if u >= 0 and u <= 1 then
                local qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
                local v = (dx * qx + dy * qy + dz * qz) * inv
                if v >= 0 and u + v <= 1 then
                    local tt = (e2x * qx + e2y * qy + e2z * qz) * inv
                    if tt >= 0 and tt <= tmax and (not best or tt < best) then best, bt = tt, t end
                end
            end
        end
    end
    local big = gg.big
    for k = 1, #big do try(big[k]) end
    local cell, cells = gg.cell, gg.cells
    local px, py, pz = (ox - gg.x0) / cell, (oy - gg.y0) / cell, (oz - gg.z0) / cell
    local ix, iy, iz = floor(px), floor(py), floor(pz)
    local sx, sy, sz = dx > 0 and 1 or -1, dy > 0 and 1 or -1, dz > 0 and 1 or -1
    local huge = math.huge
    -- distance along the ray to the next cell wall on each axis, and between walls
    local nx = dx ~= 0 and ((dx > 0 and (ix + 1 - px) or (px - ix)) * cell / (dx * sx)) or huge
    local ny = dy ~= 0 and ((dy > 0 and (iy + 1 - py) or (py - iy)) * cell / (dy * sy)) or huge
    local nz = dz ~= 0 and ((dz > 0 and (iz + 1 - pz) or (pz - iz)) * cell / (dz * sz)) or huge
    local wx = dx ~= 0 and cell / (dx * sx) or huge
    local wy = dy ~= 0 and cell / (dy * sy) or huge
    local wz = dz ~= 0 and cell / (dz * sz) or huge
    local t_in = 0
    while t_in <= tmax do
        local t_out = min_(nx, ny, nz)
        if ix >= 0 and ix < 1024 and iy >= 0 and iy < 1024 and iz >= 0 and iz < 1024 then
            local l = cells[ix + iy * 1024 + iz * 1048576]
            if l then
                for k = 1, #l do try(l[k]) end
                if best and best <= t_out then break end        -- nothing nearer can be in the cells further on
            end
        end
        if nx <= ny and nx <= nz then ix, nx = ix + sx, nx + wx
        elseif ny <= nz then iy, ny = iy + sy, ny + wy
        else iz, nz = iz + sz, nz + wz end
        t_in = t_out
    end
    return best, bt
end

-- the part of a polygon (u, v centred on the mark, then the position) with sign * coordinate <= 0.5
local function clip(poly, axis, sign)
    local out, n = {}, #poly
    for i = 1, n do
        local a, b = poly[i], poly[i % n + 1]
        local da, db = sign * a[axis] - 0.5, sign * b[axis] - 0.5
        if da <= 0 then out[#out + 1] = a end
        if (da < 0 and db > 0) or (da > 0 and db < 0) then
            local t = da / (da - db)
            out[#out + 1] = { a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t,
                              a[4] + (b[4] - a[4]) * t, a[5] + (b[5] - a[5]) * t }
        end
    end
    return out
end

-- the triangles of the group under a square of side `size` centred on q (normal n), cut to the square:
-- a flat list of vertices { u, v, x, y, z }, three per triangle
local function mark_triangles(gg, qx, qy, qz, nx, ny, nz, size)
    local X, Y, Z, T = gg.X, gg.Y, gg.Z, gg.T
    local ux, uy, uz = 0, 0, 1
    if nz > 0.9 or nz < -0.9 then ux, uy, uz = 1, 0, 0 end
    local rx, ry, rz = uy * nz - uz * ny, uz * nx - ux * nz, ux * ny - uy * nx
    local rl = math.sqrt(rx * rx + ry * ry + rz * rz)
    rx, ry, rz = rx / rl, ry / rl, rz / rl
    local vx, vy, vz = ny * rz - nz * ry, nz * rx - nx * rz, nx * ry - ny * rx
    local depth = size * 0.6
    local out = {}
    local reach = size * 0.75           -- half the square's diagonal, with room
    each_near(gg, qx - reach, qy - reach, qz - reach, qx + reach, qy + reach, qz + reach, function(t)
        local i0, i1, i2 = T[t], T[t + 1], T[t + 2]
        local x0, y0, z0 = X[i0] - qx, Y[i0] - qy, Z[i0] - qz
        local x1, y1, z1 = X[i1] - qx, Y[i1] - qy, Z[i1] - qz
        local x2, y2, z2 = X[i2] - qx, Y[i2] - qy, Z[i2] - qz
        local d0, d1, d2 = x0 * nx + y0 * ny + z0 * nz, x1 * nx + y1 * ny + z1 * nz, x2 * nx + y2 * ny + z2 * nz
        if not ((d0 > -depth and d0 < depth) or (d1 > -depth and d1 < depth) or (d2 > -depth and d2 < depth)) then return end
        local u0, u1, u2 = (x0 * rx + y0 * ry + z0 * rz) / size, (x1 * rx + y1 * ry + z1 * rz) / size, (x2 * rx + y2 * ry + z2 * rz) / size
        if min_(u0, u1, u2) >= 0.5 or max_(u0, u1, u2) <= -0.5 then return end
        local v0, v1, v2 = (x0 * vx + y0 * vy + z0 * vz) / size, (x1 * vx + y1 * vy + z1 * vz) / size, (x2 * vx + y2 * vy + z2 * vz) / size
        if min_(v0, v1, v2) >= 0.5 or max_(v0, v1, v2) <= -0.5 then return end
        -- facing the mark (either winding: strips alternate), not edge-on
        local e1x, e1y, e1z = x1 - x0, y1 - y0, z1 - z0
        local e2x, e2y, e2z = x2 - x0, y2 - y0, z2 - z0
        local tx, ty, tz = e1y * e2z - e1z * e2y, e1z * e2x - e1x * e2z, e1x * e2y - e1y * e2x
        local tl = math.sqrt(tx * tx + ty * ty + tz * tz)
        if tl == 0 or math.abs(tx * nx + ty * ny + tz * nz) / tl <= 0.35 then return end
        local poly = { { u0, v0, X[i0], Y[i0], Z[i0] }, { u1, v1, X[i1], Y[i1], Z[i1] }, { u2, v2, X[i2], Y[i2], Z[i2] } }
        poly = clip(clip(clip(clip(poly, 1, 1), 1, -1), 2, 1), 2, -1)
        for k = 2, #poly - 1 do
            out[#out + 1] = poly[1]; out[#out + 1] = poly[k]; out[#out + 1] = poly[k + 1]
        end
    end)
    return out
end

-- the triangles of every group of a stream, read from its file again (only the positions and the strips)
local function load_geometry(st, yield)
    local raw = file.Read(st.path, "GAME")
    if not raw or sub(raw, 1, 4) ~= "GMOS" or byte(raw, 5) ~= 6 then error("missing or bad stream " .. tostring(st.path)) end
    local blob = util.Decompress(sub(raw, 6))
    if not blob then error("decompress failed " .. st.path) end
    raw = nil
    yield()
    local info, d = decode(blob, yield)
    blob = nil
    local groups = {}
    local vbase, ibase, cp = 0, 0, 0
    local pref, px, py, pz, idx = d.pref, d.px, d.py, d.pz, d.idx
    local POS_Q = info.posq
    for gi, g in ipairs(info.groups) do
        local X, Y, Z = {}, {}, {}
        local x, y, z = 0, 0, 0
        for i = 1, g.nv do
            local r = pref[vbase + i]
            if r == 0 then
                cp = cp + 1
                x = x + px[cp]; y = y + py[cp]; z = z + pz[cp]
                X[i], Y[i], Z[i] = x * POS_Q, y * POS_Q, z * POS_Q
            else
                X[i], Y[i], Z[i] = X[i - r], Y[i - r], Z[i - r]
            end
        end
        local C, hwm = {}, 0
        for c = 1, g.nidx do
            local code = idx[ibase + c]
            if code == 0 then hwm = hwm + 1; C[c] = hwm else C[c] = hwm + 1 - code end
        end
        yield()
        groups[gi] = index_group(X, Y, Z, C, g.chunks_len, yield)
        vbase = vbase + g.nv
        ibase = ibase + g.nidx
    end
    return groups
end

local function drop_mark(ent, k)
    local m = table.remove(ent.gmo_decals, k)
    if m then
        m.mesh:Destroy()
        for j = 1, #marks_all do
            if marks_all[j] == m then table.remove(marks_all, j) break end
        end
    end
end
local function clear_marks(ent)
    while ent.gmo_decals and #ent.gmo_decals > 0 do drop_mark(ent, 1) end
end

-- hit: the bullet's path in the space of each group that was drawn when it arrived ({ ox, oy, oz, dx, dy, dz,
-- scale } per group number), and what it hit (MAT_*)
local function make_mark(ent, st, hit)
    local best
    for gi, r in pairs(hit.rays) do
        local gg = st.geo[gi]
        if gg then
            local t, tri = ray_group(gg, r[1], r[2], r[3], r[4], r[5], r[6], 2 * MARK_REACH * r[7])
            if t and (not best or t / r[7] < best.t) then best = { t = t / r[7], tl = t, gi = gi, r = r, tri = tri } end
        end
    end
    if not best then return end         -- no surface near the hit: the game would not have marked it either
    local gg, r, g = st.geo[best.gi], best.r, st.info.groups[best.gi]
    local X, Y, Z, T = gg.X, gg.Y, gg.Z, gg.T
    local qx, qy, qz = r[1] + r[4] * best.tl, r[2] + r[5] * best.tl, r[3] + r[6] * best.tl
    local i0, i1, i2 = T[best.tri], T[best.tri + 1], T[best.tri + 2]
    local e1x, e1y, e1z = X[i1] - X[i0], Y[i1] - Y[i0], Z[i1] - Z[i0]
    local e2x, e2y, e2z = X[i2] - X[i0], Y[i2] - Y[i0], Z[i2] - Z[i0]
    local nx, ny, nz = e1y * e2z - e1z * e2y, e1z * e2x - e1x * e2z, e1x * e2y - e1y * e2x
    local nl = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nl == 0 then return end
    nx, ny, nz = nx / nl, ny / nl, nz / nl
    if nx * r[4] + ny * r[5] + nz * r[6] > 0 then nx, ny, nz = -nx, -ny, -nz end   -- towards where the bullet came from
    local name = util.DecalMaterial(MARK_NAMES[hit.mattype] or "Impact.Concrete")
    if not name or name == "" then name = util.DecalMaterial("Impact.Concrete") end
    local mat = name and name ~= "" and Material(name)
    if not mat or mat:IsError() then return end
    -- the game's decals are cut-outs of one sheet ("Subrect"); for models they name a material of their own
    local own = mat:GetString("$modelmaterial")
    if own and own ~= "" then
        local m2 = Material(own)
        if m2 and not m2:IsError() then mat = m2 end
    end
    local size = math.Clamp(mat:Width() * (mat:GetFloat("$decalscale") or 0.1), 3, 12) * r[7]
    local tris = mark_triangles(gg, qx, qy, qz, nx, ny, nz, size)
    if #tris == 0 then return end
    local obj = Mesh(mat)
    local vp, vn = Vector(), Vector(nx, ny, nz)
    mesh.Begin(obj, MATERIAL_TRIANGLES, #tris / 3 * 2)
    for pass = 1, 2 do                  -- both windings: whichever the game takes as the front shows
        for k = 1, #tris, 3 do
            for j = 0, 2 do
                local p = tris[k + (pass == 1 and j or 2 - j)]
                vp:SetUnpacked(p[3] + nx * 0.03, p[4] + ny * 0.03, p[5] + nz * 0.03)
                mesh.Position(vp)
                mesh.Normal(vn)
                mesh.TexCoord(0, p[1] + 0.5, p[2] + 0.5)
                mesh.Color(255, 255, 255, 255)
                mesh.AdvanceVertex()
            end
        end
    end
    mesh.End()
    local marks = ent.gmo_decals
    if not marks then
        marks = {}
        ent.gmo_decals = marks
        ent:CallOnRemove("gmo_stream_marks", clear_marks)
    end
    if #marks >= MARK_MAX then drop_mark(ent, 1) end
    local m = { mesh = obj, mat = mat, bone = g.bone, ptb = g.ptb, b = g.b, m = g.m, ent = ent }
    marks[#marks + 1] = m
    marks_all[#marks_all + 1] = m
    if #marks_all > MARK_TOTAL then
        local oldest = marks_all[1]
        local list = IsValid(oldest.ent) and oldest.ent.gmo_decals
        local at
        for k = 1, #(list or {}) do if list[k] == oldest then at = k break end end
        if at then drop_mark(oldest.ent, at) else table.remove(marks_all, 1) end
    end
    G.marks_made = (G.marks_made or 0) + 1
end

-- pos, dir: where the bullet stopped and which way it flew (world); mattype: MAT_* of what it hit. The path is
-- taken into the space of the groups now, while the entity is where it was hit; the mark itself is made when
-- the triangles of the model are in memory (Think hook below)
function G.AddDecal(ent, pos, dir, mattype)
    if not cv_decals:GetBool() or not cv_enable:GetBool() or not IsValid(ent) or #mark_queue >= 64 then return end
    local mdl = ent:GetModel()
    local st = mdl and lookup(mdl)
    local cache = ent.gmo_cache
    if not st or not st.ready or st.geo_failed or not cache or not cache.lists then return end
    ent:SetupBones()
    local start = pos - dir * MARK_REACH
    local by_bone, rays, any = {}, {}, false
    for _, pass in ipairs({ false, true }) do
        for _, byb in ipairs(cache.lists[pass] or {}) do
            for _, it in ipairs(byb.items) do
                local g = it.g
                if g and g.gi and not rays[g.gi] then
                    local r = by_bone[g.bone]
                    if r == nil then
                        r = false
                        local bm = ent:GetBoneMatrix(g.bone)
                        local inv = bm and (bm * g.ptb):GetInverse()
                        if inv then
                            local o = inv * start
                            local d = inv * (start + dir) - o
                            local len = d:Length()
                            if len > 0 then r = { o.x, o.y, o.z, d.x / len, d.y / len, d.z / len, len } end
                        end
                        by_bone[g.bone] = r
                    end
                    if r then
                        rays[g.gi] = r
                        any = true
                    end
                end
            end
        end
    end
    if any then mark_queue[#mark_queue + 1] = { ent = ent, st = st, rays = rays, mattype = mattype, t = RealTime() } end
end

hook.Add("Think", "gmo_stream_marks", function()
    if #mark_queue == 0 then return end
    mark_deadline = SysTime() + 0.002
    local k = 1
    while k <= #mark_queue do
        local hit = mark_queue[k]
        local st = hit.st
        if not IsValid(hit.ent) or RealTime() - hit.t > 8 or st.geo_failed then
            table.remove(mark_queue, k)
        elseif st.geo then
            table.remove(mark_queue, k)
            st.geo_used = RealTime()
            local t1 = SysTime()
            local ok, err = pcall(make_mark, hit.ent, st, hit)
            if not ok then print("[GMOS] mark: " .. tostring(err)) end
            if SysTime() - t1 > (G.mark_worst or 0) then G.mark_worst = SysTime() - t1 end   -- slowest mark, seconds
            if SysTime() > mark_deadline then return end
        else
            k = k + 1
        end
    end
    -- the triangles of the first model waiting, a little per frame
    local hit = mark_queue[1]
    local st = hit and hit.st
    if not st then return end
    if not st.geo_co then
        st.geo_co = coroutine.create(function()
            return load_geometry(st, function() if SysTime() > mark_deadline then coroutine.yield() end end)
        end)
    end
    local t1 = SysTime()
    local ok, res = coroutine.resume(st.geo_co)
    G.mark_load = (G.mark_load or 0) + (SysTime() - t1)     -- time spent reading triangles, seconds
    G.mark_load_frames = (G.mark_load_frames or 0) + 1
    if not ok then
        st.geo_failed, st.geo_co = true, nil
        print("[GMOS] mark: " .. tostring(res))
    elseif coroutine.status(st.geo_co) == "dead" then
        st.geo, st.geo_used, st.geo_co = res, RealTime(), nil
    end
end)

net.Receive("gmo_stream_decal", function()
    local ent, pos, dir, mattype = net.ReadEntity(), net.ReadVector(), net.ReadNormal(), net.ReadUInt(8)
    local ok, err = pcall(G.AddDecal, ent, pos, dir, mattype)
    if not ok then print("[GMOS] mark: " .. tostring(err)) end
end)
-- impacts Lua itself asks for on this client (vehicle bases with their own bullets) go the same way
do
    local f = G.ORIG_Effect or (old and old.ORIG_Effect) or util.Effect
    G.ORIG_Effect = f
    if f then
        util.Effect = function(name, data, ...)
            if name == "Impact" and data and cv_decals:GetBool() then
                pcall(function()
                    local ent = data:GetEntity()
                    if not IsValid(ent) then return end
                    local dir = data:GetOrigin() - data:GetStart()
                    if dir:LengthSqr() == 0 then return end
                    dir:Normalize()
                    local sd = util.GetSurfaceData(data:GetSurfaceProp() or 0)
                    G.AddDecal(ent, data:GetOrigin(), dir, sd and sd.material or 0)
                end)
            end
            return f(name, data, ...)
        end
    end
end
do
    local f = G.ORIG_RemoveAllDecals or (old and old.ORIG_RemoveAllDecals) or meta.RemoveAllDecals
    G.ORIG_RemoveAllDecals = f
    if f then
        meta.RemoveAllDecals = function(self, ...)
            clear_marks(self)
            return f(self, ...)
        end
    end
end
G.marks = { index_group = index_group, ray_group = ray_group, cut = mark_triangles, load = load_geometry }

function meta:DrawModel(flags)
    if G.priming or not cv_enable:GetBool() then return ORIG(self, flags) end
    local mdl = self:GetModel()
    if not mdl then return ORIG(self, flags) end
    local st = lookup(mdl)
    if not st then return ORIG(self, flags) end
    if not st.ready then
        step(st)
        return ORIG(self, flags)
    end
    if EyePos():DistToSqr(self:GetPos()) > cv_far:GetFloat() ^ 2 then return ORIG(self, flags) end
    st.last_draw = RealTime()
    return draw_stream(self, flags, st)
end

-- free the IMeshes of streams nobody drew for 5 minutes (decoded again when needed)
timer.Create("gmo_stream_gc", 30, 0, function()
    local now = RealTime()
    for mdl, st in pairs(states) do
        if st and st.geo and now - (st.geo_used or now) > MARK_KEEP then st.geo = nil end
        if st and st.ready and now - (st.last_draw or now) > 300 then
            for _, g in ipairs(st.info.groups) do
                for _, c in ipairs(g.chunks or {}) do c:Destroy() end
                g.chunks = {}
            end
            states[mdl] = nil
            G.reset_lookup()
        end
    end
end)

-- view models: the engine draws them itself, without Lua's DrawModel. While the current model is streamed and
-- decoded, a RenderOverride draws the stream instead of the stand-in mesh; GMod still runs PreDrawViewModel,
-- ViewModelDrawn (attachments) and PostDrawViewModel (hands) around it, once. Set or cleared every frame,
-- before the draw: weapon switches change the model of the same view-model entity.
local function vm_override(self, flags)
    local st = self.gmo_vm_st
    if not st or not st.ready then return end
    st.last_draw = RealTime()
    draw_stream(self, flags, st, true)
end
hook.Add("PreDrawViewModel", "gmo_stream_vm", function(vm)
    if not IsValid(vm) then return end
    local mdl = cv_enable:GetBool() and vm:GetModel()
    local st = mdl and mdl ~= "" and lookup(mdl) or nil
    if st and not st.ready then step(st) end
    if st and st.ready then
        vm.gmo_vm_st = st
        if vm.RenderOverride == nil then vm.RenderOverride = vm_override end
    elseif vm.RenderOverride == vm_override then
        vm.RenderOverride = nil
    end
end)

-- decode the view models of the weapons the player carries before they are drawn (one slice per frame):
-- otherwise the stand-in mesh shows in the hand for a moment at the first draw
local warm_next = 0
hook.Add("Think", "gmo_stream_warm", function()
    local now = RealTime()
    if now < warm_next or not cv_enable:GetBool() then return end
    local ply = LocalPlayer()
    if not IsValid(ply) then return end
    for _, w in ipairs(ply:GetWeapons()) do
        local m = w.GetWeaponViewModel and w:GetWeaponViewModel()
        local st = m and m ~= "" and lookup(m) or nil
        if st and not st.ready and not st.failed then
            step(st)
            return
        end
    end
    warm_next = now + 1
end)

-- entities the engine draws without Lua (props, gibs): route them through DrawModel
hook.Add("OnEntityCreated", "gmo_stream", function(ent)
    timer.Simple(0, function()
        if not IsValid(ent) or ent.RenderOverride then return end
        local mdl = ent:GetModel()
        if not mdl or not G.manifest[string.lower(mdl)] then return end
        if ent:IsScripted() then return end   -- scripted entities draw through their Draw -> DrawModel
        ent.RenderOverride = function(self, flags) self:DrawModel(flags) end
    end)
end)

if GMOS_PENDING_MANIFEST then G.AddManifest(GMOS_PENDING_MANIFEST) end
GMOS_PENDING_MANIFEST = nil
