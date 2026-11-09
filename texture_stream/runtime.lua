-- GMod Addon Optimizer texture stream runtime (GMOT core). Shared by every addon that ships it: the newest
-- core wins and keeps the manifests registered by older ones.
--
-- An optimized addon stores its textures as GMOT blobs in data_static/gmot/<slug>/ and keeps a tiny
-- placeholder VTF at each original path. When a model, material or Lua call needs one of those textures,
-- this runtime decodes the blob (decoder.lua) into a DXT VTF, writes a GMA with it to data/gmot/, mounts it
-- and points the materials at materials/gmot/<slug>/<i>.vtf. Big textures get a 512 px preview first.
-- The GMAs stay mounted for the session (deleted on the next start); after a map change the textures
-- already mounted are reused without decoding again.

local O = GMOT.Orig or {}
GMOT.Orig = O
local IMAT = FindMetaTable("IMaterial")
local ENT = FindMetaTable("Entity")
O.Material = O.Material or Material
O.CreateMaterial = O.CreateMaterial or CreateMaterial
O.SetTexture = O.SetTexture or IMAT.SetTexture
O.SetMaterial = O.SetMaterial or ENT.SetMaterial
O.SetSubMaterial = O.SetSubMaterial or ENT.SetSubMaterial
O.SetModel = O.SetModel or ENT.SetModel
O.ClientsideModel = O.ClientsideModel or ClientsideModel
O.CreateClientProp = O.CreateClientProp or ents.CreateClientProp

local PREVIEW = 512             -- bigger textures get a preview first (the encoder's preview size)
local DIR = "gmot"              -- data/gmot: GMAs of this session
local budget = CreateClientConVar("gmot_budget", "6", true, false,
    "Texture stream: at most this many milliseconds of decoding per frame", 1, 50)
local prefetch = CreateClientConVar("gmot_prefetch", "1", true, false,
    "Texture stream: decode in the background the textures no model shows (effects first, then the rest)", 0, 1)

local lower, gsub, SysTime = string.lower, string.gsub, SysTime
local function norm(s)
    s = lower((gsub(s, "\\", "/")))
    s = gsub(s, "^/+", "")
    s = gsub(s, "^materials/", "")
    return (gsub(s, "%.vm?[tf]$", ""))
end
local function norm_model(s) return lower((gsub(s, "\\", "/"))) end

GMOT.Manifests = GMOT.Manifests or {}
local BYREF, BYMAT, BYMODEL = {}, {}, {}
local ENTRIES = {}
local SEEN_MODEL, SEEN_MAT = {}, {}
local TARGETS = {}              -- materials pointed at a stream texture by Lua: key -> { m, param, entry }
local previews, queue, applies = {}, {}, {}
local later = {}                -- background prefetch (manifest classes 1-3), filled after the map loads
local stats = GMOT.Stats or { decoded = 0, reused = 0, mounts = 0, decode_s = 0, worst = 0, failed = 0 }
GMOT.Stats = stats

local function want(e)
    if e.state then return end
    e.state = 1
    e.t0 = SysTime()
    if file.Exists("materials/" .. e.name .. ".vtf", "GAME") then
        -- mounted earlier in this game session (map change): use it as it is
        applies[#applies + 1] = e
        stats.reused = stats.reused + 1
        return
    end
    if e.side > PREVIEW then previews[#previews + 1] = e end
    queue[#queue + 1] = e
end

local function want_list(l) for k = 1, #l do if not l[k].state then want(l[k]) end end end

local function want_material(name)
    if type(name) ~= "string" or name == "" or SEEN_MAT[name] then return end
    SEEN_MAT[name] = true
    local k = norm(name)
    local l = BYMAT[k]
    if l then want_list(l) end
    local e = BYREF[k]
    if e then
        e.self_mat = true           -- a material named like the texture (no VMT: the engine makes one)
        if not e.state then want(e) end
    end
end

-- model: path as the game gives it; ent: an entity using it (for models no manifest names, its materials)
local function want_model(model, ent)
    if type(model) ~= "string" or model == "" or SEEN_MODEL[model] then return end
    if string.find(model, "%.vmt$") or string.find(model, "%.spr$") then  -- env_sprite & co: a material
        SEEN_MODEL[model] = true
        want_material(model)
        return
    end
    local l = BYMODEL[norm_model(model)]
    if l then
        SEEN_MODEL[model] = true
        want_list(l)
    elseif ent and ent.GetMaterials then
        SEEN_MODEL[model] = true
        for _, m in ipairs(ent:GetMaterials() or {}) do want_material(m) end
    end
end

local function texture_is(m, param, e)
    local t = m:GetTexture(param)
    if not t then return false end
    local cur = norm(t:GetName() or "")
    return cur == e.ref or cur == e.name_p or cur == e.name
end

local function apply(e, full)
    local name = full and e.name or e.name_p
    local refs = e.refs
    for j = 1, #refs, 2 do
        local m = O.Material(refs[j])
        -- only where the material still shows this texture (Lua may have swapped it meanwhile)
        if m and not m:IsError() and texture_is(m, refs[j + 1], e) then O.SetTexture(m, refs[j + 1], name) end
    end
    if e.self_mat then
        local m = O.Material(e.ref)
        if m and not m:IsError() and texture_is(m, "$basetexture", e) then O.SetTexture(m, "$basetexture", name) end
    end
    for _, t in pairs(TARGETS) do
        if t[3] == e then O.SetTexture(t[1], t[2], name) end
    end
    e.state = full and 3 or 2
    if full then e.ready = SysTime() - (e.t0 or SysTime()) end
end

local function target(m, param, e)
    if not (m and m.GetName) then return end
    TARGETS[m:GetName() .. "\0" .. lower(param)] = { m, param, e }
    if not e.state then want(e) end
end

-- ------------------------------------------------------------------ GMA in data/, mounted
local function u32(n)
    return string.char(n % 256, math.floor(n / 256) % 256, math.floor(n / 65536) % 256, math.floor(n / 16777216) % 256)
end
local function gma(files)
    local parts = { "GMAD", string.char(3), u32(0), u32(0), u32(os.time()), u32(0), "\0", "gmot\0", "{}\0", "gmot\0", u32(1) }
    for k, f in ipairs(files) do parts[#parts + 1] = u32(k) .. f.name .. "\0" .. u32(#f.data) .. u32(0) .. u32(0) end
    parts[#parts + 1] = u32(0)
    for _, f in ipairs(files) do parts[#parts + 1] = f.data end
    parts[#parts + 1] = u32(0)
    return table.concat(parts)
end

local pending, pending_bytes, pending_t0, batch = {}, 0, 0, 0
local pending_urgent = false    -- something in `pending` is waited for on screen
local idle = true               -- the worker waits for work (not halfway through a batch)
local background = false        -- the decode in progress is a prefetch: dropped when the screen needs something
local PREEMPT = "gmot: preempted"
local function flush()
    if #pending == 0 then return end
    batch = batch + 1
    local files = {}
    for k, p in ipairs(pending) do
        files[k] = { name = "materials/" .. (p.full and p.e.name or p.e.name_p) .. ".vtf", data = p.vtf }
    end
    local done = pending
    pending, pending_bytes, pending_urgent = {}, 0, false
    local path = DIR .. "/s" .. os.time() .. "_" .. batch .. ".gma"
    local tmp = path .. ".tmp.dat"     -- file.Write only takes some extensions; renamed below
    file.CreateDir(DIR)
    local h = file.Open(tmp, "wb", "DATA")
    if not h then
        for _, p in ipairs(done) do p.e.state = -1 end
        stats.failed = stats.failed + #done
        return
    end
    h:Write(gma(files))
    h:Close()
    files = nil
    file.Delete(path)
    file.Rename(tmp, path)
    local ok = game.MountGMA("data/" .. path)
    stats.mounts = stats.mounts + 1
    if not ok then
        print("[GMOT] could not mount " .. path)
        for _, p in ipairs(done) do p.e.state = -1 end
        stats.failed = stats.failed + #done
        return
    end
    for _, p in ipairs(done) do
        coroutine.yield()               -- one texture load per frame
        if p.full or (p.e.state or 0) < 2 then apply(p.e, p.full) end
    end
end

local function work()
    idle = false
    while true do
        local e, preview, urgent = nil, false, true
        if #applies > 0 then
            e = table.remove(applies, 1)
            coroutine.yield()
            apply(e, true)
            e = nil
        elseif #previews > 0 then
            e, preview = table.remove(previews, 1), true
        elseif #queue > 0 then
            e = table.remove(queue, 1)
        elseif #later > 0 then
            e, urgent = table.remove(later, 1), false
            if e.state then
                e = nil
            else
                e.state = 1
                e.t0 = SysTime()
            end
        end
        if e and ((preview and (e.state or 0) >= 2) or e.state == 3 or e.state == -1) then
            e = nil                     -- nothing left to do for it
        elseif e then
            local data = file.Read(e.blob, "GAME")
            local t0 = SysTime()
            local ok, vtf = false, "blob not found"
            background = not urgent
            if data then ok, vtf = pcall(GMOT.Decode, data, preview) end
            background = false
            data = nil
            stats.decode_s = stats.decode_s + (SysTime() - t0)
            if not ok and vtf == PREEMPT then
                e.state = nil           -- back to the prefetch list, after what the screen waits for
                table.insert(later, 1, e)
                stats.preempted = (stats.preempted or 0) + 1
            elseif ok and vtf then
                stats.decoded = stats.decoded + 1
                if #pending == 0 then pending_t0 = SysTime() end
                pending[#pending + 1] = { e = e, vtf = vtf, full = not preview, urgent = urgent }
                if urgent then pending_urgent = true end
                pending_bytes = pending_bytes + #vtf
            else
                stats.failed = stats.failed + 1
                e.state = -1
                print("[GMOT] " .. e.slug .. " texture " .. e.i .. " (" .. e.ref .. ") failed: " .. tostring(vtf))
            end
        end
        -- previews go up as soon as the last one is decoded (before any full texture), what the screen waits
        -- for as soon as its queue is empty; otherwise (and for the background prefetch) in batches
        if #pending > 0 then
            local age = SysTime() - pending_t0
            local busy = #previews > 0
            if (not busy and pending_urgent and (#queue == 0 or not pending[#pending].full))
                    or (not busy and #queue == 0 and #later == 0)
                    or pending_bytes >= (busy and 3000000 or 6000000) or age > (busy and 1.5 or 3) then
                flush()
            end
        end
        if not e and #previews == 0 and #queue == 0 and #applies == 0 and #later == 0 then
            idle = true
            coroutine.yield()
            idle = false
        end
    end
end

-- ------------------------------------------------------------------ what is on screen
local snap, si, next_inv = {}, 1, 0
local NSUB = {}
local function scan(now)
    local ply = LocalPlayer()
    if IsValid(ply) then
        local w = ply:GetActiveWeapon()
        if IsValid(w) then
            want_model(w:GetModel(), w)
            if isstring(w.ViewModel) then want_model(w.ViewModel) end
        end
        local vm = ply:GetViewModel()
        if IsValid(vm) then want_model(vm:GetModel(), vm) end
        local hands = ply.GetHands and ply:GetHands()
        if IsValid(hands) then want_model(hands:GetModel(), hands) end
        if now >= next_inv then
            next_inv = now + 0.5
            for _, ww in ipairs(ply:GetWeapons()) do
                if IsValid(ww) then
                    want_model(ww:GetModel(), ww)
                    if isstring(ww.ViewModel) then want_model(ww.ViewModel) end
                    if isstring(ww.WorldModel) then want_model(ww.WorldModel) end
                end
            end
        end
    end
    -- a slice of the other entities per frame (models, material overrides, sub-materials set by the server)
    if si > #snap then snap, si = ents.GetAll(), 1 end
    local last = math.min(#snap, si + 63)
    for k = si, last do
        local e = snap[k]
        if IsValid(e) then
            local m = e:GetModel()
            if m then
                want_model(m, e)
                local mo = e:GetMaterial()
                if mo ~= "" then want_material(mo) end
                local n = NSUB[m]
                if not n then
                    n = math.min(#(e:GetMaterials() or {}), 32)
                    NSUB[m] = n
                end
                for s = 0, n - 1 do
                    local sm = e:GetSubMaterial(s)
                    if sm ~= "" then want_material(sm) end
                end
            end
        end
    end
    si = last + 1
end

-- static props (not entities) and brush materials of the map, read from the BSP
local function scan_map()
    local f = file.Open("maps/" .. game.GetMap() .. ".bsp", "rb", "GAME")
    if not f then return end
    if f:Read(4) ~= "VBSP" then f:Close() return end
    f:ReadLong()
    local lumps = {}
    for l = 0, 63 do lumps[l] = { f:ReadLong(), f:ReadLong() }; f:ReadLong(); f:ReadLong() end
    local tex = lumps[43]
    if tex[2] > 0 and tex[2] < 4000000 then
        f:Seek(tex[1])
        local data = f:Read(tex[2]) or ""
        if data:sub(1, 4) ~= "LZMA" then
            for name in string.gmatch(data, "[^%z]+") do want_material(name) end
        end
    end
    local game_lump = lumps[35]
    if game_lump[2] > 4 then
        f:Seek(game_lump[1])
        local n = f:ReadLong()
        for _ = 1, math.min(n or 0, 64) do
            local id = f:ReadLong()
            local flags = f:ReadUShort()
            f:ReadUShort()
            local ofs, len = f:ReadLong(), f:ReadLong()
            if id == 1936749168 and flags % 2 == 0 and len > 4 then     -- "sprp", not compressed
                local back = f:Tell()
                f:Seek(ofs)
                local count = f:ReadLong() or 0
                if count > 0 and count < 16384 then
                    for _ = 1, count do
                        local name = f:Read(128) or ""
                        want_model((string.match(name, "^[^%z]+")))
                    end
                end
                f:Seek(back)
            end
        end
    end
    f:Close()
end

-- ------------------------------------------------------------------ Lua that names textures or materials
Material = function(name, ...)
    if name and SEEN_MAT[name] == nil then want_material(name) end
    return O.Material(name, ...)
end
CreateMaterial = function(name, shader, params, ...)
    local m = O.CreateMaterial(name, shader, params, ...)
    if m and type(params) == "table" then
        for k, v in pairs(params) do
            if type(k) == "string" and type(v) == "string" then
                local e = BYREF[norm(v)]
                if e then
                    target(m, k, e)
                    if e.state == 3 or e.state == 2 then O.SetTexture(m, k, e.state == 3 and e.name or e.name_p) end
                end
            end
        end
    end
    return m
end
IMAT.SetTexture = function(self, param, tex, ...)
    if type(param) == "string" and self and self.GetName then
        local e = type(tex) == "string" and BYREF[norm(tex)] or nil
        if e then
            TARGETS[self:GetName() .. "\0" .. lower(param)] = { self, param, e }
            if e.state == 3 then tex = e.name elseif e.state == 2 then tex = e.name_p elseif not e.state then want(e) end
        elseif next(TARGETS) ~= nil then
            TARGETS[self:GetName() .. "\0" .. lower(param)] = nil
        end
    end
    return O.SetTexture(self, param, tex, ...)
end
-- per-frame material calls: each IMaterial object is looked at once (weak cache)
local SEEN_IMAT = setmetatable({}, { __mode = "k" })
local function seen_imat(m)
    if m and not SEEN_IMAT[m] and type(m) ~= "string" and type(m) ~= "number" then
        SEEN_IMAT[m] = true
        local ok, name = pcall(m.GetName, m)
        if ok and name then want_material(name) end
    end
end
for _, k in ipairs({ "SetMaterial", "MaterialOverride", "ModelMaterialOverride", "BrushMaterialOverride",
                     "WorldMaterialOverride" }) do
    local key = "render." .. k
    O[key] = O[key] or render[k]
    if O[key] then
        local f = O[key]
        render[k] = function(m, ...) if m then seen_imat(m) end return f(m, ...) end
    end
end
O["render.MaterialOverrideByIndex"] = O["render.MaterialOverrideByIndex"] or render.MaterialOverrideByIndex
if O["render.MaterialOverrideByIndex"] then
    local f = O["render.MaterialOverrideByIndex"]
    render.MaterialOverrideByIndex = function(i, m, ...) if m then seen_imat(m) end return f(i, m, ...) end
end
O["surface.SetMaterial"] = O["surface.SetMaterial"] or surface.SetMaterial
do
    local f = O["surface.SetMaterial"]
    surface.SetMaterial = function(m, ...) if m then seen_imat(m) end return f(m, ...) end
end
O["surface.GetTextureID"] = O["surface.GetTextureID"] or surface.GetTextureID
do
    local f = O["surface.GetTextureID"]
    surface.GetTextureID = function(name, ...) want_material(name) return f(name, ...) end
end

ENT.SetMaterial = function(self, mat, ...)
    if mat and mat ~= "" then want_material(mat) end
    return O.SetMaterial(self, mat, ...)
end
ENT.SetSubMaterial = function(self, i, mat, ...)
    if mat and mat ~= "" then want_material(mat) end
    return O.SetSubMaterial(self, i, mat, ...)
end
ENT.SetModel = function(self, model, ...)
    want_model(model)
    return O.SetModel(self, model, ...)
end
ClientsideModel = function(model, ...)
    want_model(model)
    return O.ClientsideModel(model, ...)
end
ents.CreateClientProp = function(model, ...)
    if model then want_model(model) end
    return O.CreateClientProp(model, ...)
end

-- ------------------------------------------------------------------ manifests
-- manifest = { slug = "...", tex = { { blob, ref, side, { vmt, param, ... }, class }, ... },
--              models = { [model] = { i, ... } } }; class: see texture_stream.classify()
local function index(man)
    local list = {}
    for i, t in ipairs(man.tex or {}) do
        local e = { slug = man.slug, i = i, blob = t[1], ref = t[2], side = t[3] or 0, refs = t[4] or {}, cls = t[5] or 0,
                    name = "gmot/" .. man.slug .. "/" .. i, name_p = "gmot/" .. man.slug .. "/" .. i .. "_p" }
        list[i] = e
        ENTRIES[#ENTRIES + 1] = e
        BYREF[e.ref] = BYREF[e.ref] or e
        for j = 1, #e.refs, 2 do
            local l = BYMAT[e.refs[j]]
            if not l then l = {}; BYMAT[e.refs[j]] = l end
            l[#l + 1] = e
        end
    end
    for model, ids in pairs(man.models or {}) do
        local l = BYMODEL[model]
        if not l then l = {}; BYMODEL[model] = l end
        for _, i in ipairs(ids) do if list[i] then l[#l + 1] = list[i] end end
    end
end

function GMOT.Register(man)
    if type(man) ~= "table" or not man.slug then return end
    for k, old in ipairs(GMOT.Manifests) do
        if old.slug == man.slug then return end     -- the same addon twice
    end
    GMOT.Manifests[#GMOT.Manifests + 1] = man
    index(man)
    SEEN_MODEL, SEEN_MAT = {}, {}
end

for _, man in ipairs(GMOT.Manifests) do index(man) end   -- registered by an older core

-- leftovers of the previous session (no longer mounted; the ones mounted now fail to delete and stay)
for _, f in ipairs(file.Find(DIR .. "/*", "DATA") or {}) do file.Delete(DIR .. "/" .. f) end

local co
hook.Add("Think", "gmot_stream", function()
    local now = SysTime()
    scan(now)
    local waiting = #queue > 0 or #previews > 0 or #applies > 0
    if idle and not waiting and #pending == 0 and #later == 0 then return end
    if not co or coroutine.status(co) == "dead" then co = coroutine.create(work) end
    -- a share of the frame: more while something on screen waits, little for the background prefetch
    local ft = math.Clamp(RealFrameTime(), 1 / 300, 1 / 15)
    local deadline = now + math.min(budget:GetFloat() / 1000, (waiting and 0.3 or 0.1) * ft)
    GMOT.SetYield(function()
        if SysTime() > deadline then coroutine.yield() end
        if background and (#queue > 0 or #previews > 0) then error(PREEMPT, 0) end
    end)
    local ok, err = coroutine.resume(co)
    GMOT.SetYield(nil)
    local spent = SysTime() - now
    if spent > stats.worst then stats.worst = spent end
    if not ok then print("[GMOT] " .. tostring(err)); co = nil end
end)

-- background prefetch: effects a few seconds after the map loads, then what Lua names, then the rest
local function queue_later(classes)
    if not prefetch:GetBool() then return end
    local add = {}
    for _, e in ipairs(ENTRIES) do
        if not e.state and classes[e.cls] then add[#add + 1] = e end
    end
    table.sort(add, function(a, b) if a.cls ~= b.cls then return a.cls < b.cls end return a.side < b.side end)
    for _, e in ipairs(add) do later[#later + 1] = e end
end

local function on_map()
    pcall(scan_map)
    local world = game.GetWorld()
    if world and world.GetMaterials then
        for _, m in ipairs(world:GetMaterials() or {}) do want_material(m) end
    end
    timer.Simple(3, function() queue_later({ [1] = true }) end)
    timer.Simple(20, function() queue_later({ [2] = true, [3] = true }) end)
end
hook.Add("InitPostEntity", "gmot_stream", on_map)
if IsValid(LocalPlayer()) then on_map() end   -- loaded after the map started (Lua refresh)

concommand.Add("gmot_status", function()
    local by = {}
    for _, e in ipairs(ENTRIES) do
        local s = by[e.slug] or { n = 0, full = 0, preview = 0, waiting = 0, failed = 0 }
        by[e.slug] = s
        s.n = s.n + 1
        if e.state == 3 then s.full = s.full + 1 elseif e.state == 2 then s.preview = s.preview + 1
        elseif e.state == 1 then s.waiting = s.waiting + 1 elseif e.state == -1 then s.failed = s.failed + 1 end
    end
    for slug, s in SortedPairs(by) do
        print(string.format("[GMOT] %s: %d textures, %d ready, %d preview, %d waiting, %d failed",
            slug, s.n, s.full, s.preview, s.waiting, s.failed))
    end
    print(string.format("[GMOT] decoded %d (%.1f s CPU), reused %d, mounts %d, failed %d, worst frame %.0f ms",
        stats.decoded, stats.decode_s, stats.reused, stats.mounts, stats.failed, stats.worst * 1000))
end)

GMOT.Want = function(model) want_model(model) end
GMOT.WantMaterial = want_material
GMOT.Entries = ENTRIES
