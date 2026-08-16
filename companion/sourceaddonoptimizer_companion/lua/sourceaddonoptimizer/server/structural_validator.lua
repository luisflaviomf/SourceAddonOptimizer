SAO.StructuralValidator = SAO.StructuralValidator or {}

local state = {active = false}
local active_entities = {}
local progress_fields = {schema = true, run_id = true, plan_sha256 = true, cursor = true, completed_tests = true, resources = true, save_serial = true}
local resource_fields = {id = true, test_id = true, path = true, status = true, reason = true, warnings = true}

local function safe_text(value, maximum)
    if not isstring(value) then return "" end
    value = string.gsub(value, "[%c]", " ")
    value = string.gsub(value, "%a:[/\\][^%s]+", "<absolute-path>")
    value = string.gsub(value, "\\\\[^%s]+", "<absolute-path>")

    return string.sub(string.Trim(value), 1, maximum)
end

local function cleanup_entity(entity)
    if IsValid(entity) then entity:Remove() end
    active_entities[entity] = nil
end

local function cleanup_all_entities()
    for entity in pairs(active_entities) do
        cleanup_entity(entity)
    end
end

local function append_warning(warnings, message)
    if #warnings >= SAO.Constants.MAX_VALIDATION_WARNINGS then
        if state.truncation then state.truncation.warnings = math.min(1000000, state.truncation.warnings + 1) end

        return
    end

    warnings[#warnings + 1] = safe_text(message, 512)
end

local function resource(test, status, reason, warnings)
    return {
        id = test.id,
        test_id = test.id,
        path = test.path,
        status = status,
        reason = safe_text(reason, 1024),
        warnings = warnings or {}
    }
end

local function check_required_files(test)
    local missing = {}
    local total_bytes = 0

    for _, path in ipairs(test.required_files) do
        if not file.Exists(path, "GAME") then
            missing[#missing + 1] = path
        else
            local size = file.Size(path, "GAME")
            if isnumber(size) and size > 0 then total_bytes = total_bytes + size end
        end
    end

    return missing, total_bytes
end

local function size_warnings(test, actual_bytes)
    local warnings = {}
    if test.expected_bytes > 0 and actual_bytes ~= test.expected_bytes then
        append_warning(warnings, "mounted provider or stale output size differs from the declared optimized result")
    end

    return warnings
end

local function create_model_entity(test)
    if not util.IsValidModel(test.path) then return nil, "util.IsValidModel rejected the model" end
    local entity = ents.Create("prop_dynamic")
    if not IsValid(entity) then return nil, "temporary model entity could not be created" end
    active_entities[entity] = true
    entity:SetNoDraw(true)
    entity:SetNotSolid(true)
    entity:SetPos(Vector(0, 0, -16384))
    entity:SetModel(test.path)
    entity:Spawn()
    entity:Activate()
    if not IsValid(entity) or entity:GetModel() ~= test.path then return entity, "temporary model entity rejected the expected model" end

    return entity
end

local function finite_vector(value)
    if not isvector(value) then return false end

    for _, component in ipairs({value.x, value.y, value.z}) do
        if not isnumber(component) or component ~= component or component == math.huge or component == -math.huge then return false end
    end

    return true
end

local function normalized_entity_materials(entity)
    local result = {}
    local materials = entity:GetMaterials() or {}

    for index, raw in ipairs(materials) do
        if index > SAO.Constants.MAX_MATERIALS_PER_MODEL then break end
        if isstring(raw) and raw ~= "" then
            local path = string.lower(string.Replace(raw, "\\", "/"))
            if not string.StartWith(path, "materials/") then path = "materials/" .. path end
            if not string.EndsWith(path, ".vmt") then path = path .. ".vmt" end
            local normalized = SAO.Contract.NormalizeVirtualPath(path)
            if normalized then result[normalized] = true end
        end
    end

    return result, #materials
end

local function with_model_entity(test, operation)
    local entity
    local ok, result = xpcall(function()
        local create_reason
        entity, create_reason = create_model_entity(test)
        if create_reason then return resource(test, "FAILED", create_reason, {}) end

        return operation(entity)
    end, function(message)
        return resource(test, "FAILED", "validation exception: " .. safe_text(tostring(message), 900), {})
    end)
    cleanup_entity(entity)
    if not ok then return result end

    return result
end

local function validate_model_structure(test)
    local missing, actual_bytes = check_required_files(test)
    if #missing > 0 then return resource(test, "FAILED", "required model file is missing: " .. missing[1], {}) end
    local warnings = size_warnings(test, actual_bytes)

    return with_model_entity(test, function(entity)
        local bone_count = entity:GetBoneCount()
        if not isnumber(bone_count) or bone_count < 0 or bone_count > 65535 then append_warning(warnings, "model bone metadata is unavailable or implausible") end
        local mins, maxs = entity:GetModelBounds()
        if not finite_vector(mins) or not finite_vector(maxs) then append_warning(warnings, "model bounds are unavailable or implausible") end
        local resolved, material_count = normalized_entity_materials(entity)
        if material_count > SAO.Constants.MAX_MATERIALS_PER_MODEL then append_warning(warnings, "model material list exceeded its validation cap") end
        for _, material in ipairs(test.materials) do
            if not resolved[material] then append_warning(warnings, "declared material was not exposed by the active model state: " .. material) end
        end

        return resource(test, #warnings > 0 and "WARNING" or "PASS", #warnings > 0 and "model loaded with bounded warnings" or "required DX90 model family loaded", warnings)
    end)
end

local function find_bodygroup(entity, name)
    local groups = entity:GetBodyGroups() or {}
    for index, group in ipairs(groups) do
        if index > SAO.Constants.MAX_BODYGROUP_STATES_PER_MODEL then return nil end
        if isstring(group.name) and string.lower(group.name) == string.lower(name) then return group end
    end

    return nil
end

local function validate_model_state(test)
    if not file.Exists(test.path, "GAME") then return resource(test, "FAILED", "model state target is missing", {}) end

    return with_model_entity(test, function(entity)
        local warnings = {}
        local skin_count = entity:SkinCount()
        for _, skin in ipairs(test.skins) do
            if isnumber(skin_count) and skin_count > 0 and skin >= skin_count then return resource(test, "FAILED", "declared skin is outside the model range", warnings) end
            entity:SetSkin(skin)
            if entity:GetSkin() ~= skin then return resource(test, "FAILED", "declared skin could not be selected", warnings) end
        end

        for _, bodygroup in ipairs(test.bodygroups) do
            local group = find_bodygroup(entity, bodygroup.name)
            if not group then return resource(test, "FAILED", "declared bodygroup is missing: " .. bodygroup.name, warnings) end
            if not isnumber(group.id) or not isnumber(group.num) or bodygroup.value >= group.num then return resource(test, "FAILED", "declared bodygroup value is outside the model range", warnings) end
            entity:SetBodygroup(group.id, bodygroup.value)
            if entity:GetBodygroup(group.id) ~= bodygroup.value then return resource(test, "FAILED", "declared bodygroup state could not be selected", warnings) end
        end

        local _, material_count = normalized_entity_materials(entity)
        if material_count > SAO.Constants.MAX_MATERIALS_PER_MODEL then append_warning(warnings, "model materials were truncated at the validation cap") end

        return resource(test, #warnings > 0 and "WARNING" or "PASS", "declared model states were selectable", warnings)
    end)
end

local function validate_material(test)
    local missing, actual_bytes = check_required_files(test)
    if #missing > 0 then return resource(test, "FAILED", "required material or texture is missing: " .. missing[1], {}) end
    local warnings = size_warnings(test, actual_bytes)

    return resource(test, #warnings > 0 and "WARNING" or "PASS", "declared VMT/VTF dependencies resolve", warnings)
end

local function pending_visual(test)
    return resource(test, "WARNING", "visual client missing; render test is pending client validation", {})
end

local dispatch = {
    ["model_structure"] = validate_model_structure,
    ["model_state"] = validate_model_state,
    ["material_resolve"] = validate_material,
    ["model_render"] = pending_visual
}

local function progress_root(run_id)
    return SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.validation_progress .. "/" .. run_id
end

local function validate_saved_resource(value)
    local ok = SAO.Contract.ValidateClosedKeys(value, resource_fields)
    if not ok or not isstring(value.id) or #value.id > 64 or not isstring(value.test_id) or #value.test_id > 64 or not SAO.Validation.Statuses[value.status] or not isstring(value.reason) or #value.reason > 1024 or not istable(value.warnings) or #value.warnings > SAO.Constants.MAX_VALIDATION_WARNINGS then return false end
    local path = SAO.Contract.NormalizeVirtualPath(value.path)
    if not path then return false end
    for _, warning in ipairs(value.warnings) do
        if not isstring(warning) or #warning > 512 then return false end
    end
    value.path = path

    return true
end

local function latest_progress(run_id, plan_sha256)
    local root = progress_root(run_id)
    local files = file.Find(root .. "/structural-progress-*.json", "DATA")
    table.sort(files or {}, function(left, right) return left > right end)

    for _, filename in ipairs(files or {}) do
        if string.match(filename, "^structural%-progress%-%d%d%d%d%.json$") then
            local raw = file.Read(root .. "/" .. filename, "DATA")
            local saved = raw and SAO.Contract.DecodeJSON(raw, SAO.Constants.MAX_BUNDLE_BYTES) or nil
            local closed = saved and SAO.Contract.ValidateClosedKeys(saved, progress_fields) or false
            if closed and saved.schema == "sao-structural-progress-v1" and saved.run_id == run_id and saved.plan_sha256 == plan_sha256 and isnumber(saved.cursor) and saved.cursor == math.floor(saved.cursor) and saved.cursor >= 1 and istable(saved.resources) and #saved.resources <= SAO.Constants.MAX_VALIDATION_TESTS then
                local valid = true
                for _, value in ipairs(saved.resources) do
                    if not validate_saved_resource(value) then valid = false break end
                end
                if valid then return saved end
            end
        end
    end

    return nil
end

local function save_progress(reason)
    if not state.active then return false end
    state.save_serial = state.save_serial + 1
    if state.save_serial > SAO.Constants.MAX_VALIDATION_PROGRESS_FILES then
        state.truncation.resources = math.min(1000000, state.truncation.resources + 1)

        return false
    end
    local root = progress_root(state.run_id)
    file.CreateDir(root)
    local raw = SAO.Contract.EncodeJSON({
        schema = "sao-structural-progress-v1",
        run_id = state.run_id,
        plan_sha256 = state.plan_sha256,
        cursor = state.cursor,
        completed_tests = state.completed_tests,
        resources = state.resources,
        save_serial = state.save_serial
    })
    if not raw or #raw > SAO.Constants.MAX_BUNDLE_BYTES then return false end
    local filename = string.format("structural-progress-%04d.json", state.save_serial)
    local path = root .. "/" .. filename
    local partial = path .. ".partial.json"
    local handle = file.Open(partial, "wb", "DATA")
    if not handle then return false end
    handle:Write(raw)
    handle:Flush()
    handle:Close()
    if file.Size(partial, "DATA") ~= #raw or not file.Rename(partial, path) then return false end
    state.last_progress_reason = safe_text(reason, 128)

    return true
end

local function append_lua_errors()
    local identifiers = table.GetKeys(state.lua_errors_by_id)
    table.sort(identifiers)
    for _, identifier in ipairs(identifiers) do
        if #state.resources >= SAO.Constants.MAX_VALIDATION_TESTS then
            state.truncation.resources = state.truncation.resources + 1
        else
            local entry = state.lua_errors_by_id[identifier]
            state.resources[#state.resources + 1] = {
                id = "lua-error-" .. string.sub(identifier, 1, 54),
                test_id = "lua-error",
                path = "models/error.mdl",
                status = "WARNING",
                reason = safe_text("Lua error observed during validation: " .. entry.message, 1024),
                warnings = entry.source ~= "" and {safe_text(entry.source, 512)} or {}
            }
        end
    end
end

local function finish(interrupted)
    cleanup_all_entities()
    append_lua_errors()
    local ok, result = SAO.ServerResultWriter.WriteStructural(nil, state, interrupted)
    state.active = false

    return ok, result
end

function SAO.StructuralValidator.IsActive()
    return state.active
end

function SAO.StructuralValidator.RecordLuaError(entry)
    if not state.active or not istable(entry) or not isstring(entry.id) then return end
    if state.lua_errors_by_id[entry.id] then return end
    if table.Count(state.lua_errors_by_id) >= SAO.Constants.MAX_LUA_ERRORS then
        state.truncation.warnings = math.min(1000000, state.truncation.warnings + 1)

        return
    end
    state.lua_errors_by_id[entry.id] = {
        message = safe_text(entry.message, 1024),
        source = safe_text(entry.source, 512)
    }
end

function SAO.StructuralValidator.Start(ply, run_id)
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end
    if state.active then return false, "structural validation is already active" end
    if not isstring(run_id) or run_id == "" then
        local plans = SAO.Validation.FindPlanRunIds()
        run_id = plans[1]
    end
    if not run_id then return false, "no validation plan is available" end
    local plan, reason = SAO.Validation.ReadPlan(run_id)
    if not plan then return false, reason end
    local saved = latest_progress(run_id, plan.plan_sha256)
    state = {
        active = true,
        owner = ply,
        run_id = plan.run_id,
        plan_sha256 = plan.plan_sha256,
        tests = plan.tests,
        cursor = saved and saved.cursor or 1,
        completed_tests = saved and saved.completed_tests or 0,
        resources = saved and saved.resources or {},
        save_serial = saved and saved.save_serial or 0,
        lua_errors_by_id = {},
        started_at = SysTime(),
        truncation = {resources = 0, warnings = 0}
    }

    return true, run_id
end

function SAO.StructuralValidator.Cancel(reason)
    if not state.active then return false, "no structural validation is active" end
    save_progress(reason or "cancelled")
    cleanup_all_entities()

    return finish(true)
end

hook.Add("Think", "SAO.StructuralValidationQueue", function()
    if not state.active then return end

    for _ = 1, SAO.Constants.VALIDATION_TESTS_PER_TICK do
        local test = state.tests[state.cursor]
        if not test then
            save_progress("completed")
            local ok, result = finish(false)
            if ok then MsgN("[SAO] Structural validation result written: " .. tostring(result)) else MsgN("[SAO] Structural result write failed: " .. tostring(result)) end

            return
        end
        local handler = dispatch[test.kind]
        if not handler then
            SAO.StructuralValidator.Cancel("unknown test kind")

            return
        end
        local ok, result = xpcall(function() return handler(test) end, function(message)
            return resource(test, "FAILED", "validation dispatch exception: " .. safe_text(tostring(message), 900), {})
        end)
        cleanup_all_entities()
        if not ok or not istable(result) then result = resource(test, "FAILED", "validation handler failed", {}) end
        state.resources[#state.resources + 1] = result
        state.cursor = state.cursor + 1
        state.completed_tests = state.completed_tests + 1
        if state.completed_tests % SAO.Constants.VALIDATION_PROGRESS_INTERVAL == 0 then save_progress("periodic") end
    end
end)

hook.Add("PreCleanupMap", "SAO.StructuralValidationMapCleanup", function()
    if state.active then SAO.StructuralValidator.Cancel("map change") end
end)

hook.Add("ShutDown", "SAO.StructuralValidationShutdown", function()
    if state.active then SAO.StructuralValidator.Cancel("shutdown") end
end)

hook.Add("PlayerDisconnected", "SAO.StructuralValidationDisconnect", function(ply)
    if state.active and state.owner == ply then SAO.StructuralValidator.Cancel("owner disconnected") end
end)

concommand.Add("sao_validate_start", function(ply, _, arguments)
    if not SAO.Permissions.CanUse(ply) then return end
    local ok, result = SAO.StructuralValidator.Start(ply, arguments and arguments[1] or nil)
    if ok then MsgN("[SAO] Structural validation started: " .. tostring(result)) else MsgN("[SAO] Structural validation did not start: " .. tostring(result)) end
end)

concommand.Add("sao_validate_cancel", function(ply)
    if not SAO.Permissions.CanUse(ply) then return end
    local ok, result = SAO.StructuralValidator.Cancel("user cancelled")
    if not ok then MsgN("[SAO] Structural validation cancel failed: " .. tostring(result)) end
end)
