SAO.ServerProfiler = SAO.ServerProfiler or {}

local initial_queue = {}
local initial_cursor = 1
local reconcile_queue = {}
local reconcile_cursor = 1
local state = {
    active = false
}

local function saturating_increment(value, amount)
    return math.min(1000000, value + (amount or 1))
end

local function normalized_model_path(value)
    local normalized = SAO.Contract.NormalizeVirtualPath(value)
    if not normalized or not string.StartWith(normalized, "models/") or not string.EndsWith(normalized, ".mdl") then return nil end

    return normalized
end

local function normalized_material_path(value)
    if not isstring(value) or value == "" or string.StartWith(value, "!") then return nil end
    local normalized = string.lower(string.Replace(string.Trim(value), "\\", "/"))
    if not string.StartWith(normalized, "materials/") then normalized = "materials/" .. normalized end
    if not string.EndsWith(normalized, ".vmt") then normalized = normalized .. ".vmt" end
    normalized = SAO.Contract.NormalizeVirtualPath(normalized)
    if not normalized or not string.StartWith(normalized, "materials/") then return nil end

    return normalized
end

local function evidence_rank(name)
    local evidence = SAO.Constants.EvidenceByName[name]

    return evidence and evidence.rank or 0
end

local function stronger_evidence(left, right)
    local rank = math.max(evidence_rank(left), evidence_rank(right))

    return SAO.Constants.EvidenceByWire[rank] or "registered"
end

local function new_metrics()
    local histogram = {}

    for index = 1, #SAO.Constants.PROFILER_HISTOGRAM_MS + 1 do
        histogram[index] = 0
    end

    return {
        samples = 0,
        total_ms = 0,
        max_ms = 0,
        queue_high_water = 0,
        histogram = histogram
    }
end

local function update_queue_high_water()
    if not state.metrics then return end
    local initial_remaining = math.max(0, #initial_queue - initial_cursor + 1)
    local reconcile_remaining = math.max(0, #reconcile_queue - reconcile_cursor + 1)
    state.metrics.queue_high_water = math.max(state.metrics.queue_high_water, initial_remaining + reconcile_remaining)
end

local function enqueue(target, ent)
    if #target >= SAO.Constants.MAX_ENTITY_QUEUE then
        state.truncation.models = saturating_increment(state.truncation.models)

        return false
    end

    target[#target + 1] = ent
    update_queue_high_water()

    return true
end

local function queue_initial_inventory()
    initial_queue = {}
    initial_cursor = 1

    for _, ent in ipairs(ents.GetAll()) do
        if not enqueue(initial_queue, ent) then break end
    end
end

local function queue_reconciliation()
    if reconcile_cursor <= #reconcile_queue then return end
    reconcile_queue = {}
    reconcile_cursor = 1

    for _, ent in ipairs(ents.GetAll()) do
        if not enqueue(reconcile_queue, ent) then break end
    end
end

local function promote_material(path, evidence)
    local record = state.materials_by_path[path]
    if record then
        record.evidence = stronger_evidence(record.evidence, evidence)

        return record
    end

    if state.material_count >= SAO.Constants.MAX_MATERIALS then
        state.truncation.materials = saturating_increment(state.truncation.materials)

        return nil
    end

    record = {
        id = string.lower(util.SHA256(path)),
        path = path,
        evidence = evidence
    }
    state.materials_by_path[path] = record
    state.material_count = state.material_count + 1

    return record
end

local function promote_model(path, evidence)
    local record = state.models_by_path[path]
    if record then
        record.evidence = stronger_evidence(record.evidence, evidence)

        return record
    end

    if state.model_count >= SAO.Constants.MAX_MODELS then
        state.truncation.models = saturating_increment(state.truncation.models)

        return nil
    end

    record = {
        id = string.lower(util.SHA256(path)),
        path = path,
        evidence = evidence,
        skins_by_value = {},
        bodygroups_by_key = {},
        submaterials_by_key = {},
        materials_by_path = {},
        bodygroup_count = 0,
        submaterial_count = 0,
        material_count = 0
    }
    state.models_by_path[path] = record
    state.model_count = state.model_count + 1

    return record
end

local function record_skin(record, ent)
    local value = math.Clamp(math.floor(ent:GetSkin() or 0), 0, 255)
    record.skins_by_value[value] = true
end

local function record_bodygroups(record, ent, evidence)
    for _, bodygroup in ipairs(ent:GetBodyGroups() or {}) do
        local name = isstring(bodygroup.name) and string.sub(bodygroup.name, 1, 128) or nil
        local identifier = tonumber(bodygroup.id)

        if name and name ~= "" and identifier and identifier >= 0 then
            local value = math.Clamp(math.floor(ent:GetBodygroup(identifier) or 0), 0, 255)
            local key = name .. "\0" .. tostring(value)
            local existing = record.bodygroups_by_key[key]

            if existing then
                existing.evidence = stronger_evidence(existing.evidence, evidence)
            elseif record.bodygroup_count < SAO.Constants.MAX_BODYGROUP_STATES_PER_MODEL then
                record.bodygroups_by_key[key] = {
                    name = name,
                    value = value,
                    evidence = evidence
                }
                record.bodygroup_count = record.bodygroup_count + 1
            end
        end
    end
end

local function record_materials(record, ent, evidence)
    local materials = ent:GetMaterials() or {}

    for index, value in ipairs(materials) do
        if index > SAO.Constants.MAX_MATERIALS_PER_MODEL then break end
        local path = normalized_material_path(value)

        if path and not record.materials_by_path[path] then
            record.materials_by_path[path] = true
            record.material_count = record.material_count + 1
            promote_material(path, evidence)
        elseif path then
            promote_material(path, evidence)
        end
    end

    local maximum_slot = math.min(#materials - 1, SAO.Constants.MAX_SUBMATERIALS_PER_MODEL - 1)

    for slot = 0, maximum_slot do
        local path = normalized_material_path(ent:GetSubMaterial(slot))

        if path then
            local key = tostring(slot) .. "\0" .. path
            local existing = record.submaterials_by_key[key]

            if existing then
                existing.evidence = stronger_evidence(existing.evidence, evidence)
            elseif record.submaterial_count < SAO.Constants.MAX_SUBMATERIALS_PER_MODEL then
                record.submaterials_by_key[key] = {
                    slot = slot,
                    material = path,
                    evidence = evidence
                }
                record.submaterial_count = record.submaterial_count + 1
            end

            promote_material(path, evidence)
        end
    end
end

local function record_entity(ent)
    if not state.active or not IsValid(ent) or ent:GetNoDraw() or ent:IsDormant() then return end
    local path = normalized_model_path(ent:GetModel())
    if not path then return end
    local record = promote_model(path, "instantiated")
    if not record then return end
    record_skin(record, ent)
    record_bodygroups(record, ent, "instantiated")
    record_materials(record, ent, "instantiated")
end

local function process_queue(queue, cursor, budget)
    local processed = 0

    while cursor <= #queue and processed < budget do
        record_entity(queue[cursor])
        cursor = cursor + 1
        processed = processed + 1
    end

    return cursor
end


local function record_slice(elapsed_ms)
    local metrics = state.metrics
    metrics.samples = metrics.samples + 1
    metrics.total_ms = metrics.total_ms + elapsed_ms
    metrics.max_ms = math.max(metrics.max_ms, elapsed_ms)
    local bucket = #SAO.Constants.PROFILER_HISTOGRAM_MS + 1

    for index, upper_bound in ipairs(SAO.Constants.PROFILER_HISTOGRAM_MS) do
        if elapsed_ms <= upper_bound then
            bucket = index

            break
        end
    end

    metrics.histogram[bucket] = metrics.histogram[bucket] + 1
end

local function sorted_values(source, comparator)
    local values = {}

    for _, value in pairs(source) do
        values[#values + 1] = value
    end

    table.sort(values, comparator)

    return values
end

local function exported_models()
    local models = {}

    for _, record in ipairs(sorted_values(state.models_by_path, function(left, right) return left.path < right.path end)) do
        local skins = {}

        for value in pairs(record.skins_by_value) do
            skins[#skins + 1] = value
        end

        table.sort(skins)
        local materials = {}

        for path in pairs(record.materials_by_path) do
            materials[#materials + 1] = path
        end

        table.sort(materials)
        models[#models + 1] = {
            id = record.id,
            path = record.path,
            evidence = record.evidence,
            skins = skins,
            bodygroups = sorted_values(record.bodygroups_by_key, function(left, right)
                if left.name == right.name then return left.value < right.value end

                return left.name < right.name
            end),
            submaterials = sorted_values(record.submaterials_by_key, function(left, right)
                if left.slot == right.slot then return left.material < right.material end

                return left.slot < right.slot
            end),
            materials = materials
        }
    end

    return models
end

local function percentile_95(metrics)
    if metrics.samples == 0 then return 0 end
    local target = math.ceil(metrics.samples * 0.95)
    local cumulative = 0

    for index, count in ipairs(metrics.histogram) do
        cumulative = cumulative + count

        if cumulative >= target then
            return SAO.Constants.PROFILER_HISTOGRAM_MS[index] or metrics.max_ms
        end
    end

    return metrics.max_ms
end

local function performance_summary()
    local metrics = state.metrics

    return {
        samples = metrics.samples,
        average_ms = metrics.samples > 0 and metrics.total_ms / metrics.samples or 0,
        p95_ms = percentile_95(metrics),
        max_ms = metrics.max_ms,
        queue_high_water = metrics.queue_high_water
    }
end

local function new_payload(performance)
    return {
        models = {},
        materials = {},
        lua_errors = {},
        performance = performance,
        truncation = state.truncation
    }
end

local function build_payloads()
    local payloads = {}
    local performance = performance_summary()
    local current = new_payload(performance)
    local accepted_bytes = 0
    local accepting = true

    local function encoded_size(payload)
        local raw = util.TableToJSON(payload, false)

        return raw and #raw or SAO.Constants.MAX_PROFILE_PAYLOAD_BYTES + 1
    end

    local function finish_current()
        if #current.models == 0 and #current.materials == 0 and #current.lua_errors == 0 and #payloads > 0 then return true end
        local bytes = encoded_size(current)

        if accepted_bytes + bytes > SAO.Constants.MAX_BUNDLE_BYTES - SAO.Constants.MAX_PROFILE_PAYLOAD_BYTES then
            accepting = false

            return false
        end

        payloads[#payloads + 1] = current
        accepted_bytes = accepted_bytes + bytes
        current = new_payload(performance)

        return true
    end

    local function add_item(category, item)
        if not accepting then
            state.truncation.bytes = saturating_increment(state.truncation.bytes, encoded_size(item))

            return
        end

        local target = current[category]
        target[#target + 1] = item

        if encoded_size(current) <= SAO.Constants.MAX_PROFILE_PAYLOAD_BYTES then return end
        target[#target] = nil

        if not finish_current() then
            state.truncation.bytes = saturating_increment(state.truncation.bytes, encoded_size(item))

            return
        end

        current[category][1] = item

        if encoded_size(current) > SAO.Constants.MAX_PROFILE_PAYLOAD_BYTES then
            current[category][1] = nil
            state.truncation.bytes = saturating_increment(state.truncation.bytes, encoded_size(item))
        end
    end

    for _, model in ipairs(exported_models()) do
        add_item("models", model)
    end

    for _, material in ipairs(sorted_values(state.materials_by_path, function(left, right) return left.path < right.path end)) do
        add_item("materials", material)
    end

    for _, lua_error in ipairs(sorted_values(state.lua_errors_by_id, function(left, right) return left.id < right.id end)) do
        add_item("lua_errors", lua_error)
    end

    finish_current()

    return payloads
end

local function sanitized_title(value)
    if not isstring(value) then return "" end
    value = string.gsub(value, "[%c]", " ")

    return string.sub(string.Trim(value), 1, 256)
end

local function mounted_addons()
    local result = {}
    local ok, addons = pcall(function() return engine.GetAddons() end)
    if not ok or not istable(addons) then return result end

    for _, addon in ipairs(addons) do
        local workshop_id = tostring(addon.wsid or "")

        if addon.mounted and string.match(workshop_id, "^[0-9]+$") and #workshop_id <= 20 and #result < SAO.Constants.MAX_MODELS then
            result[#result + 1] = {
                workshop_id = workshop_id,
                title = sanitized_title(addon.title)
            }
        end
    end

    table.sort(result, function(left, right) return left.workshop_id < right.workshop_id end)

    return result
end

local function environment_summary()
    local duration = math.Clamp(SysTime() - state.started_at, 0, 604800)

    return {
        game_build = string.sub(tostring(VERSIONSTR or VERSION or BRANCH or "unknown"), 1, 64),
        map = string.sub(game.GetMap() or "", 1, 128),
        gamemode = string.sub(engine.ActiveGamemode() or "", 1, 128),
        realm = game.SinglePlayer() and "singleplayer" or "server",
        session_seconds = duration,
        mounted_addons = mounted_addons()
    }
end

local function new_run_id()
    local raw = string.lower(util.SHA256(tostring(SysTime()) .. ":" .. tostring(math.random())))

    return string.sub(raw, 1, 8) .. "-" .. string.sub(raw, 9, 12) .. "-" .. string.sub(raw, 13, 16) .. "-" .. string.sub(raw, 17, 20) .. "-" .. string.sub(raw, 21, 32)
end

local function record_registered_models()
    local registries = {list.Get("SpawnableEntities") or {}, list.Get("Vehicles") or {}}

    for _, registry in ipairs(registries) do
        for _, definition in pairs(registry) do
            local path = normalized_model_path(definition.Model)
            if path then promote_model(path, "registered") end
        end
    end

    for _, definition in ipairs(weapons.GetList() or {}) do
        local world_model = normalized_model_path(definition.WorldModel)
        local view_model = normalized_model_path(definition.ViewModel)
        if world_model then promote_model(world_model, "registered") end
        if view_model then promote_model(view_model, "registered") end
    end
end

local function send_identifier(ply, message_name, identifier)
    if not IsValid(ply) then return end
    net.Start(message_name)
    net.WriteUInt(#identifier, 6)
    net.WriteData(identifier, #identifier)
    net.Send(ply)
end

function SAO.ServerProfiler.IsActive()
    return state.active
end

function SAO.ServerProfiler.RecordLuaError(entry)
    if not state.active or not istable(entry) or not isstring(entry.id) then return end
    local existing = state.lua_errors_by_id[entry.id]

    if existing then
        existing.count = math.min(1000000, existing.count + 1)

        return
    end

    if state.lua_error_count >= SAO.Constants.MAX_LUA_ERRORS then
        state.truncation.lua_errors = saturating_increment(state.truncation.lua_errors)

        return
    end

    state.lua_errors_by_id[entry.id] = entry
    state.lua_error_count = state.lua_error_count + 1
end

function SAO.ServerProfiler.PromoteObservation(path, evidence)
    if not state.active or not SAO.Constants.EvidenceByName[evidence] then return false end
    path = normalized_model_path(path)
    if not path then return false end

    return promote_model(path, evidence) ~= nil
end

function SAO.ServerProfiler.Start(ply)
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end
    if state.active then return false, "a profiling session is already active" end
    local identifier, reason = SAO.Permissions.OpenSession(ply)
    if not identifier then return false, reason end
    state = {
        active = true,
        owner = ply,
        identifier = identifier,
        run_id = new_run_id(),
        created_utc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        started_at = SysTime(),
        models_by_path = {},
        materials_by_path = {},
        lua_errors_by_id = {},
        model_count = 0,
        material_count = 0,
        lua_error_count = 0,
        metrics = new_metrics(),
        truncation = {
            models = 0,
            materials = 0,
            lua_errors = 0,
            bytes = 0
        }
    }
    record_registered_models()
    queue_initial_inventory()
    send_identifier(ply, SAO.Constants.Net.BeginSession, identifier)

    return true, state.run_id
end

function SAO.ServerProfiler.StopAndExport(ply)
    if not state.active or state.owner ~= ply then return false, "no owned profiling session" end
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end
    local valid, reason = SAO.Permissions.ValidateSession(ply, state.identifier)
    if not valid then return false, reason end
    local payloads = build_payloads()
    local run_id = state.run_id
    local identifier = state.identifier
    local environment = environment_summary()
    local ok, write_reason = SAO.Storage.WriteBundle("profiles", "sao-runtime-profile-v1", run_id, state.created_utc, {
        name = "Source Addon Optimizer Companion",
        version = "1.0.0"
    }, environment, payloads)
    SAO.Permissions.CloseSession(ply, identifier)
    send_identifier(ply, SAO.Constants.Net.EndSession, identifier)
    state.active = false

    return ok, ok and run_id or write_reason
end


util.AddNetworkString(SAO.Constants.Net.BeginSession)
util.AddNetworkString(SAO.Constants.Net.EndSession)
util.AddNetworkString(SAO.Constants.Net.Observation)

net.Receive(SAO.Constants.Net.Observation, function(_, ply)
    if not SAO.Permissions.CanUse(ply) then return end
    if not SAO.Permissions.CheckRateLimit(ply, "profile_observation", SAO.Constants.OBSERVATION_SEND_INTERVAL) then return end
    local identifier_bytes = net.ReadUInt(6)
    if identifier_bytes == 0 or identifier_bytes > SAO.Constants.MAX_NET_IDENTIFIER_BYTES then
        SAO.Permissions.RecordSecurityEvent("profile_observation", "invalid session length")

        return
    end

    local identifier = net.ReadData(identifier_bytes)
    local batch_count = net.ReadUInt(5)
    if batch_count == 0 or batch_count > SAO.Constants.MAX_OBSERVATIONS_PER_MESSAGE then
        SAO.Permissions.RecordSecurityEvent("profile_observation", "invalid observation count")

        return
    end

    local valid = SAO.Permissions.ValidateSession(ply, identifier)
    if not valid or not state.active or state.owner ~= ply then return end

    for _ = 1, batch_count do
        local path_bytes = net.ReadUInt(10)
        if path_bytes == 0 or path_bytes > SAO.Constants.MAX_PATH_BYTES then
            SAO.Permissions.RecordSecurityEvent("profile_observation", "invalid path length")

            return
        end

        local path = net.ReadData(path_bytes)
        local evidence = SAO.Constants.EvidenceByWire[net.ReadUInt(3)]
        if not evidence or not SAO.ServerProfiler.PromoteObservation(path, evidence) then
            SAO.Permissions.RecordSecurityEvent("profile_observation", "invalid observation")
        end
    end
end)

hook.Add("OnEntityCreated", "SAO.ProfileEntityCreated", function(ent)
    if not state.active then return end
    timer.Simple(0, function()
        if state.active then enqueue(reconcile_queue, ent) end
    end)
end)

timer.Create("SAO.ProfileReconciliation", SAO.Constants.PROFILER_RECONCILE_SECONDS, 0, function()
    if state.active then queue_reconciliation() end
end)

hook.Add("Think", "SAO.ProfileBudgetedWork", function()
    if not state.active then return end
    local started = SysTime()
    initial_cursor = process_queue(initial_queue, initial_cursor, SAO.Constants.PROFILER_INITIAL_BUDGET)
    reconcile_cursor = process_queue(reconcile_queue, reconcile_cursor, SAO.Constants.PROFILER_RECONCILE_BUDGET)

    if initial_cursor > #initial_queue then
        initial_queue = {}
        initial_cursor = 1
    end

    if reconcile_cursor > #reconcile_queue then
        reconcile_queue = {}
        reconcile_cursor = 1
    end

    update_queue_high_water()
    record_slice((SysTime() - started) * 1000)
end)

concommand.Add("sao_profile_start", function(ply)
    local ok, result = SAO.ServerProfiler.Start(ply)
    if not ok then MsgN("[SAO] Profiling did not start: " .. tostring(result)) end
end)

concommand.Add("sao_profile_stop", function(ply)
    local ok, result = SAO.ServerProfiler.StopAndExport(ply)
    if ok then
        MsgN("[SAO] Profile exported: " .. tostring(result))
    else
        MsgN("[SAO] Profile export failed: " .. tostring(result))
    end
end)
