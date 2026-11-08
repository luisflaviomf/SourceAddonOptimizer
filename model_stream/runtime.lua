-- GMod Addon Optimizer: compressed model geometry ("GMOS" streams) drawn with IMeshes.
-- The addon ships a coarse model (physics, shadows, far distance, fallback) and the full geometry
-- compressed in data_static/. This file decodes a stream the first time its model is drawn
-- (a few milliseconds per frame) and from then on draws the full geometry in place of the model.
-- Console: gmo_stream 0 draws the shipped models only (compare / disable), gmo_stream_far sets the
-- distance beyond which the coarse model is drawn. Streams unused for 5 minutes are freed.
if SERVER then return end

local VERSION = 6
if GMOS and (GMOS.VERSION or 0) >= VERSION then
    -- an equal or newer runtime is already loaded: only merge this addon's manifest
    if GMOS_PENDING_MANIFEST then GMOS.AddManifest(GMOS_PENDING_MANIFEST) end
    GMOS_PENDING_MANIFEST = nil
    return
end

local old = GMOS
GMOS = { VERSION = VERSION, manifest = old and old.manifest or {} }
local G = GMOS

local cv_enable = CreateClientConVar("gmo_stream", "1", true, false, "Draw optimized model geometry streams")
local cv_far = CreateClientConVar("gmo_stream_far", "4000", true, false, "Beyond this distance the coarse model is drawn")
local cv_budget = CreateClientConVar("gmo_stream_budget_ms", "4", true, false, "Decode time per frame")

local byte, sub, find, floor, ldexp = string.byte, string.sub, string.find, math.floor, math.ldexp
local band = bit.band

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
    for i, name in pairs(info.mats) do mats[i] = Material(name) end
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
        -- one strip IMesh per chunk (at most 65535 vertices each)
        local chunks = {}
        local c0 = 0
        for _, len in ipairs(g.chunks_len) do
            local obj = Mesh()
            mesh.Begin(obj, MATERIAL_TRIANGLE_STRIP, len - 2)
            for c = c0 + 1, c0 + len do
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
            c0 = c0 + len
            yield()
        end
        g.chunks = chunks
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
                byb.items[#byb.items + 1] = { mat = m, chunks = g.chunks, area = g.area or 0, bone = g.bone, ptb = g.ptb,
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

local function draw_list(ent, list, bms)
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
                if it.refract then render.UpdateRefractTexture() end
                render.SetMaterial(it.mat)
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

local function draw_stream(ent, flags, st)
    if flags and band(flags, SHADOW_FLAGS) ~= 0 then return ORIG(ent, flags) end
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
    else
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
    render.SetColorModulation(cr / 255, cg / 255, cb / 255)
    render.SetBlend(ca / 255)
    if not only_tr then
        local list = cache.lists[false]
        draw_list(ent, list, fb.m)
        if not cache.flash then
            cache.flash = function() draw_list(ent, cache.lists.flash, cache.fb.m) end
        end
        if lit_by_flashlight(ent) then render.RenderFlashlights(cache.flash) end
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
function meta:DrawModel(flags)
    if G.priming or not cv_enable:GetBool() then return ORIG(self, flags) end
    local mdl = self:GetModel()
    if not mdl then return ORIG(self, flags) end
    local st = by_raw[mdl]
    if st == nil then
        st = state_for(string.lower(mdl))
        by_raw[mdl] = st
    end
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
