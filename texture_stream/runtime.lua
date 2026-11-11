-- GMod Addon Optimizer texture stream runtime (GMOT core). Shared by every addon that ships it: the newest
-- core wins and keeps the manifests registered by older ones.
--
-- An optimized addon stores its textures as GMOT blobs in data_static/gmot/<slug>/ and keeps a tiny
-- placeholder VTF at each original path. When something needs one of those textures, this runtime decodes
-- the blob (decoder.lua) into a VTF, writes a GMA with it to data/gmot/, mounts it and points the materials
-- at materials/gmot/<slug>/<i>.vtf. Big textures come first as a 512 px preview, and only go to full
-- resolution when they are big on screen (weapons in hand, things up close, HUD). The GMAs are kept in
-- data/gmot for the next sessions (gmot_cache_mb): what is there is mounted at start and only applied.
--
-- Work order: what is on screen now (previews first) > what Lua only named (preview until drawn) >
-- background prefetch (effects, then the rest). Lower work is dropped mid-decode when higher work arrives.
-- Every engine call that loads something (Material, SetTexture) is paced: the worker pauses between them
-- when the frame's share is used, and the ones nothing on screen waits for take at most ~10% of the time.

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
local PT = FindMetaTable("ProjectedTexture")
local EMITTER = FindMetaTable("CLuaEmitter")
local ITEX = FindMetaTable("ITexture")
local ITEX_NAME = ITEX and ITEX.GetName

local PREVIEW = 512             -- bigger textures get a preview first (the encoder's preview size)
local FULL_PX = 200             -- on-screen diameter from which an entity's textures go to full resolution
local DIR = "gmot"              -- data/gmot: GMAs of this session
local budget = CreateClientConVar("gmot_budget", "6", true, false,
    "Texture stream: at most this many milliseconds of decoding per frame", 1, 50)
local prefetch = CreateClientConVar("gmot_prefetch", "1", true, false,
    "Texture stream: decode in the background the textures no model shows (effects first, then the rest)", 0, 1)
local prefetch_mb = CreateClientConVar("gmot_prefetch_mb", "256", true, false,
    "Texture stream: at most this many MB of textures decoded in the background per session", 0, 16384)
local cache_mb = CreateClientConVar("gmot_cache_mb", "512", true, false,
    "Texture stream: MB of decoded textures kept in data/gmot for the next sessions (0: decode again every session)",
    0, 16384)

local lower, gsub, SysTime = string.lower, string.gsub, SysTime
-- a material or texture name as the game's file system reads it: either slash, repeated slashes as one, any
-- case (the game keeps repeated slashes in the texture's name: a VMT written with two backslashes after
-- "models" loads the file and calls the texture models//weapons/...)
local function norm(s)
    s = lower((gsub(s, "\\", "/")))
    s = gsub(s, "//+", "/")
    s = gsub(s, "^/+", "")
    s = gsub(s, "^materials/", "")
    return (gsub(s, "%.v[mt][tf]$", ""))    -- .vmt, .vtf
end
local function norm_model(s) return lower((gsub(s, "\\", "/"))) end

GMOT.Manifests = GMOT.Manifests or {}
local BYREF, BYMAT, BYMODEL = {}, {}, {}
local ENTRIES = {}
local BYNAME = {}               -- name of a decoded VTF (gmot/<slug>/<i>[_p|_h]) -> entry
local SEEN_MODEL, SEEN_MAT, SEEN_ENT = {}, {}, {}
local RESOLVED = {}             -- texture name as Lua gave it -> entry or false
local TARGETS = {}              -- materials pointed at a stream texture by Lua: key -> { m, param, entry }
local PTS = setmetatable({}, { __mode = "k" })  -- projected textures (lamps) showing a stream texture -> entry
local previews, queue = {}, {}  -- on screen: previews, then full resolution
local low = {}                  -- named by Lua, not seen drawn yet
local later = {}                -- background prefetch (manifest classes 1-3), filled after the map loads
-- mounted, to be shown (e.cached = finest size mounted and not shown yet): what the screen waits for, and
-- what nothing waits for (named by Lua, prefetch)
local applies, applies_bg = {}, {}
local deadline = 0              -- the worker pauses at the next step after this time
local rest_until = 0            -- no background apply before this time
local in_worker = false         -- running inside the worker coroutine (it may pause)
local function breathe()
    if in_worker and SysTime() > deadline then coroutine.yield() end
end
local stats = GMOT.Stats or {}
for k, v in pairs({ decoded = 0, reused = 0, mounts = 0, decode_s = 0, worst = 0, failed = 0, preempted = 0 }) do
    stats[k] = stats[k] or v
end
stats.ops = stats.ops or {}
stats.worst_what = stats.worst_what or ""
GMOT.Stats = stats
local doing = ""                -- what the worker is doing (for the worst-frame report)
local did = ""                  -- the steps of this frame, for the worst-frame report
local function step(label)
    doing = label
    did = did == "" and label or (#did < 300 and did .. " > " .. label or did)
end
stats.ops_what = stats.ops_what or {}
local function timed(label, t0, what)
    local d = SysTime() - t0
    if d > (stats.ops[label] or 0) then
        stats.ops[label] = d
        stats.ops_what[label] = what
    end
end

local MOUNTED = {}              -- files our GMAs put in the game filesystem (lowercase paths)
local function mount(path)
    local t0 = SysTime()
    local ok, files = game.MountGMA(path)
    timed("mount", t0)
    if ok then
        for _, name in ipairs(files or {}) do MOUNTED[lower(name)] = true end
    end
    return ok
end
local function has(name) return MOUNTED["materials/" .. name .. ".vtf"] == true end

-- name of the VTF of an entry at a size: drop = finest levels left out (0 full, e.P preview, 1 half)
local function fname(e, drop)
    if drop == 0 then return e.name end
    if drop == e.P then return e.name_p end
    return e.name .. "_h"
end

local function push(list, e, front)
    if front then table.insert(list, 1, e) else list[#list + 1] = e end
end
-- full: the full resolution is needed (else the preview is enough: what the GPU samples of a big texture on
-- something small on screen); lo: only named by Lua, not seen drawn; held: what the player holds (first in
-- line). Asking again upgrades.
local function want(e, full, lo, held)
    local st = e.state
    if st == 3 or st == -1 then return end
    local big = e.side > PREVIEW
    if not st then
        e.state = 1
        e.t0 = SysTime()
        -- mounted earlier (cache of an earlier session, or before a map change): use it as it is
        local d = has(e.name) and 0 or nil
        if not d and big then
            d = e.P >= 2 and has(e.name .. "_h") and 1 or (has(e.name_p) and e.P or nil)
        end
        if d then
            e.cached, e.got = d, d
            if d == 0 then e.want_full = true end
            stats.reused = stats.reused + 1
            if lo then applies_bg[#applies_bg + 1] = e end
        end
    end
    if lo then
        if not e.in_hi and not e.in_lo and e.cached ~= 0 then
            e.in_lo = true
            low[#low + 1] = e
        end
        return
    end
    if not e.in_hi then
        e.in_hi = true
        if e.cached then push(applies, e, held) end
    end
    if held and not e.held then
        e.held = true
        -- already waiting further back in line: move up
        for _, list in ipairs({ applies, previews, queue }) do
            for k = #list, 1, -1 do if list[k] == e then table.remove(list, k) push(list, e, true) end end
        end
    end
    if big and not e.in_prev and not e.got and (e.state or 0) < 2 then
        e.in_prev = true
        push(previews, e, held)
    end
    if (full or not big) and not e.want_full then
        e.want_full = true
        push(queue, e, held)
    end
end

local function want_list(l, full, lo, held) for k = 1, #l do want(l[k], full, lo, held) end end

local function texture_is(m, param, e)
    local t = m:GetTexture(param)
    if not t then return false end
    local cur = norm(t:GetName() or "")
    return cur == e.ref or cur == e.name or cur == e.name_p or cur == e.name .. "_h"
end

-- the material the engine makes for a texture named without a VMT
local function point_self(e, name)
    local m = O.Material(e.ref)
    if m and not m:IsError() and texture_is(m, "$basetexture", e) then O.SetTexture(m, "$basetexture", name) end
end

local LOOKED, look = {}, {}     -- materials seen on screen: asked for what they hold (look_inside), and the line
-- SEEN_*: 1 = named by Lua, 2 = on screen (preview), 3 = on screen at full resolution
local function want_material(name, full, lo)
    if type(name) ~= "string" or name == "" then return end
    local lvl = lo and 1 or (full == false and 2 or 3)
    if (SEEN_MAT[name] or 0) >= lvl then return end
    SEEN_MAT[name] = lvl
    local k = norm(name)
    local l = BYMAT[k]
    if l then want_list(l, lvl == 3, lo) end
    local e = BYREF[k]
    if e then
        if not e.self_mat then
            e.self_mat = true       -- a material named like the texture (no VMT: the engine makes one)
            if e.lvl then point_self(e, fname(e, e.lvl)) end   -- decoded before anything named it this way
        end
        want(e, lvl == 3, lo)
    end
    if lvl >= 2 and not LOOKED[k] then
        LOOKED[k] = true
        look[#look + 1] = { name, k, lvl == 3 }
    end
end

-- What a material on screen really holds. The manifests list, from the files, the materials that sat next to
-- the addon when it was optimized; a material of another addon (a texture pack several addons share), or one
-- the game put together in a way the files do not show, can hold these textures too. Each material is asked
-- for its parameters once, a few materials per frame (look_some, from the Think hook).
local function look_inside(name, k, full)
    local m = O.Material(k)     -- k: the name without prefix and extension, the way Material() takes it
    if not ITEX_NAME or not m or m:IsError() or not m.GetKeyValues then return end
    for param, v in pairs(m:GetKeyValues() or {}) do
        local tv = type(v)
        if tv == "ITexture" or tv == "table" or tv == "userdata" then
            local ok, tn = pcall(ITEX_NAME, v)
            local e = ok and type(tn) == "string" and BYREF[norm(tn)]
            if e then
                local refs, known = e.refs, false
                for j = 1, #refs, 2 do
                    if refs[j] == k and refs[j + 1] == param then known = true break end
                end
                if not known then
                    refs[#refs + 1] = k
                    refs[#refs + 1] = param
                    local l = BYMAT[k]
                    if not l then l = {}; BYMAT[k] = l end
                    l[#l + 1] = e
                    stats.found = (stats.found or 0) + 1
                    if e.lvl then O.SetTexture(m, param, fname(e, e.lvl)) end   -- decoded already: show it now
                end
                want(e, full)
            end
        end
    end
end
local function look_some(n)
    for _ = 1, math.min(#look, n) do
        local it = table.remove(look)
        local ok, err = pcall(look_inside, it[1], it[2], it[3])
        if not ok and not stats.look_error then
            stats.look_error = tostring(err)
            print("[GMOT] material " .. tostring(it[1]) .. ": " .. stats.look_error)
        end
    end
end

-- model: path as the game gives it; ent: an entity using it (for models no manifest names, its materials);
-- full: false when the entity is small on screen
local function want_model(model, ent, full, held)
    if type(model) ~= "string" or model == "" then return end
    local lvl = held and 4 or (full == false and 2 or 3)
    local known = (SEEN_MODEL[model] or 0) >= lvl
    if known and (not ent or (SEEN_ENT[model] or 0) >= lvl) then return end
    if not known then
        SEEN_MODEL[model] = lvl
        if string.find(model, "%.vmt$") or string.find(model, "%.spr$") then  -- env_sprite & co: a material
            SEEN_ENT[model] = lvl
            want_material(model, lvl >= 3)
            return
        end
        local l = BYMODEL[norm_model(model)]
        if l then want_list(l, lvl >= 3, nil, held) end
    end
    -- the materials the entity really has: the manifests only know the models that were next to the addon
    -- when it was optimized (a model of another addon can use these textures)
    if ent and (SEEN_ENT[model] or 0) < lvl then
        SEEN_ENT[model] = lvl
        if ent.GetMaterials then
            for _, m in ipairs(ent:GetMaterials() or {}) do want_material(m, lvl >= 3) end
        end
    end
end

-- a 2048 texture spread over a model narrower than ~256 px on screen is only sampled at its 512 level or lower
local function needs_full(ent, eye, scale)
    local r = ent:BoundingRadius()
    if r <= 0 then return true end
    local d = eye:Distance(ent:WorldSpaceCenter())
    if d <= r * 1.5 then return true end
    return 2 * r / d * scale > FULL_PX
end

-- points everything that shows the texture at its decoded VTF of this size. Each Material/SetTexture call
-- can load a file (tens of ms the first time, and it cannot be split): the worker pauses between them when
-- the frame's share is used. bg: nothing on screen waits for it; these calls then take ~10% of the time.
local function apply(e, drop, bg)
    if drop >= (e.lvl or 99) then return end   -- already showing this size or a finer one
    step("apply " .. e.ref)
    local name = fname(e, drop)
    local refs = e.refs
    local cost = 0
    for j = 1, #refs, 2 do
        breathe()
        local t1 = SysTime()
        local m = O.Material(refs[j])
        timed("apply_material", t1, refs[j])
        cost = cost + (SysTime() - t1)
        -- only where the material still shows this texture (Lua may have swapped it meanwhile)
        if m and not m:IsError() then
            breathe()
            t1 = SysTime()
            if texture_is(m, refs[j + 1], e) then O.SetTexture(m, refs[j + 1], name) end
            timed("apply_texture", t1, name)
            cost = cost + (SysTime() - t1)
        end
    end
    if e.self_mat then breathe() end
    local t1 = SysTime()
    if e.self_mat then point_self(e, name) end
    for _, t in pairs(TARGETS) do
        if t[3] == e then O.SetTexture(t[1], t[2], name) end
    end
    for pt, pe in pairs(PTS) do
        if pe == e and IsValid(pt) then
            O.PTSetTexture(pt, name)
            pt:Update()
        end
    end
    cost = cost + (SysTime() - t1)
    e.lvl = drop
    if e.cached and e.cached >= drop then e.cached = nil end
    e.state = drop == 0 and 3 or 2
    local dt = SysTime() - (e.t0 or SysTime())
    if drop == 0 then e.ready = dt elseif drop == e.P then e.pready = dt else e.hready = dt end
    if cost > (stats.ops.apply or 0) then
        stats.ops.apply = cost
        stats.ops_what.apply = e.ref
    end
    stats.applied = (stats.applied or 0) + 1
    if bg then rest_until = SysTime() + cost * 9 end
end

-- a material Lua made or changed points at one of our textures: follow it, and know it by name
-- ("name" and "!name", how Entity:SetMaterial refers to materials made by CreateMaterial)
local function target(m, param, e)
    if not (m and m.GetName) then return end
    local nm = m:GetName()
    local key = nm .. "\0" .. lower(param)
    local old = TARGETS[key]
    if old and old[3] == e and old[1] == m then return end
    TARGETS[key] = { m, param, e }
    for _, key in ipairs({ lower(nm), "!" .. lower(nm) }) do
        local l = BYMAT[key]
        if not l then l = {}; BYMAT[key] = l end
        local dup = false
        for _, x in ipairs(l) do if x == e then dup = true break end end
        if not dup then l[#l + 1] = e end
    end
    want(e, true, true)
end

-- ------------------------------------------------------------------ decoded textures of earlier sessions
-- data/gmot/cache.txt lists the GMAs kept: { [file] = { bytes, last use, { slug, ... }, first file } }. A slug
-- carries the addon's content hash, so a GMA never serves another version of the addon. At start: the least
-- recently used files go when the cache passes gmot_cache_mb; the small GMAs the registered addons use are
-- merged into a few big ones (every mounted GMA is one more place each file lookup of the game searches);
-- they are mounted (their textures are then applied, not decoded); anything else in data/gmot is a leftover
-- and is deleted (a file this game process has mounted cannot be deleted and stays).
local CACHE_INDEX = DIR .. "/cache.txt"
local MERGE_TO = 64 * 1048576   -- merged GMAs up to this size
local cache = {}
do
    local raw = file.Read(CACHE_INDEX, "DATA")
    if raw and raw ~= "" then cache = util.JSONToTable(raw) or {} end
end
local function save_cache() file.Write(CACHE_INDEX, util.TableToJSON(cache)) end

local function cache_add(path, bytes, slugs, first)
    local list = {}
    for s in pairs(slugs) do list[#list + 1] = s end
    cache[string.match(path, "[^/]+$")] = { bytes, os.time(), list, first }
    save_cache()
end

-- files of a GMA this runtime wrote
local function read_gma(data)
    if not data or data:sub(1, 5) ~= "GMAD\3" then return nil end
    local pos = 6 + 16
    for _ = 1, 4 do pos = (string.find(data, "\0", pos, true) or #data) + 1 end   -- required, name, desc, author
    pos = pos + 4
    local entries = {}
    while true do
        local a, b, c, d = string.byte(data, pos, pos + 3)
        if not d or a + b + c + d == 0 then pos = pos + 4 break end
        local z = string.find(data, "\0", pos + 4, true)
        if not z then return nil end
        local name = data:sub(pos + 4, z - 1)
        local s1, s2, s3, s4 = string.byte(data, z + 1, z + 4)
        entries[#entries + 1] = { name = name, size = s1 + s2 * 256 + s3 * 65536 + s4 * 16777216 }
        pos = z + 1 + 12
    end
    for _, e in ipairs(entries) do
        e.data = data:sub(pos, pos + e.size - 1)
        if #e.data ~= e.size then return nil end
        pos = pos + e.size
    end
    return entries
end

local write_gma                 -- defined with the GMA writer below

-- small GMAs -> big ones of up to MERGE_TO; returns { [old name] = new name }. The old files are dropped from
-- the index and deleted (when mounted by this process they are deleted on the next start, as leftovers)
local function merge(names)
    local merged = {}
    local group, gbytes, gslugs = {}, 0, {}
    local function close_group()
        if #group >= 2 then
            local files = {}
            for _, name in ipairs(group) do
                local entries = read_gma(file.Read(DIR .. "/" .. name, "DATA"))
                if entries then for _, e in ipairs(entries) do files[#files + 1] = e end end
            end
            local path = #files > 0 and write_gma(files)
            if path then
                local new = string.match(path, "[^/]+$")
                cache_add(path, gbytes, gslugs, files[1].name)
                for _, name in ipairs(group) do
                    cache[name] = nil
                    file.Delete(DIR .. "/" .. name)
                    merged[name] = new
                end
            end
        end
        group, gbytes, gslugs = {}, 0, {}
    end
    for _, name in ipairs(names) do
        local c = cache[name]
        group[#group + 1] = name
        gbytes = gbytes + (c[1] or 0)
        for _, sl in ipairs(c[3] or {}) do gslugs[sl] = true end
        if gbytes >= MERGE_TO then close_group() end
    end
    close_group()
    save_cache()
    return merged
end

-- at shutdown (quit or map change): this session's small GMAs become a few big ones for the next start
local function consolidate()
    if cache_mb:GetInt() <= 0 then return end
    local small = {}
    for name, c in pairs(cache) do
        if type(c) == "table" and (c[1] or 0) < MERGE_TO / 4 and file.Exists(DIR .. "/" .. name, "DATA") then
            small[#small + 1] = name
        end
    end
    if #small < 6 then return end
    table.sort(small, function(a, b) return (cache[a][2] or 0) > (cache[b][2] or 0) end)
    merge(small)
end

local function cache_start()
    local t_start = SysTime()
    local known = {}
    for _, man in ipairs(GMOT.Manifests) do known[man.slug] = true end
    local limit = cache_mb:GetInt() * 1048576
    local names = {}
    for name, c in pairs(cache) do
        if type(c) == "table" and file.Exists(DIR .. "/" .. name, "DATA") then names[#names + 1] = name
        else cache[name] = nil end
    end
    table.sort(names, function(a, b) return (cache[a][2] or 0) > (cache[b][2] or 0) end)
    -- after a map change our GMAs of this game process are still mounted: no merging then
    local newest = names[1] and cache[names[1]][4]
    local same_process = newest and file.Exists(newest, "GAME")
    local total, useful, small, small_bytes = 0, {}, {}, 0
    for _, name in ipairs(names) do
        local c = cache[name]
        total = total + (c[1] or 0)
        if total > limit then
            file.Delete(DIR .. "/" .. name)
            if not file.Exists(DIR .. "/" .. name, "DATA") then cache[name] = nil else useful[#useful + 1] = name end
        else
            local use = false
            for _, s in ipairs(c[3] or {}) do if known[s] then use = true break end end
            if use then
                useful[#useful + 1] = name
                if not same_process and (c[1] or 0) < MERGE_TO / 4 then
                    small[#small + 1] = name
                    small_bytes = small_bytes + (c[1] or 0)
                end
            end
        end
    end
    -- normally merged at the previous shutdown; a game that did not shut down cleanly (crash, killed) can
    -- leave many small ones: merge them now (it costs a moment at the first frame)
    if #small > 24 then
        local merged = merge(small)
        local keep, added = {}, {}
        for _, name in ipairs(useful) do
            local new = merged[name]
            if not new then keep[#keep + 1] = name
            elseif not added[new] then added[new] = true; keep[#keep + 1] = new end
        end
        useful = keep
    end
    timed("cache_prune", t_start)
    local mounted = 0
    local t_mount = SysTime()
    for _, name in ipairs(useful) do
        if mount("data/" .. DIR .. "/" .. name) then
            cache[name][2] = os.time()
            mounted = mounted + 1
        else
            file.Delete(DIR .. "/" .. name)
            cache[name] = nil
        end
    end
    timed("cache_mount_all", t_mount)
    local t_clean = SysTime()
    for _, name in ipairs(file.Find(DIR .. "/*", "DATA") or {}) do
        if name ~= "cache.txt" and not cache[name] then file.Delete(DIR .. "/" .. name) end
    end
    timed("cache_cleanup", t_clean)
    stats.cache_mounted = mounted
    save_cache()
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

local batch = 0
-- writes files = { { name, data }, ... } as data/gmot/<new>.gma; returns its path in data/ or nil
write_gma = function(files)
    batch = batch + 1
    local path = DIR .. "/s" .. os.time() .. "_" .. batch .. ".gma"
    local tmp = path .. ".tmp.dat"     -- file.Write only takes some extensions; renamed below
    file.CreateDir(DIR)
    local h = file.Open(tmp, "wb", "DATA")
    if not h then
        print("[GMOT] cannot write data/" .. tmp)
        return nil
    end
    h:Write(gma(files))
    h:Close()
    file.Delete(path)
    file.Rename(tmp, path)
    return path
end

local pending, pending_bytes, pending_t0 = {}, 0, 0
local pending_urgent = false    -- something in `pending` is waited for on screen
local idle = true               -- the worker waits for work (not halfway through a batch)
local background = false        -- the decode in progress is not for the screen: dropped when the screen needs something
local PREEMPT = "gmot: preempted"
local planes_kept = 0           -- previews whose planes wait for their full decode
local function urgent_work() return #previews > 0 or #queue > 0 end

local function flush()
    if #pending == 0 then return end
    local files, slugs, bytes = {}, {}, 0
    for k, p in ipairs(pending) do
        files[k] = { name = "materials/" .. fname(p.e, p.drop) .. ".vtf", data = p.vtf }
        slugs[p.e.slug] = true
        bytes = bytes + #p.vtf
    end
    local done = pending
    pending, pending_bytes, pending_urgent = {}, 0, false
    step("write")
    local t0 = SysTime()
    local first = files[1].name
    local path = write_gma(files)
    files = nil
    timed("write", t0)
    if not path then
        for _, p in ipairs(done) do p.e.state = -1 end
        stats.failed = stats.failed + #done
        return
    end
    coroutine.yield()
    step("mount " .. path)
    local ok = mount("data/" .. path)
    stats.mounts = stats.mounts + 1
    if ok then pcall(cache_add, path, bytes, slugs, first) end
    if not ok then
        print("[GMOT] could not mount data/" .. path)
        for _, p in ipairs(done) do p.e.state = -1 end
        stats.failed = stats.failed + #done
        return
    end
    for _, p in ipairs(done) do
        local e = p.e
        if p.drop < math.min(e.lvl or 99, e.cached or 99) then
            e.cached = p.drop
            if p.bg and not e.in_hi then applies_bg[#applies_bg + 1] = e else push(applies, e, e.held) end
        end
    end
end

-- shows the finest mounted size of the first entry of an apply line
local function apply_next(list, bg)
    local a = table.remove(list, 1)
    local d = a.cached
    if d then
        a.cached = nil
        apply(a, d, bg)
    end
end

local function work()
    idle = false
    while true do
        local e, drop, urgent, src, again = nil, 0, true, nil, false
        if #applies > 0 then
            apply_next(applies)
        elseif #previews > 0 then
            e = table.remove(previews, 1)
            e.in_prev = false
            drop = e.P
        elseif #queue > 0 then
            e = table.remove(queue, 1)
            if e.P >= 2 and math.min(e.lvl or 99, e.cached or 99) > 1 and not e.half then
                -- 2048 and up: the half size first (a quarter of the work), then the full one
                e.half, drop, again = true, 1, true
            end
        elseif #applies_bg > 0 and SysTime() >= rest_until then
            apply_next(applies_bg, true)
        elseif #low > 0 then
            -- named by Lua only: big textures up to the preview (drawing them asks for more)
            e, urgent, src = table.remove(low, 1), false, low
            e.in_lo = false
            if e.in_hi then e = nil elseif e.side > PREVIEW and not e.want_full then drop = e.P end
        elseif #later > 0 then
            e, urgent, src = table.remove(later, 1), false, later
            if e.state then
                e = nil
            else
                e.state = 1
                e.t0 = SysTime()
                if e.side > PREVIEW then drop = e.P end
            end
        elseif #applies_bg > 0 then
            coroutine.yield()           -- only background applies left, and they rest
        end
        if e and (e.state == 3 or e.state == -1 or drop >= math.min(e.lvl or 99, e.cached or 99)) then
            e = nil                     -- nothing left to do for it at this size
        elseif e and has(fname(e, drop)) then
            -- in a GMA mounted from the cache
            if urgent then
                apply(e, drop)
            else
                e.cached = drop
                applies_bg[#applies_bg + 1] = e
            end
            if again then queue[#queue + 1] = e end
            e = nil
        elseif e then
            step((drop > 0 and "decode/" .. drop .. " " or "decode ") .. e.ref)
            GMOT.where = "start"
            local t0 = SysTime()
            local data = file.Read(e.blob, "GAME")
            timed("read", t0)
            t0 = SysTime()
            local ok, vtf = false, "blob not found"
            background = not urgent
            local keep = drop > 0 and drop == e.P and e.want_full and planes_kept < 3 and {} or nil
            if data then ok, vtf = pcall(GMOT.Decode, data, drop, keep or e.planes) end
            background = false
            if keep then
                e.planes = ok and keep.planes and keep or nil
                if e.planes then planes_kept = planes_kept + 1 end
            elseif drop == 0 and e.planes then
                e.planes = nil
                planes_kept = planes_kept - 1
            end
            if again and ok and vtf ~= PREEMPT then queue[#queue + 1] = e end
            data = nil
            stats.decode_s = stats.decode_s + (SysTime() - t0)
            if not ok and vtf == PREEMPT then
                -- back where it came from, after what the screen waits for
                if src == later then
                    e.state = nil
                    table.insert(later, 1, e)
                elseif not e.in_hi then
                    e.in_lo = true
                    table.insert(low, 1, e)
                end
                stats.preempted = stats.preempted + 1
            elseif ok and vtf then
                stats.decoded = stats.decoded + 1
                if #pending == 0 then pending_t0 = SysTime() end
                pending[#pending + 1] = { e = e, vtf = vtf, drop = drop, bg = not urgent }
                if urgent then pending_urgent = true end
                pending_bytes = pending_bytes + #vtf
            else
                stats.failed = stats.failed + 1
                e.state = -1
                print("[GMOT] " .. e.slug .. " texture " .. e.i .. " (" .. e.ref .. ") failed: " .. tostring(vtf))
            end
        end
        -- previews go up as soon as the last one is decoded (before any full texture), what the screen waits
        -- for as soon as its queue is empty; otherwise (and for the background) in batches
        if #pending > 0 then
            local age = SysTime() - pending_t0
            local busy = #previews > 0
            if (not busy and pending_urgent and (#queue == 0 or pending[#pending].drop > 0))
                    or (not busy and #queue == 0 and #low == 0 and #later == 0)
                    or pending_bytes >= (busy and 3000000 or 6000000) or age > (busy and 1.5 or 3) then
                flush()
            end
        end
        if not e and #previews == 0 and #queue == 0 and #applies == 0 and #applies_bg == 0 and #low == 0
                and #later == 0 then
            idle = true
            doing = ""
            coroutine.yield()
            idle = false
        end
    end
end

-- ------------------------------------------------------------------ what is on screen
local snap, si, next_inv = {}, 1, 0
local NSUB = {}
local function sub_materials(e, m, full)
    local mo = e:GetMaterial()
    if mo ~= "" then want_material(mo, full) end
    local n = NSUB[m]
    if not n then
        n = math.min(#(e:GetMaterials() or {}), 32)
        NSUB[m] = n
    end
    for s = 0, n - 1 do
        local sm = e:GetSubMaterial(s)
        if sm ~= "" then want_material(sm, full) end
    end
end

local function scan(now)
    local ply = LocalPlayer()
    local eye = EyePos()
    local fov = IsValid(ply) and ply:GetFOV() or 75
    local scale = ScrH() / (2 * math.tan(math.rad(math.Clamp(fov, 10, 170)) / 2))
    if IsValid(ply) then
        -- what the player holds: always full resolution, with its material changes (camos, skins)
        for _, x in ipairs({ ply:GetActiveWeapon(), ply:GetViewModel(), ply.GetHands and ply:GetHands() or nil }) do
            if IsValid(x) then
                local m = x:GetModel()
                if m then
                    want_model(m, x, true, true)
                    sub_materials(x, m, true)
                end
                if isstring(x.ViewModel) then want_model(x.ViewModel) end
            end
        end
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
                local full = needs_full(e, eye, scale)
                want_model(m, e, full)
                sub_materials(e, m, full)
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
    local ent_lump = lumps[0]
    if ent_lump[2] > 0 and ent_lump[2] < 8000000 then
        f:Seek(ent_lump[1])
        local data = f:Read(ent_lump[2]) or ""
        if data:sub(1, 4) ~= "LZMA" then
            local seen = {}
            for v in string.gmatch(data, '"([^"\n]+)"') do
                if not seen[v] and string.find(v, "/", 1, true) then
                    seen[v] = true
                    local k = norm(v)
                    if BYMAT[k] or BYREF[k] then want_material(v) end
                end
            end
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

-- ------------------------------------------------------------------ Lua that names or draws materials
-- naming a material (Material, CreateMaterial, SetTexture with a name, Entity:SetMaterial) only queues it
-- behind what is on screen; drawing it (render/surface calls) puts it on screen at full resolution
Material = function(name, ...)
    if name and (SEEN_MAT[name] or 0) < 1 then want_material(name, true, true) end
    return O.Material(name, ...)
end
-- the stream texture a name stands for: its original path or one of its decoded VTFs (a name read back from
-- a material that already shows it)
local function entry_named(name)
    local e = RESOLVED[name]
    if e == nil then
        local k = norm(name)
        e = BYREF[k] or BYNAME[k] or false
        RESOLVED[name] = e
    end
    return e or nil
end
-- tex: a name or an ITexture (Lua copying the texture of one material to another)
local function entry_of(tex)
    if type(tex) == "string" then return entry_named(tex) end
    if tex ~= nil and ITEX_NAME then
        local ok, name = pcall(ITEX_NAME, tex)
        if ok and type(name) == "string" then return entry_named(name) end
    end
end
CreateMaterial = function(name, shader, params, ...)
    local m = O.CreateMaterial(name, shader, params, ...)
    if m and type(params) == "table" then
        for k, v in pairs(params) do
            if type(k) == "string" and type(v) == "string" then
                local e = entry_named(v)
                if e then
                    target(m, k, e)
                    if e.lvl then O.SetTexture(m, k, fname(e, e.lvl)) end
                end
            end
        end
    end
    return m
end
IMAT.SetTexture = function(self, param, tex, ...)
    if type(param) == "string" and self and self.GetName then
        local e = entry_of(tex)
        if e then
            target(self, param, e)
            if e.lvl then tex = fname(e, e.lvl) end
        elseif next(TARGETS) ~= nil then
            TARGETS[self:GetName() .. "\0" .. lower(param)] = nil
        end
    end
    return O.SetTexture(self, param, tex, ...)
end
-- per-frame draw calls: each IMaterial object is looked at once (weak cache)
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
    surface.GetTextureID = function(name, ...) want_material(name, true, true) return f(name, ...) end
end
O["surface.SetTexture"] = O["surface.SetTexture"] or surface.SetTexture
if O["surface.SetTexture"] and surface.GetTextureNameByID then
    local f, SEEN_ID = O["surface.SetTexture"], {}
    surface.SetTexture = function(id, ...)
        if id and not SEEN_ID[id] then
            SEEN_ID[id] = true
            local ok, name = pcall(surface.GetTextureNameByID, id)
            if ok and name then want_material(name) end
        end
        return f(id, ...)
    end
end

-- lamps (vehicle headlights, flashlights): no material in between, the lamp itself is pointed at the stream
-- and followed while it shows it. On screen as soon as it is lit: front of the line, full resolution.
if PT and PT.SetTexture and PT.Update then
    O.PTSetTexture = O.PTSetTexture or PT.SetTexture
    PT.SetTexture = function(self, tex, ...)
        local e = entry_of(tex)
        if e then
            if PTS[self] ~= e then
                PTS[self] = e
                want(e, true)
            end
            if e.lvl then tex = fname(e, e.lvl) end
        elseif PTS[self] then
            PTS[self] = nil
        end
        return O.PTSetTexture(self, tex, ...)
    end
end
-- particles made by Lua
if EMITTER and EMITTER.Add then
    O.EmitterAdd = O.EmitterAdd or EMITTER.Add
    local f = O.EmitterAdd
    EMITTER.Add = function(self, mat, ...)
        if type(mat) == "string" then want_material(mat) elseif mat then seen_imat(mat) end
        return f(self, mat, ...)
    end
end
O["game.AddDecal"] = O["game.AddDecal"] or game.AddDecal
if O["game.AddDecal"] then
    local f = O["game.AddDecal"]
    game.AddDecal = function(name, mats, ...)
        if type(mats) == "table" then
            for _, m in pairs(mats) do want_material(m, true, true) end
        else
            want_material(mats, true, true)
        end
        return f(name, mats, ...)
    end
end

ENT.SetMaterial = function(self, mat, ...)
    if mat and mat ~= "" then want_material(mat, true, true) end
    return O.SetMaterial(self, mat, ...)
end
ENT.SetSubMaterial = function(self, i, mat, ...)
    if mat and mat ~= "" then want_material(mat, true, true) end
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
        local P, s = 0, e.side
        while s > PREVIEW do s = s / 2; P = P + 1 end
        e.P = P                         -- levels between the full size and the preview
        list[i] = e
        ENTRIES[#ENTRIES + 1] = e
        BYREF[e.ref] = BYREF[e.ref] or e
        BYNAME[e.name], BYNAME[e.name_p], BYNAME[e.name .. "_h"] = e, e, e
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
    for _, old in ipairs(GMOT.Manifests) do
        if old.slug == man.slug then return end     -- the same addon twice
    end
    GMOT.Manifests[#GMOT.Manifests + 1] = man
    index(man)
    SEEN_MODEL, SEEN_MAT, SEEN_ENT, RESOLVED = {}, {}, {}, {}
end

for _, man in ipairs(GMOT.Manifests) do index(man) end   -- registered by an older core

local co
local started = false
local function ensure_started()
    if started then return end
    started = true
    local ok, err = pcall(cache_start)
    if not ok then print("[GMOT] cache: " .. tostring(err)) end
end
hook.Add("Think", "gmot_stream", function()
    ensure_started()
    local now = SysTime()
    did = ""
    local ok_scan, err_scan = pcall(scan, now)
    look_some(4)
    timed("scan", now)
    local t_scan = SysTime() - now
    if not ok_scan and not stats.scan_error then
        stats.scan_error = tostring(err_scan)
        print("[GMOT] scan: " .. stats.scan_error)
    end
    local waiting = urgent_work() or #applies > 0
    if idle and not waiting and #pending == 0 and #low == 0 and #later == 0 and #applies_bg == 0 then
        stats.frame, stats.frame_what = t_scan, "scan"
        return
    end
    if not co or coroutine.status(co) == "dead" then co = coroutine.create(work) end
    -- a share of the frame: more while something on screen waits, little for the rest; what the player holds
    -- gets half of the frame. While the screen waits, a game running above 60 fps also gives what it has
    -- above 60 (a fast PC shows the textures sooner and stays at 60 or more meanwhile)
    local hot = (applies[1] and applies[1].held) or (previews[1] and previews[1].held) or (queue[1] and queue[1].held)
    local ft = math.Clamp(RealFrameTime(), 1 / 300, 1 / 15)
    local allow = (hot and 0.5 or waiting and 0.3 or 0.1) * ft
    if waiting then allow = math.max(allow, 1 / 60 - (ft - (stats.frame or 0))) end
    deadline = now + math.min(budget:GetFloat() / 1000, allow)
    local last, last_mem = SysTime(), collectgarbage("count")
    GMOT.SetYield(function()
        local t = SysTime()
        if t - last > (stats.gap or 0) then
            local mem = collectgarbage("count")
            stats.gap, stats.gap_what = t - last, string.format("%s, in %s; Lua %.0f -> %.0f MB", doing,
                tostring(GMOT.where), last_mem / 1024, mem / 1024)
        end
        if t > deadline then
            coroutine.yield()
            t = SysTime()
        end
        last, last_mem = t, collectgarbage("count")
        if background and urgent_work() then error(PREEMPT, 0) end
    end)
    in_worker = true
    local ok, err = coroutine.resume(co)
    in_worker = false
    GMOT.SetYield(nil)
    local spent = SysTime() - now
    stats.frame, stats.frame_what = spent, did
    if spent > stats.worst then
        stats.worst = spent
        stats.worst_what = string.format("scan %.0f ms; %s", t_scan * 1000, did)
    end
    if not ok then print("[GMOT] " .. tostring(err)); co = nil end
end)

-- background prefetch: effects a few seconds after the map loads, then what Lua names, then the rest
-- At most gmot_prefetch_mb of decoded textures per session (smallest first inside each class): with hundreds
-- of addons the rest waits until something names or shows it.
local prefetched = 0
local function queue_later(classes)
    if not prefetch:GetBool() then return end
    local add = {}
    for _, e in ipairs(ENTRIES) do
        if not e.state and not e.in_later and classes[e.cls] then add[#add + 1] = e end
    end
    table.sort(add, function(a, b) if a.cls ~= b.cls then return a.cls < b.cls end return a.side < b.side end)
    for _, e in ipairs(add) do
        local s = math.min(e.side, PREVIEW)
        local bytes = s * s * 4 / 3
        if prefetched + bytes > prefetch_mb:GetInt() * 1048576 then
            stats.prefetch_left = (stats.prefetch_left or 0) + 1
        else
            prefetched = prefetched + bytes
            e.in_later = true
            later[#later + 1] = e
        end
    end
end

local function on_map()
    ensure_started()
    pcall(scan_map)
    local world = game.GetWorld()
    if world and world.GetMaterials then
        for _, m in ipairs(world:GetMaterials() or {}) do want_material(m) end
    end
    -- the sky: the engine draws it by itself
    local sky = GetConVar("sv_skyname")
    sky = sky and sky:GetString() or ""
    if sky ~= "" then
        for _, side in ipairs({ "bk", "dn", "ft", "lf", "rt", "up" }) do want_material("skybox/" .. sky .. side) end
    end
    -- what the map needs and an earlier session left decoded goes up now, behind the loading screen
    local t_end = SysTime() + 0.75
    while #applies > 0 and SysTime() < t_end do apply_next(applies) end
    timer.Simple(3, function() queue_later({ [1] = true }) end)
    timer.Simple(20, function() queue_later({ [2] = true, [3] = true }) end)
end
hook.Add("InitPostEntity", "gmot_stream", on_map)
hook.Add("ShutDown", "gmot_stream", function()
    local ok, err = pcall(consolidate)
    if not ok then print("[GMOT] cache: " .. tostring(err)) end
end)
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
        print(string.format("[GMOT] %s: %d textures, %d full, %d preview, %d waiting, %d failed",
            slug, s.n, s.full, s.preview, s.waiting, s.failed))
    end
    print(string.format("[GMOT] queues: screen %d+%d, named %d, background %d, to show %d+%d; decoded %d (%.1f s), "
        .. "from the cache %d, mounts %d, failed %d, worst frame %.0f ms (%s)", #previews, #queue, #low, #later,
        #applies, #applies_bg, stats.decoded, stats.decode_s, stats.reused, stats.mounts, stats.failed,
        stats.worst * 1000, stats.worst_what))
    if (stats.prefetch_left or 0) > 0 then
        print(string.format("[GMOT] %d textures left out of the background prefetch (gmot_prefetch_mb %d): "
            .. "decoded when something names or shows them", stats.prefetch_left, prefetch_mb:GetInt()))
    end
end)

GMOT.Want = function(model) want_model(model) end
GMOT.WantMaterial = want_material
GMOT.Entries = ENTRIES
